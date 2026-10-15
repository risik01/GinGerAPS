"""Vista del giorno e analisi dei pasti.

Ogni voce CamAPS (vedi camaps_entries) viene collegata al pasto del diario
a cui appartiene:
- bolo con insulina: pasto tra 45 minuti prima e 30 minuti dopo il bolo;
- pasto a lenta digestione: pasto da cui partono le quote (fino a 4 ore prima);
- pasto o spuntino senza bolo: pasto tra 45 minuti prima e 30 minuti dopo;
- trattamento ipoglicemia: voce "Correzione ipo" del diario entro 30 minuti.
Per un pasto: carboidrati mangiati e dichiarati a CamAPS, insulina del bolo,
insulina automatica (3 ore, o fino a un'ora dopo l'ultima quota lenta),
glicemia prima, picco e a 2 ore. Sono numeri descrittivi, non dosi.
"""

import calendar as cal_mod
from datetime import date, datetime, time, timedelta

from .camaps_entries import GlucoseIndex, classify
from .diary import meals_between, notes_for_day
from .stats import daily_summary
from .store import Store
from .timeutil import fmt_local, now_local, parse_local

OCCURRENCE_BEFORE = timedelta(minutes=30)
OCCURRENCE_AFTER = timedelta(minutes=180)

BOLUS_BEFORE = timedelta(minutes=30)   # bolo fino a 30 min prima del pasto
BOLUS_AFTER = timedelta(minutes=45)    # ...o fino a 45 min dopo
SLOW_AFTER = timedelta(hours=4)
HYPO_NEAR = timedelta(minutes=30)
WINDOW = timedelta(hours=3)
CONTEXT = timedelta(hours=6)


def _minutes(t: datetime, day_start: datetime) -> float:
    return round((t - day_start).total_seconds() / 60, 1)


def _glucose(store: Store, start: datetime, end: datetime):
    return [(parse_local(r["ts_local"]), r["mgdl"]) for r in store.db.execute(
        "SELECT ts_local, mgdl FROM glucose WHERE ts_local >= ? AND ts_local < ? ORDER BY ts_local",
        (fmt_local(start), fmt_local(end)))]


def _bolus_rows(store: Store, start: datetime, end: datetime):
    return [dict(r) for r in store.db.execute(
        "SELECT guid, ts_local, insulin_delivered, carbs_input, bolus_type, rec_carbs, rec_correction "
        "FROM bolus WHERE soft_deleted = 0 AND ts_local >= ? AND ts_local < ? ORDER BY ts_local",
        (fmt_local(start), fmt_local(end)))]


def _basal_segments(store: Store, start: datetime, end: datetime):
    out = []
    for r in store.db.execute(
            "SELECT ts_local, duration_s, rate FROM basal WHERE soft_deleted = 0 "
            "AND ts_local >= ? AND ts_local < ? ORDER BY ts_local",
            (fmt_local(start - timedelta(days=1)), fmt_local(end))):
        a = parse_local(r["ts_local"])
        b = a + timedelta(seconds=r["duration_s"])
        if b > start and a < end:
            out.append((max(a, start), min(b, end), r["rate"]))
    return out


def _insulin_between(segments, start: datetime, end: datetime) -> float:
    total = 0.0
    for a, b, rate in segments:
        lo, hi = max(a, start), min(b, end)
        if hi > lo:
            total += rate * (hi - lo).total_seconds() / 3600
    return total


