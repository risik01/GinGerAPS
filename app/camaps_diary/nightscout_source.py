"""Sorgente dati Nightscout (API REST ufficiale e documentata).

Letture glicemia da /api/v1/entries.json, boli/carboidrati e basale temporanea
da /api/v1/treatments.json (eventType "Temp Basal" per la basale: non tutti i
sistemi AID la pubblicano su Nightscout, in quel caso resta vuota). Non
testato contro un server reale (nessuna istanza Nightscout disponibile in
questa installazione): implementato seguendo l'API ufficiale documentata,
con test unitari su risposte finte che riproducono la forma reale dei dati.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from .datasource import DataSource, DataSourceError
from .timeutil import DEFAULT_TZ, fmt_local


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class NightscoutDataSource(DataSource):
    kind = "nightscout"

    def __init__(self, base_url: str, token: str = "", tz: str = DEFAULT_TZ, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.tz_name = tz
        self.tz = ZoneInfo(tz)
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None):
        params = dict(params or {})
        if self.token:
            params["token"] = self.token
        try:
            resp = requests.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise DataSourceError(f"Nightscout non raggiungibile: {exc}") from exc
        except ValueError as exc:
            raise DataSourceError(f"Risposta di Nightscout non valida: {exc}") from exc

    def _to_local(self, epoch_ms: int) -> datetime:
        return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone(self.tz).replace(tzinfo=None)

    def _to_utc_iso(self, naive_local: datetime) -> str:
        """start/end dell'interfaccia DataSource sono sempre ora locale nuda
        (vedi timeutil.py): qui diventano UTC per interrogare Nightscout."""
        return naive_local.replace(tzinfo=self.tz).astimezone(timezone.utc).isoformat()

    def fetch_glucose(self, start: datetime, end: datetime) -> list[dict]:
        rows = self._get("/api/v1/entries.json", {
            "find[dateString][$gte]": self._to_utc_iso(start),
            "find[dateString][$lte]": self._to_utc_iso(end),
            "count": 100000,
        })
        out = []
        for r in rows or []:
            if r.get("type") != "sgv" or r.get("sgv") is None or r.get("date") is None:
                continue
            t = self._to_local(r["date"])
            out.append({
                "ts_local": fmt_local(t),
                "ts_utc": t.replace(tzinfo=self.tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mgdl": int(r["sgv"]),
            })
        return out

    def _treatments(self, start: datetime, end: datetime, extra: dict | None = None) -> list[dict]:
        params = {
            "find[created_at][$gte]": self._to_utc_iso(start),
            "find[created_at][$lte]": self._to_utc_iso(end),
            "count": 100000,
        }
        params.update(extra or {})
        return self._get("/api/v1/treatments.json", params) or []

    def fetch_boluses(self, start: datetime, end: datetime) -> list[dict]:
        out = []
        for r in self._treatments(start, end):
            insulin = r.get("insulin")
            created = r.get("created_at")
            if not insulin or not created or not r.get("_id"):
                continue
            t_utc = _parse_iso(created)
            t_local = t_utc.astimezone(self.tz).replace(tzinfo=None)
            out.append({
                "guid": r["_id"],
                "ts_local": fmt_local(t_local),
                "ts_utc": t_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "insulin_delivered": float(insulin),
                "carbs_input": float(r.get("carbs") or 0),
                "bolus_type": r.get("eventType") or "bolus",
                "soft_deleted": 0,
            })
        return out

    def fetch_basals(self, start: datetime, end: datetime) -> list[dict]:
        out = []
        for r in self._treatments(start, end, {"find[eventType]": "Temp Basal"}):
            rate, duration_min, created = r.get("rate"), r.get("duration"), r.get("created_at")
            if rate is None or not duration_min or not created or not r.get("_id"):
                continue
            t_utc = _parse_iso(created)
            t_local = t_utc.astimezone(self.tz).replace(tzinfo=None)
            out.append({
                "guid": r["_id"],
                "ts_local": fmt_local(t_local),
                "ts_utc": t_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "duration_s": int(duration_min) * 60,
                "rate": float(rate),
                "soft_deleted": 0,
            })
        return out

    def status(self) -> dict:
        try:
            data = self._get("/api/v1/status.json")
            return {"ok": bool(data), "errore": None}
        except DataSourceError as exc:
            return {"ok": False, "errore": str(exc)}
