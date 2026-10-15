"""Dati sintetici per la modalita' demo: circa 30 giorni di glicemie, boli,
basale e pasti plausibili, per provare l'interfaccia senza un vero account
Glooko/Nightscout. Generati con un seed fisso: stessi dati a ogni avvio in
modalita' demo, cosi' non si accumulano a ogni riavvio dell'add-on."""

import math
import random
from datetime import datetime, time, timedelta

from .diary import save_meal
from .store import Store
from .timeutil import DEFAULT_TZ, fmt_local, local_to_utc, now_local

GIORNI_DEMO = 30
SEED = 20261015

PASTI_DEMO = (
    (8, 0, "colazione", [("Yogurt greco", 12.0), ("Pane integrale", 25.0)]),
    (13, 0, "pranzo", [("Pasta al pomodoro", 65.0), ("Insalata verde", 3.0)]),
    (20, 0, "cena", [("Petto di pollo", 1.0), ("Riso basmati", 45.0)]),
)


def _mgdl_at(minuti_dal_giorno: float, rnd: random.Random) -> float:
    """Curva giornaliera plausibile: base ~115 mg/dL con rialzi dopo i pasti."""
    base = 115 + 10 * math.sin(minuti_dal_giorno / 1440 * 2 * math.pi)
    for ora_pasto, _, _, voci in PASTI_DEMO:
        picco_min = ora_pasto * 60 + 60  # il picco arriva circa un'ora dopo
        carbo_tot = sum(c for _, c in voci)
        ampiezza = min(90, carbo_tot * 0.9)
        dist = minuti_dal_giorno - picco_min
        base += ampiezza * math.exp(-(dist ** 2) / (2 * 70 ** 2))
    return max(55, min(260, base + rnd.gauss(0, 6)))


def seed_demo_data(store: Store, tz: str = DEFAULT_TZ) -> None:
    rnd = random.Random(SEED)
    oggi = now_local(tz).date()
    primo_giorno = oggi - timedelta(days=GIORNI_DEMO - 1)

    glucose_rows = []
    basal_rows = []
    bolus_rows = []
    guid_n = 0

    d = primo_giorno
    while d <= oggi:
        inizio_giorno = datetime.combine(d, time())
        for m in range(0, 1440, 5):
            t = inizio_giorno + timedelta(minutes=m)
            if t > now_local(tz):
                break
            mgdl = round(_mgdl_at(m, rnd))
            ts_local = fmt_local(t)
            glucose_rows.append({
                "ts_local": ts_local,
                "ts_utc": local_to_utc(t, tz).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mgdl": mgdl,
            })

        # basale: diurna piu' alta, notturna piu' bassa, a blocchi di un'ora
        for h in range(24):
            t = inizio_giorno + timedelta(hours=h)
            if t > now_local(tz):
                break
            rate = 0.55 if h < 6 or h >= 23 else 0.85
            guid_n += 1
            basal_rows.append({
                "guid": f"demo-basal-{guid_n}",
                "ts_local": fmt_local(t),
                "ts_utc": local_to_utc(t, tz).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "duration_s": 3600,
                "rate": round(rate + rnd.gauss(0, 0.05), 2),
                "soft_deleted": 0,
            })

        for ora, minuto, tipo, voci in PASTI_DEMO:
            t = inizio_giorno + timedelta(hours=ora, minutes=minuto)
            if t > now_local(tz):
                continue
            carbo_tot = sum(c for _, c in voci)
            unita = round(carbo_tot / 10 + rnd.gauss(0, 0.3), 2)  # solo per dati plausibili, non una formula di dose
            guid_n += 1
            bolus_rows.append({
                "guid": f"demo-bolus-{guid_n}",
                "ts_local": fmt_local(t),
                "ts_utc": local_to_utc(t, tz).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "insulin_delivered": max(0.1, unita),
                "insulin_programmed": max(0.1, unita),
                "carbs_input": carbo_tot,
                "bolus_type": "suggested",
                "soft_deleted": 0,
            })
            save_meal(store, {
                "ora": t.strftime("%Y-%m-%dT%H:%M"), "tipo": tipo,
                "voci": [{"descrizione": nome, "carbo_g": c} for nome, c in voci],
            })
        d += timedelta(days=1)

    store.upsert_glucose(glucose_rows)
    store.upsert_basal(basal_rows)
    store.upsert_bolus(bolus_rows)
    store.set("demo_seminato", "1")
