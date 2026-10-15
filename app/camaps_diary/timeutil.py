"""Gestione orari.

Glooko memorizza gli orari di CamAPS come ORA LOCALE del telefono, ma li scrive
con il suffisso "Z" come se fossero UTC (es. un bolo delle 13:04 a Roma arriva
come "13:04:29.000Z"). Verificato sull'export reale: alcuni boli risultano
"successivi" al proprio syncTimestamp (che e' UTC vero), cosa possibile solo
se l'orario del bolo e' locale.

Quindi: leggiamo quegli orari come ora locale "nuda" e calcoliamo l'UTC reale
con il fuso del profilo (Europe/Rome), che gestisce da solo l'ora legale.
Nell'ora ripetuta del cambio di fine ottobre (02:00-03:00) si assume la prima
occorrenza: e' l'unica ambiguita' possibile.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Europe/Rome"
LOCAL_FMT = "%Y-%m-%d %H:%M:%S"


def parse_glooko_local(value: str) -> datetime:
    """'2026-10-12T13:04:29.000Z' -> datetime(2026, 10, 12, 13, 4, 29) senza fuso."""
    return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")


def to_glooko_local(dt_local: datetime) -> str:
    """Ora locale nuda -> formato atteso da Glooko per le finestre v3."""
    return dt_local.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def local_to_utc(dt_local: datetime, tz: str = DEFAULT_TZ) -> datetime:
    return dt_local.replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


def utc_iso(dt_local: datetime, tz: str = DEFAULT_TZ) -> str:
    return local_to_utc(dt_local, tz).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_local(dt_local: datetime) -> str:
    return dt_local.strftime(LOCAL_FMT)


def parse_local(value: str) -> datetime:
    return datetime.strptime(value, LOCAL_FMT)


def now_local(tz: str = DEFAULT_TZ) -> datetime:
    return datetime.now(ZoneInfo(tz)).replace(tzinfo=None, microsecond=0)


def split_by_day(start: datetime, seconds: float):
    """Divide un intervallo [start, start+seconds) sui giorni di calendario.
    Restituisce {data: secondi_in_quel_giorno}."""
    out = {}
    t, end = start, start + timedelta(seconds=seconds)
    while t < end:
        midnight = datetime(t.year, t.month, t.day) + timedelta(days=1)
        nxt = min(end, midnight)
        out[t.date()] = out.get(t.date(), 0.0) + (nxt - t).total_seconds()
        t = nxt
    return out
