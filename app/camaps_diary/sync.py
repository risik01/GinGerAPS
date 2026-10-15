"""Sincronizzazione Glooko -> database locale, e import di un export di glooko_probe."""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .datasource import DataSource
from .glooko import BASAL_PATH, BOLUS_PATH, FIRST_GUID, GlookoClient
from .normalize import basal_row, bolus_row, profile_settings, v3_rows
from .store import Store
from .timeutil import DEFAULT_TZ, now_local, parse_local

# CamAPS carica su Glooko a intervalli di ore: a ogni sync si riscaricano
# le ultime 24 ore di glicemie per raccogliere i dati arrivati in ritardo.
CGM_LOOKBACK_H = 24


def _v2_types(store: Store):
    return (
        ("boli", BOLUS_PATH, "normalBoluses", bolus_row, store.upsert_bolus),
        ("basali", BASAL_PATH, "scheduledBasals", basal_row, store.upsert_basal),
    )


def sync(client: GlookoClient, store: Store, backfill_days: int = 14, log=print) -> dict:
    for key, value in profile_settings(client.profile).items():
        store.set(key, value)
    tz = store.get("timezone", DEFAULT_TZ)
    report = {}

    # Boli e basali: incrementali con il cursore salvato all'ultima sync
    since = (datetime.now(timezone.utc) - timedelta(days=backfill_days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    for name, path, list_key, convert, upsert in _v2_types(store):
        cur_ts = store.get(f"cursore_{name}_ts") or since
        cur_guid = store.get(f"cursore_{name}_guid") or FIRST_GUID
        n = 0
        for items, (ts, guid) in client.iter_v2(path, list_key, cur_ts, cur_guid):
            n += upsert([convert(r, tz) for r in items if r.get("guid") and r.get("pumpTimestamp")])
            store.set(f"cursore_{name}_ts", ts)
            store.set(f"cursore_{name}_guid", guid)
        report[name] = n
        log(f"-> {name}: {n} record nuovi o aggiornati")

    # Glicemie, modalita' CamAPS ed eventi dal grafico v3, a finestre di 24 ore
    now = now_local(tz)
    start = now - timedelta(days=backfill_days)
    last = store.last_glucose_local()
    if last:
        start = max(start, parse_local(last) - timedelta(hours=CGM_LOOKBACK_H))
    n_g = n_m = 0
    t = start
    while t < now:
        end = min(t + timedelta(days=1), now)
        glucose, modes, events = v3_rows(client.v3_window(t, end), tz)
        n_g += store.upsert_glucose(glucose)
        n_m += store.upsert_modes(modes)
        store.upsert_events(events)
        t = end
        if t < now:
            time.sleep(client.pause_s)
    report["glicemie"] = n_g
    report["modalita"] = n_m
    log(f"-> glicemie: {n_g} letture scaricate (dal {start:%d/%m %H:%M})")
    store.set("ultima_sync_utc", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return report


def import_raw(export_dir: str | Path, store: Store, log=print) -> dict:
    """Importa la cartella creata da glooko_probe.py (sottocartella raw/)."""
    base = Path(export_dir)
    raw = base / "raw" if (base / "raw").is_dir() else base

    def load(name):
        p = raw / f"{name}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    profile = load("profile")
    if profile:
        for key, value in profile_settings(profile).items():
            store.set(key, value)
    tz = store.get("timezone", DEFAULT_TZ)
    report = {}
    for name, list_key, convert, upsert in (
        ("normal_boluses", "normalBoluses", bolus_row, store.upsert_bolus),
        ("scheduled_basals", "scheduledBasals", basal_row, store.upsert_basal),
    ):
        rows = {}
        for page in load(name) or []:
            for r in page.get(list_key) or []:
                if r.get("guid") and r.get("pumpTimestamp"):
                    rows[r["guid"]] = convert(r, tz)
        report[name] = upsert(list(rows.values()))
    n_g = n_m = 0
    for page in load("v3_graph") or []:
        glucose, modes, events = v3_rows(page, tz)
        n_g += store.upsert_glucose(glucose)
        n_m += store.upsert_modes(modes)
        store.upsert_events(events)
    report["glicemie"] = n_g
    report["modalita"] = n_m
    for k, v in report.items():
        log(f"-> {k}: {v}")
    return report


def sync_generic(source: DataSource, store: Store, backfill_days: int = 14, log=print) -> dict:
    """Sincronizzazione per sorgenti diverse da Glooko (Nightscout, xDrip+):
    fetch a intervallo diretto, senza il cursore incrementale per GUID usato
    per Glooko (piu' semplice, adatto a sorgenti che espongono solo query per
    intervallo di date)."""
    tz = store.get("timezone", DEFAULT_TZ)
    now = now_local(tz)
    start = now - timedelta(days=backfill_days)
    last = store.last_glucose_local()
    if last:
        start = max(start, parse_local(last) - timedelta(hours=CGM_LOOKBACK_H))
    n_g = store.upsert_glucose(source.fetch_glucose(start, now))
    n_b = store.upsert_bolus(source.fetch_boluses(start, now))
    n_ba = store.upsert_basal(source.fetch_basals(start, now))
    report = {"glicemie": n_g, "boli": n_b, "basali": n_ba}
    log(f"-> {source.kind}: glicemie {n_g}, boli {n_b}, basali {n_ba}")
    store.set("ultima_sync_utc", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return report