def _assign(meals: list, items: list) -> None:
    """Scrive item['_pasto'] = id del pasto del diario a cui appartiene la voce."""
    parsed = [(m, parse_local(m["ora"])) for m in meals]
    for it in items:
        best = None
        for m, tm in parsed:
            is_hypo_meal = m["tipo"] == "ipoglicemia"
            if it["tipo"] == "ipo":
                ok = is_hypo_meal and abs(it["t"] - tm) <= HYPO_NEAR
                dist = abs(it["t"] - tm)
            elif is_hypo_meal:
                continue
            elif it["tipo"] == "lento":
                ok = tm - BOLUS_BEFORE <= it["t"] <= tm + SLOW_AFTER
                dist = abs(it["t"] - tm)
            else:  # bolo, spuntino
                ok = tm - BOLUS_BEFORE <= it["t"] <= tm + BOLUS_AFTER
                dist = abs(it["t"] - tm)
            if ok and (best is None or dist < best[0]):
                best = (dist, m["id"])
        if best:
            it["_pasto"] = best[1]


def analyse_meal(meal: dict, items: list, gindex: GlucoseIndex, glucose: list, basal: list,
                 last_data) -> dict:
    t = parse_local(meal["ora"])
    if last_data is None or t > last_data:
        return {"in_attesa": True}
    mine = [it for it in items if it.get("_pasto") == meal["id"]]
    eaten = meal["carbo_g"] or 0

    if meal["tipo"] == "ipoglicemia":
        hypo = [it for it in mine if it["tipo"] == "ipo"]
        lows = [v for pt, v in glucose if t - timedelta(minutes=30) <= pt <= t + timedelta(minutes=30)]
        return {
            "in_attesa": False, "ipo": True,
            "carbo_ipo_camaps_g": round(sum(it["carbo"] for it in hypo), 1),
            "voci_ipo": [it["t"].strftime("%H:%M") for it in hypo],
            "glicemia_inizio": gindex.near(t),
            "minimo": min(lows) if lows else None,
            "glicemia_30": gindex.near(t + timedelta(minutes=30)),
            "glicemia_60": gindex.near(t + timedelta(minutes=60)),
            "dati_fino_a": last_data.strftime("%H:%M"),
            "completa": last_data >= t + timedelta(hours=1),
        }

    boluses = [it for it in mine if it["tipo"] == "bolo"]
    slow = [it for it in mine if it["tipo"] == "lento"]
    snacks = [it for it in mine if it["tipo"] == "spuntino"]
    bolus_u = sum(it["U"] for it in boluses)
    carbs_bolus = sum(it["carbo"] for it in boluses)
    carbs_slow = sum(it["carbo"] for it in slow)
    carbs_snack = sum(it["carbo"] for it in snacks)
    declared = carbs_bolus + carbs_slow + carbs_snack
    window_end = t + WINDOW
    if slow:
        window_end = max(window_end, max(it["fine"] for it in slow) + timedelta(hours=1))
    auto_u = _insulin_between(basal, t, window_end)
    window = [(pt, v) for pt, v in glucose if t <= pt <= window_end]
    start_g = gindex.near(t)
    peak = max(window, key=lambda p: p[1]) if window else None
    cover = len(window) >= 60
    return {
        "in_attesa": False,
        "ipo": False,
        "boli": [{"ora": it["t"].strftime("%H:%M"), "U": it["U"], "carbo_g": it["carbo"]} for it in boluses],
        "lenti": [{"da": it["t"].strftime("%H:%M"), "a": it["fine"].strftime("%H:%M"),
                   "quote": it["quote"], "carbo_g": it["carbo"]} for it in slow],
        "spuntini": [{"ora": it["t"].strftime("%H:%M"), "carbo_g": it["carbo"]} for it in snacks],
        "bolo_U": round(bolus_u, 2),
        "carbo_bolo_g": round(carbs_bolus, 1),
        "carbo_lento_g": round(carbs_slow, 1),
        "carbo_spuntino_g": round(carbs_snack, 1),
        "carbo_dichiarati_g": round(declared, 1),
        "differenza_carbo_g": round(eaten - declared, 1) if mine else None,
        "finestra_h": round((window_end - t).total_seconds() / 3600, 1),
        "insulina_auto_U": round(auto_u, 2),
        "glicemia_inizio": start_g,
        "picco": peak[1] if peak and cover else None,
        "picco_ora": peak[0].strftime("%H:%M") if peak and cover else None,
        "glicemia_2h": gindex.near(t + timedelta(hours=2)),
        "g_per_U_bolo": round(eaten / bolus_u, 1) if bolus_u > 0 and eaten else None,
        "g_per_U_totale": round(eaten / (bolus_u + auto_u), 1) if (bolus_u + auto_u) > 0 and eaten else None,
        "completa": last_data >= window_end,
        "dati_fino_a": last_data.strftime("%H:%M"),
    }


