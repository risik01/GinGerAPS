"""Riepilogo giornaliero: glicemia, insulina (boli + basale automatica),
carboidrati inseriti in CamAPS e modalita' del sistema."""

import statistics
from datetime import date, datetime, time, timedelta

from .camaps_entries import classify
from .store import Store
from .timeutil import fmt_local, parse_local, split_by_day


def _range(day_from: date, day_to: date):
    d = day_from
    while d <= day_to:
        yield d
        d += timedelta(days=1)


def daily_summary(store: Store, day_from: date, day_to: date) -> list[dict]:
    low = float(store.get("soglia_bassa", 70))
    high = float(store.get("soglia_alta", 180))
    very_low = float(store.get("soglia_molto_bassa", 54))
    very_high = float(store.get("soglia_molto_alta", 250))
    start = datetime.combine(day_from, time())
    end = datetime.combine(day_to + timedelta(days=1), time())
    s_start, s_end = fmt_local(start), fmt_local(end)
    # i segmenti di basale e le modalita' possono iniziare il giorno prima
    s_prev = fmt_local(start - timedelta(days=1))

    days = {d: {"giorno": d, "glicemie": [], "boli_U": 0.0, "n_boli": 0,
                "carbo_con_bolo_g": 0.0, "carbo_lento_g": 0.0, "carbo_spuntino_g": 0.0,
                "carbo_ipo_g": 0.0, "n_ipo": 0,
                "basale_U": 0.0, "modalita_h": {}} for d in _range(day_from, day_to)}

    for row in store.db.execute(
            "SELECT ts_local, mgdl FROM glucose WHERE ts_local >= ? AND ts_local < ? ORDER BY ts_local",
            (s_start, s_end)):
        days[parse_local(row["ts_local"]).date()]["glicemie"].append(
            (parse_local(row["ts_local"]), row["mgdl"]))

    # Voci CamAPS: classificate con un margine di qualche ora, perche' un
    # pasto a lenta digestione puo' scavalcare la mezzanotte.
    margin = timedelta(hours=6)
    rows = [dict(r) for r in store.db.execute(
        "SELECT guid, ts_local, insulin_delivered, carbs_input, bolus_type, rec_carbs, rec_correction "
        "FROM bolus WHERE soft_deleted = 0 AND ts_local >= ? AND ts_local < ?",
        (fmt_local(start - margin), fmt_local(end + margin)))]
    glucose_ctx = [(parse_local(r["ts_local"]), r["mgdl"]) for r in store.db.execute(
        "SELECT ts_local, mgdl FROM glucose WHERE ts_local >= ? AND ts_local < ?",
        (fmt_local(start - margin), fmt_local(end + margin)))]
    for item in classify(rows, glucose_ctx):
        for t, carbs in item["righe"]:
            d = days.get(t.date())
            if d is None:
                continue
            if item["tipo"] == "bolo":
                d["boli_U"] += item["U"]
                d["n_boli"] += 1
                d["carbo_con_bolo_g"] += carbs
            elif item["tipo"] == "lento":
                d["carbo_lento_g"] += carbs
            elif item["tipo"] == "spuntino":
                d["carbo_spuntino_g"] += carbs
            elif item["tipo"] == "ipo":
                d["carbo_ipo_g"] += carbs
                d["n_ipo"] += 1

    for row in store.db.execute(
            "SELECT ts_local, duration_s, rate FROM basal "
            "WHERE soft_deleted = 0 AND ts_local >= ? AND ts_local < ?", (s_prev, s_end)):
        for day, secs in split_by_day(parse_local(row["ts_local"]), row["duration_s"]).items():
            if day in days:
                days[day]["basale_U"] += row["rate"] * secs / 3600

    # Intervalli per modalita', fusi se si sovrappongono (Glooko spezza le
    # modalita' a mezzanotte e una modalita' ancora in corso si allunga tra
    # una sync e l'altra: cosi' non si conta mai due volte lo stesso tempo).
    intervals = {}
    for row in store.db.execute(
            "SELECT ts_local, mode, duration_s FROM camaps_mode "
            "WHERE ts_local >= ? AND ts_local < ? ORDER BY ts_local", (s_prev, s_end)):
        t0 = parse_local(row["ts_local"])
        intervals.setdefault(row["mode"], []).append((t0, t0 + timedelta(seconds=row["duration_s"])))
    for mode, spans in intervals.items():
        merged = []
        for a, b in sorted(spans):
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        for a, b in merged:
            for day, secs in split_by_day(a, (b - a).total_seconds()).items():
                if day in days:
                    m = days[day]["modalita_h"]
                    m[mode] = m.get(mode, 0.0) + secs / 3600

    out = []
    for d in days.values():
        readings = d.pop("glicemie")
        values = [v for _, v in readings]
        n = len(values)
        covered = 0.0
        for (t1, _), (t2, _) in zip(readings, readings[1:]):
            covered += min((t2 - t1).total_seconds(), 300)
        covered += 60 if readings else 0
        mean = statistics.fmean(values) if values else None
        sd = statistics.pstdev(values) if n > 1 else None
        d.update({
            "letture_cgm": n,
            "copertura_cgm_pct": round(100 * covered / 86400, 1),
            "media_mgdl": round(mean) if mean else None,
            "dev_std_mgdl": round(sd, 1) if sd else None,
            "cv_pct": round(100 * sd / mean, 1) if sd and mean else None,
            "gmi_pct": round(3.31 + 0.02392 * mean, 1) if mean else None,
            "tir_pct": round(100 * sum(low <= v <= high for v in values) / n, 1) if n else None,
            "sotto_pct": round(100 * sum(v < low for v in values) / n, 1) if n else None,
            "molto_sotto_pct": round(100 * sum(v < very_low for v in values) / n, 1) if n else None,
            "sopra_pct": round(100 * sum(v > high for v in values) / n, 1) if n else None,
            "molto_sopra_pct": round(100 * sum(v > very_high for v in values) / n, 1) if n else None,
            "boli_U": round(d["boli_U"], 2),
            "basale_U": round(d["basale_U"], 2),
            "totale_U": round(d["boli_U"] + d["basale_U"], 2),
            "carbo_con_bolo_g": round(d["carbo_con_bolo_g"], 1),
            "carbo_lento_g": round(d["carbo_lento_g"], 1),
            "carbo_spuntino_g": round(d["carbo_spuntino_g"], 1),
            "carbo_ipo_g": round(d["carbo_ipo_g"], 1),
            # carboidrati dichiarati per i pasti (esclusi i trattamenti dell'ipoglicemia)
            "carbo_camaps_g": round(d["carbo_con_bolo_g"] + d["carbo_lento_g"] + d["carbo_spuntino_g"], 1),
            "modalita_h": {k: round(v, 2) for k, v in sorted(d["modalita_h"].items())},
        })
        out.append(d)
    return out


