"""Client per l'API (non ufficiale) di Glooko, dove CamAPS FX carica i dati.

Cosa e' emerso dall'export reale:
- boli e basali (v2) si scaricano in modo INCREMENTALE per data di
  aggiornamento: la risposta contiene lastPage / lastUpdatedAt / lastGuid,
  che fanno da cursore per la pagina successiva e per la sync dopo;
- le glicemie del sensore NON arrivano dall'endpoint v2 cgm/readings
  (vuoto con CamAPS) ma dal grafico v3, a risoluzione di 1 minuto;
- nel grafico v3 ogni modalita' CamAPS (auto, boost, ease-off, ...) e'
  ripetuta 5 volte: va deduplicata;
- l'endpoint delle impostazioni del microinfusore risponde 401: non usato.
"""

import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .timeutil import to_glooko_local

SERVERS = {
    "eu": ("https://eu.api.glooko.com", "https://eu.my.glooko.com"),
    "us": ("https://api.glooko.com", "https://my.glooko.com"),
    "ca": ("https://ca.api.glooko.com", "https://ca.my.glooko.com"),
}

LOGIN_PATH = "/api/v2/users/sign_in"
PROFILE_PATH = "/api/v3/session/users"
BOLUS_PATH = "/api/v2/pumps/normal_boluses"
BASAL_PATH = "/api/v2/pumps/scheduled_basals"
V3_GRAPH_PATH = "/api/v3/graph/data"

# GUID iniziale fisso usato dal client ufficiale per la prima pagina
FIRST_GUID = "1e0c094e-1e54-4a4f-8e6a-f94484b53789"

CGM_SERIES = ["cgmLow", "cgmNormal", "cgmHigh"]
MODE_SERIES = [
    "pumpCamapsAutomaticMode", "pumpCamapsManualMode", "pumpCamapsBoostMode",
    "pumpCamapsEaseOffMode", "pumpCamapsNoCgmMode",
    "pumpCamapsNoPumpConnectivityMode", "pumpCamapsPumpDeliverySuspendedMode",
    "pumpCamapsUnableToProceedMode", "pumpCamapsBluetoothTurnedOffMode",
    "pumpCamapsDailyTotalInsulinExceededMode", "pumpCamapsDepoweredMode",
    "pumpCamapsExtendedBolusNotAllowedMode",
]
EVENT_SERIES = ["reservoirChange", "setSiteChange"]
V3_SERIES = CGM_SERIES + MODE_SERIES + EVENT_SERIES


class GlookoError(Exception):
    pass