def _summary_json(row: dict) -> dict:
    row = dict(row)
    row["giorno"] = row["giorno"].isoformat()
    return row


def _carbs_eaten(store: Store, start: datetime, end: datetime) -> dict:
    out = {}
    for r in store.db.execute(
            "SELECT substr(m.ts_local, 1, 10) AS d, "
            "SUM(CASE WHEN m.meal_type = 'ipoglicemia' THEN 0 ELSE mi.carbs_g END) AS c, "
            "SUM(CASE WHEN m.meal_type = 'ipoglicemia' THEN mi.carbs_g ELSE 0 END) AS ci "
            "FROM meal m JOIN meal_item mi ON mi.meal_id = m.id "
            "WHERE m.ts_local >= ? AND m.ts_local < ? GROUP BY d",
            (fmt_local(start), fmt_local(end))):
        out[r["d"]] = (round(r["c"] or 0, 1), round(r["ci"] or 0, 1))
    return out


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (pct / 100)
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def agp_profile(store: Store, day_from: date, day_to: date, bin_minutes: int = 30) -> list[dict]:
    """Profilo tipico della giornata nel periodo (AGP semplificato): percentili di
    glicemia per fascia oraria, calcolati su tutte le letture dei giorni scelti."""
    start = datetime.combine(day_from, time())
    end = datetime.combine(day_to + timedelta(days=1), time())
    bins: dict[int, list[float]] = {}
    for row in store.db.execute(
            "SELECT ts_local, mgdl FROM glucose WHERE ts_local >= ? AND ts_local < ?",
            (fmt_local(start), fmt_local(end))):
        t = parse_local(row["ts_local"])
        b = ((t.hour * 60 + t.minute) // bin_minutes) * bin_minutes
        bins.setdefault(b, []).append(row["mgdl"])
    out = []
    for b in range(0, 1440, bin_minutes):
        vals = sorted(bins.get(b, []))
        if not vals:
            out.append({"min": b, "n": 0})
            continue
        out.append({
            "min": b, "n": len(vals),
            "p10": round(_percentile(vals, 10)),
            "p25": round(_percentile(vals, 25)),
            "p50": round(_percentile(vals, 50)),
            "p75": round(_percentile(vals, 75)),
            "p90": round(_percentile(vals, 90)),
        })
    return out


def _steps_by_day(store: Store, day_from: date, day_to: date) -> dict:
    rows = store.db.execute(
        "SELECT giorno, passi FROM activity_steps WHERE giorno >= ? AND giorno <= ?",
        (day_from.isoformat(), day_to.isoformat()))
    return {r["giorno"]: r["passi"] for r in rows}


def period_summary(store: Store, day_from: date, day_to: date) -> list[dict]:
    rows = [_summary_json(r) for r in daily_summary(store, day_from, day_to)]
    eaten = _carbs_eaten(store, datetime.combine(day_from, time()),
                         datetime.combine(day_to + timedelta(days=1), time()))
    passi = _steps_by_day(store, day_from, day_to)
    for r in rows:
        meals, hypo = eaten.get(r["giorno"], (None, None))
        r["carbo_mangiati_g"] = meals
        r["carbo_ipo_diario_g"] = hypo
        r["passi"] = passi.get(r["giorno"])
    return rows


def _main_photo_by_day(store: Store, day_from: date, day_to: date) -> dict:
    """Una foto per giorno: quella del pasto con piu' carboidrati quel giorno
    (il "pasto principale"), altrimenti la prima foto disponibile."""
    start = fmt_local(datetime.combine(day_from, time()))
    end = fmt_local(datetime.combine(day_to + timedelta(days=1), time()))
    rows = store.db.execute(
        "SELECT giorno, foto_id FROM ("
        "  SELECT substr(m.ts_local, 1, 10) AS giorno, mp.id AS foto_id,"
        "    COALESCE((SELECT SUM(carbs_g) FROM meal_item WHERE meal_id = m.id), 0) AS carbs,"
        "    ROW_NUMBER() OVER ("
        "      PARTITION BY substr(m.ts_local, 1, 10)"
        "      ORDER BY COALESCE((SELECT SUM(carbs_g) FROM meal_item WHERE meal_id = m.id), 0) DESC,"
        "               m.ts_local, mp.id"
        "    ) AS rn"
        "  FROM meal m JOIN meal_photo mp ON mp.meal_id = m.id"
        "  WHERE m.ts_local >= ? AND m.ts_local < ?"
        ") WHERE rn = 1", (start, end))
    return {r["giorno"]: r["foto_id"] for r in rows}


def calendar_month(store: Store, anno: int, mese: int) -> list[dict]:
    """Un riepilogo per ogni giorno del mese: per il tab Calendario."""
    ultimo_giorno = cal_mod.monthrange(anno, mese)[1]
    day_from = date(anno, mese, 1)
    day_to = date(anno, mese, ultimo_giorno)
    righe = period_summary(store, day_from, day_to)
    foto = _main_photo_by_day(store, day_from, day_to)
    for r in righe:
        r["foto_id"] = foto.get(r["giorno"])
    return righe


def food_occurrences(store: Store, food_id: int, limit: int = 10) -> dict:
    """Ultime occorrenze in cui un alimento/ricetta e' stato mangiato, con la
    curva glicemica -30/+180 min allineata all'orario del pasto, per "le altre
    volte che l'ho mangiato"."""
    rows = store.db.execute(
        "SELECT mi.grams, mi.carbs_g, m.id AS meal_id, m.ts_local "
        "FROM meal_item mi JOIN meal m ON m.id = mi.meal_id "
        "WHERE mi.food_id = ? ORDER BY m.ts_local DESC LIMIT ?", (food_id, limit)).fetchall()
    occorrenze = []
    for r in rows:
        t0 = parse_local(r["ts_local"])
        inizio, fine = t0 - OCCURRENCE_BEFORE, t0 + OCCURRENCE_AFTER
        glicemia = [(_minutes(t, t0), v) for t, v in _glucose(store, inizio, fine)]
        picco = max((v for _, v in glicemia), default=None)
        picco_min = next((mm for mm, v in glicemia if v == picco), None) if picco is not None else None
        rientrata = any(mm > 0 and v <= 180 for mm, v in glicemia if mm > (picco_min or 0))
        bolo = store.db.execute(
            "SELECT insulin_delivered FROM bolus WHERE soft_deleted = 0 AND ts_local BETWEEN ? AND ? "
            "ORDER BY ABS(julianday(ts_local) - julianday(?)) LIMIT 1",
            (fmt_local(t0 - timedelta(minutes=45)), fmt_local(t0 + timedelta(minutes=45)), r["ts_local"])
        ).fetchone()
        occorrenze.append({
            "pasto_id": r["meal_id"], "ora": r["ts_local"], "grammi": r["grams"], "carbo_g": r["carbs_g"],
            "glicemia": glicemia, "picco": picco, "picco_min": picco_min, "rientrata_in_range": rientrata,
            "bolo_U": round(bolo["insulin_delivered"], 2) if bolo else None,
        })
    con_picco = [o for o in occorrenze if o["picco"] is not None]
    riepilogo = {
        "n": len(occorrenze),
        "picco_medio": round(sum(o["picco"] for o in con_picco) / len(con_picco)) if con_picco else None,
        "tempo_al_picco_medio": round(sum(o["picco_min"] for o in con_picco) / len(con_picco)) if con_picco else None,
        "rientro_pct": round(100 * sum(o["rientrata_in_range"] for o in occorrenze) / len(occorrenze), 1)
                       if occorrenze else None,
    }
    return {"occorrenze": occorrenze, "riepilogo": riepilogo}


def _item_json(it: dict, day_start: datetime) -> dict:
    return {
        "tipo": it["tipo"],
        "ora": it["t"].strftime("%H:%M"),
        "min": _minutes(it["t"], day_start),
        "fine": it["fine"].strftime("%H:%M"),
        "righe_min": [_minutes(t, day_start) for t, _ in it["righe"]],
        "U": it["U"],
        "carbo_g": it["carbo"],
        "quote": it["quote"],
        "glicemia": it.get("glicemia"),
        "rec_carbo_U": it.get("rec_carbo_U"),
        "rec_correzione_U": it.get("rec_correzione_U"),
        "pasto_id": it.get("_pasto"),
    }


def day_payload(store: Store, day: date) -> dict:
    start = datetime.combine(day, time())
    end = start + timedelta(days=1)
    glucose_all = _glucose(store, start - CONTEXT, end + CONTEXT)
    gindex = GlucoseIndex(glucose_all)
    items = classify(_bolus_rows(store, start - CONTEXT, end + CONTEXT), gindex)
    basal = _basal_segments(store, start - CONTEXT, end + CONTEXT)
    meals = meals_between(store, fmt_local(start - CONTEXT), fmt_local(end + CONTEXT))
    _assign(meals, items)
    last = store.last_glucose_local()
    last_data = parse_local(last) if last else None
    day_meals = [m for m in meals if start <= parse_local(m["ora"]) < end]
    for m in day_meals:
        m["analisi"] = analyse_meal(m, items, gindex, glucose_all, basal, last_data)

    modes = {}
    for r in store.db.execute(
            "SELECT ts_local, mode, duration_s FROM camaps_mode WHERE ts_local >= ? AND ts_local < ? "
            "ORDER BY ts_local", (fmt_local(start - timedelta(days=1)), fmt_local(end))):
        a = parse_local(r["ts_local"])
        b = a + timedelta(seconds=r["duration_s"])
        if b <= start or a >= end:
            continue
        spans = modes.setdefault(r["mode"], [])
        a, b = max(a, start), min(b, end)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])

    events = [{"tipo": r["kind"], "min": _minutes(parse_local(r["ts_local"]), start)}
              for r in store.db.execute(
                  "SELECT ts_local, kind FROM device_event WHERE ts_local >= ? AND ts_local < ?",
                  (fmt_local(start), fmt_local(end)))]

    summary = period_summary(store, day, day)[0]
    return {
        "giorno": day.isoformat(),
        "soglie": {"bassa": float(store.get("soglia_bassa", 70)),
                   "alta": float(store.get("soglia_alta", 180))},
        "glicemie": [[_minutes(t, start), v] for t, v in glucose_all if start <= t < end],
        "voci": [_item_json(it, start) for it in items if start <= it["t"] < end],
        "basale": [[_minutes(a, start), _minutes(b, start), rate]
                   for a, b, rate in basal if a < end and b > start],
        "modalita": [{"tipo": k, "da": _minutes(a, start), "a": _minutes(b, start)}
                     for k, spans in modes.items() for a, b in spans],
        "eventi": events,
        "pasti": day_meals,
        "note": notes_for_day(store, day.isoformat()),
        "riepilogo": summary,
        "ultimo_dato": last,
    }