MODE_LABELS = {
    "automatic": "auto", "boost": "boost", "ease_off": "ease-off", "manual": "manuale",
    "no_cgm": "senza sensore", "no_pump_connectivity": "senza microinfusore",
    "pump_delivery_suspended": "erogazione sospesa", "unable_to_proceed": "bloccato",
}


def format_table(rows: list[dict]) -> str:
    head = (f"{'Giorno':<11}{'CGM%':>6}{'Media':>7}{'TIR%':>6}{'<70%':>6}{'>180%':>7}"
            f"{'Boli U':>8}{'Basale U':>10}{'Totale U':>10}{'Carbo CamAPS':>14}{'Ipo g':>8}"
            f"{'Auto h':>8}")
    lines = [head, "-" * len(head)]
    def fmt(v, spec):
        if v is not None:
            return format(v, spec)
        width = "".join(ch for ch in spec.split(".")[0] if ch.isdigit())
        return format("-", ">" + width)
    for r in rows:
        lines.append(
            f"{r['giorno'].strftime('%d/%m/%Y'):<11}{fmt(r['copertura_cgm_pct'], '>6.0f')}"
            f"{fmt(r['media_mgdl'], '>7')}{fmt(r['tir_pct'], '>6.0f')}{fmt(r['sotto_pct'], '>6.0f')}"
            f"{fmt(r['sopra_pct'], '>7.0f')}{r['boli_U']:>8.1f}{r['basale_U']:>10.1f}"
            f"{r['totale_U']:>10.1f}{r['carbo_camaps_g']:>14.0f}{r['carbo_ipo_g']:>8.0f}"
            f"{r['modalita_h'].get('automatic', 0):>8.1f}")
        others = {MODE_LABELS.get(k, k): v for k, v in r["modalita_h"].items()
                  if k != "automatic" and v >= 0.05}
        if others:
            lines.append(" " * 11 + "altre modalita': "
                         + ", ".join(f"{k} {v:.1f} h" for k, v in others.items()))
    return "\n".join(lines)