class GlookoClient:
    def __init__(self, server: str = "eu", device_file: Path | None = None,
                 api_base: str | None = None, web_origin: str | None = None,
                 verbose: bool = False, pause_s: float = 0.4):
        base, origin = SERVERS[server]
        self.api_base = (api_base or base).rstrip("/")
        self.web_origin = (web_origin or origin).rstrip("/")
        self.device_file = device_file
        self.verbose = verbose
        self.pause_s = pause_s
        self.patient_code = None
        self.profile = {}
        self.http = requests.Session()
        self.http.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-GB,en;q=0.9",
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                           "Version/16.5 Safari/605.1.15"),
            "Referer": self.web_origin + "/",
            "Origin": self.web_origin,
        })

    # -- utilita' ------------------------------------------------------------

    def _log(self, msg):
        if self.verbose:
            print("   [glooko]", msg)

    def _device_identity(self) -> dict:
        """Sempre lo stesso 'telefono' finto, per non sembrare un dispositivo
        nuovo a ogni login."""
        if self.device_file and self.device_file.exists():
            try:
                return json.loads(self.device_file.read_text())
            except (OSError, ValueError):
                pass
        ident = {"deviceId": secrets.token_hex(8), "serialNumber": secrets.token_hex(12)}
        if self.device_file:
            try:
                self.device_file.parent.mkdir(parents=True, exist_ok=True)
                self.device_file.write_text(json.dumps(ident))
            except OSError:
                pass
        return ident

    def get(self, path: str, params=None) -> dict:
        url = self.api_base + path
        for attempt in range(3):
            try:
                resp = self.http.get(url, params=params, timeout=60)
            except requests.RequestException as exc:
                if attempt == 2:
                    raise GlookoError(f"Errore di rete su {path}: {exc}") from exc
                time.sleep(2 * (attempt + 1))
                continue
            self._log(f"GET {path} -> HTTP {resp.status_code}")
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code == 401:
                raise GlookoError(f"{path}: sessione non valida (HTTP 401)")
            if resp.status_code >= 400:
                raise GlookoError(f"{path}: HTTP {resp.status_code} - {resp.text[:200]}")
            return resp.json() if resp.content else {}
        raise GlookoError(f"{path}: troppi tentativi falliti")

    # -- login ---------------------------------------------------------------

    def login(self, email: str, password: str) -> None:
        ident = self._device_identity()
        payload = {
            "userLogin": {"email": email, "password": password},
            "deviceInformation": {
                "applicationType": "logbook", "os": "android", "osVersion": "33",
                "device": "Google Pixel 4a", "deviceManufacturer": "Google",
                "deviceModel": "Pixel 4a", "serialNumber": ident["serialNumber"],
                "clinicalResearch": False, "deviceId": ident["deviceId"],
                "applicationVersion": "6.1.3", "buildNumber": "0",
                "gitHash": "g4fbed2011b",
            },
        }
        try:
            resp = self.http.post(self.api_base + LOGIN_PATH, json=payload, timeout=30)
        except requests.RequestException as exc:
            raise GlookoError(f"Server Glooko non raggiungibile: {exc}") from exc
        self._log(f"login HTTP {resp.status_code}")
        if resp.status_code in (401, 403):
            raise GlookoError(f"Credenziali Glooko rifiutate (HTTP {resp.status_code})")
        if resp.status_code == 422:
            raise GlookoError("Login rifiutato (HTTP 422): forse serve il login web o la "
                              "verifica in due passaggi")
        if resp.status_code >= 400:
            raise GlookoError(f"Login fallito: HTTP {resp.status_code} - {resp.text[:200]}")
        data = resp.json() if resp.content else {}
        if any(data.get(k) for k in ("twoFaRequired", "two_fa_required", "twoFactorRequired")):
            raise GlookoError("Verifica in due passaggi attiva: non ancora gestita")
        if not self.http.cookies:
            raise GlookoError("Login senza cookie di sessione: risposta inattesa")

        self.patient_code = ((data.get("userLogin") or {}).get("glookoCode")
                             or (data.get("user") or {}).get("glookoCode"))
        try:
            self.profile = self.get(PROFILE_PATH)
        except GlookoError:
            self.profile = {}
        if not self.patient_code:
            for k in ("currentUser", "currentPatient"):
                code = (self.profile.get(k) or {}).get("glookoCode")
                if code:
                    self.patient_code = code
                    break
        if not self.patient_code:
            raise GlookoError("Login riuscito ma codice paziente non trovato")

    # -- dati ----------------------------------------------------------------

    def iter_v2(self, path: str, list_key: str, updated_since_utc: str,
                last_guid: str = FIRST_GUID, limit: int = 1000, max_pages: int = 500):
        """Scarica a pagine tutti i record aggiornati dopo updated_since_utc.
        Restituisce (record, cursore) per ogni pagina; il cursore va salvato
        per la sync successiva."""
        cursor_ts, cursor_guid = updated_since_utc, last_guid
        end = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        for _ in range(max_pages):
            page = self.get(path, {
                "patient": self.patient_code,
                "startDate": cursor_ts,
                "endDate": end,
                "lastUpdatedAt": cursor_ts,
                "lastGuid": cursor_guid,
                "limit": limit,
            })
            items = page.get(list_key) or []
            new_cursor = (page.get("lastUpdatedAt") or cursor_ts, page.get("lastGuid") or cursor_guid)
            yield items, new_cursor
            if page.get("lastPage", True) or not items or new_cursor == (cursor_ts, cursor_guid):
                break
            cursor_ts, cursor_guid = new_cursor
            time.sleep(self.pause_s)

    def v3_window(self, start_local: datetime, end_local: datetime) -> dict:
        params = [
            ("patient", self.patient_code),
            ("startDate", to_glooko_local(start_local)),
            ("endDate", to_glooko_local(end_local)),
            ("locale", "en"),
            ("insulinTooltips", "false"),
            ("filterBgReadings", "false"),
            ("splitByDay", "false"),
        ] + [("series[]", s) for s in V3_SERIES]
        return self.get(V3_GRAPH_PATH, params)