# -- integrazione Home Assistant (Fase 4) -------------------------------------
# Un solo riepilogo compatto per il coordinator della integrazione HA: evita
# che i sensori facciano piu' chiamate separate ogni ciclo di polling.

def _glucose_trend(store: Store) -> str | None:
    """Tendenza semplice confrontando l'ultima lettura con quella di circa 10-15
    minuti prima: solo descrittiva, nessuna indicazione terapeutica."""
    rows = [dict(r) for r in store.db.execute(
        "SELECT ts_local, mgdl FROM glucose ORDER BY ts_local DESC LIMIT 5")]
    if len(rows) < 2:
        return None
    latest = rows[0]
    t_latest = parse_local(latest["ts_local"])
    prev = next((r for r in rows[1:]
                 if (t_latest - parse_local(r["ts_local"])).total_seconds() >= 480), rows[-1])
    minuti = (t_latest - parse_local(prev["ts_local"])).total_seconds() / 60
    if minuti <= 0:
        return "stabile"
    tasso = (latest["mgdl"] - prev["mgdl"]) / minuti  # mg/dL al minuto
    if tasso >= 3:
        return "in salita rapida"
    if tasso >= 1:
        return "in salita"
    if tasso <= -3:
        return "in discesa rapida"
    if tasso <= -1:
        return "in discesa"
    return "stabile"


