"""Sorgente dati xDrip+ (servizio web locale sul telefono, compatibile con il
formato Nightscout: vedi Documentation/technical/Local_Web_Services.md del
progetto xDrip+). Nessun bolo/basale: xDrip+ e' un follower CGM, non ha dati
del microinfusore. Limite reale (non un bug): /sgv.json restituisce le ultime
N letture piu' recenti, non un intervallo di date come Nightscout, quindi e'
utile solo per sincronizzare il periodo recente, non per un backfill storico.
Non testato contro un dispositivo reale: implementato dalla documentazione
ufficiale, con test unitari su risposte finte."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from .datasource import DataSource, DataSourceError
from .timeutil import DEFAULT_TZ, fmt_local


class XDripDataSource(DataSource):
    kind = "xdrip"

    def __init__(self, base_url: str, tz: str = DEFAULT_TZ, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.tz = ZoneInfo(tz)
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None):
        try:
            resp = requests.get(f"{self.base_url}{path}", params=params or {}, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise DataSourceError(f"xDrip+ non raggiungibile: {exc}") from exc
        except ValueError as exc:
            raise DataSourceError(f"Risposta di xDrip+ non valida: {exc}") from exc

    def fetch_glucose(self, start: datetime, end: datetime) -> list[dict]:
        minuti = max(1, int((end - start).total_seconds() / 60))
        count = min(10000, minuti + 10)  # margine per non perdere letture ai bordi
        rows = self._get("/sgv.json", {"count": count})
        out = []
        for r in rows or []:
            if r.get("sgv") is None or r.get("date") is None:
                continue
            t = datetime.fromtimestamp(r["date"] / 1000, tz=timezone.utc).astimezone(self.tz).replace(tzinfo=None)
            if not (start <= t < end):
                continue
            out.append({
                "ts_local": fmt_local(t),
                "ts_utc": t.replace(tzinfo=self.tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mgdl": int(r["sgv"]),
            })
        return out

    def status(self) -> dict:
        try:
            data = self._get("/status.json")
            return {"ok": bool(data), "errore": None}
        except DataSourceError as exc:
            return {"ok": False, "errore": str(exc)}