def _last_meal(store: Store) -> dict | None:
    row = store.db.execute("SELECT * FROM meal ORDER BY ts_local DESC LIMIT 1").fetchone()
    if not row:
        return None
    carbo = store.db.execute(
        "SELECT COALESCE(SUM(carbs_g), 0) AS c FROM meal_item WHERE meal_id = ?",
        (row["id"],)).fetchone()["c"]
    return {"ts_local": row["ts_local"], "tipo": row["meal_type"], "carboidrati_g": round(carbo, 1)}


def _last_bolus(store: Store) -> dict | None:
    row = store.db.execute(
        "SELECT ts_local, insulin_delivered FROM bolus WHERE soft_deleted = 0 "
        "ORDER BY ts_local DESC LIMIT 1").fetchone()
    if not row:
        return None
    return {"ts_local": row["ts_local"], "unita_U": round(row["insulin_delivered"] or 0, 2)}


def ha_snapshot(store: Store, oggi: date) -> dict:
    ultima = store.db.execute(
        "SELECT ts_local, mgdl FROM glucose ORDER BY ts_local DESC LIMIT 1").fetchone()
    minuti_da_ultimo = None
    if ultima:
        minuti_da_ultimo = round((now_local() - parse_local(ultima["ts_local"])).total_seconds() / 60)
    oggi_riga = period_summary(store, oggi, oggi)[0]
    settimana = period_summary(store, oggi - timedelta(days=6), oggi)
    full_sett = [r for r in settimana if (r.get("copertura_cgm_pct") or 0) >= 70]
    n_read = sum(r["letture_cgm"] for r in full_sett)
    tir_7g = (sum(r["tir_pct"] * r["letture_cgm"] for r in full_sett) / n_read) if n_read else None
    return {
        "glicemia": {
            "mgdl": ultima["mgdl"] if ultima else None,
            "trend": _glucose_trend(store),
            "ts_local": ultima["ts_local"] if ultima else None,
            "minuti_da_ultimo_dato": minuti_da_ultimo,
        },
        "tir_oggi_pct": oggi_riga.get("tir_pct"),
        "tir_7g_pct": round(tir_7g, 1) if tir_7g is not None else None,
        "carboidrati_oggi_g": oggi_riga.get("carbo_mangiati_g"),
        "insulina_oggi_U": oggi_riga.get("totale_U"),
        "insulina_oggi_boli_U": oggi_riga.get("boli_U"),
        "insulina_oggi_basale_U": oggi_riga.get("basale_U"),
        "ultimo_pasto": _last_meal(store),
        "ultimo_bolo": _last_bolus(store),
        "collegamento": {
            "ok": store.get("sync_stato") == "ok",
            "stato": store.get("sync_stato", "mai"),
            "errore": store.get("sync_errore") or None,
        },
    }
