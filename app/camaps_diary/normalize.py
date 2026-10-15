"""Conversione dei record Glooko nelle righe del database."""

import json

from .timeutil import fmt_local, parse_glooko_local, utc_iso


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def bolus_row(r: dict, tz: str) -> dict:
    """Bolo CamAPS. type: 'suggested' = dal calcolatore di CamAPS;
    'manual' con 0 U = voce di soli carboidrati (es. pasto a lento
    assorbimento suddiviso da CamAPS)."""
    t = parse_glooko_local(r["pumpTimestamp"])
    return {
        "guid": r["guid"],
        "ts_local": fmt_local(t),
        "ts_utc": utc_iso(t, tz),
        "insulin_delivered": _num(r.get("insulinDelivered")) or 0.0,
        "insulin_programmed": _num(r.get("insulinProgrammed")),
        "carbs_input": _num(r.get("carbsInput")) or 0.0,
        "rec_carbs": _num(r.get("insulinRecommendationForCarbs")),
        "rec_correction": _num(r.get("insulinRecommendationForCorrection")),
        "rec_total": _num(r.get("totalInsulinRecommendation")),
        "insulin_on_board": _num(r.get("insulinOnBoard")),
        "bolus_type": r.get("type"),
        "soft_deleted": 1 if r.get("softDeleted") else 0,
        "updated_at": r.get("updatedAt"),
        "raw": json.dumps(r, ensure_ascii=False),
    }


def basal_row(r: dict, tz: str) -> dict:
    """Segmento di basale: con CamAPS in automatico e' la velocita' decisa
    dall'algoritmo per qualche minuto (rate in U/h, duration in secondi)."""
    t = parse_glooko_local(r["pumpTimestamp"])
    return {
        "guid": r["guid"],
        "ts_local": fmt_local(t),
        "ts_utc": utc_iso(t, tz),
        "duration_s": int(r.get("duration") or 0),
        "rate": _num(r.get("rate")) or 0.0,
        "soft_deleted": 1 if r.get("softDeleted") else 0,
        "updated_at": r.get("updatedAt"),
    }


def v3_rows(page: dict, tz: str):
    """Dal grafico v3: glicemie, modalita' CamAPS (deduplicate) ed eventi."""
    series = (page or {}).get("series") or {}
    glucose, modes, events = {}, {}, {}
    for name in ("cgmLow", "cgmNormal", "cgmHigh"):
        for p in series.get(name) or []:
            if p.get("calculated") or not p.get("timestamp"):
                continue
            mgdl = p.get("y")
            if mgdl is None and p.get("value"):
                mgdl = p["value"] / 100.0
            if not mgdl or mgdl <= 0:
                continue
            t = parse_glooko_local(p["timestamp"])
            glucose[fmt_local(t)] = {"ts_local": fmt_local(t), "ts_utc": utc_iso(t, tz),
                                     "mgdl": int(round(mgdl))}
    for name, pts in series.items():
        if not name.startswith("pumpCamaps"):
            continue
        for p in pts or []:
            if not p.get("timestamp") or not p.get("type"):
                continue
            t = parse_glooko_local(p["timestamp"])
            key = (fmt_local(t), p["type"])
            dur = int(p.get("duration") or 0)
            if key not in modes or dur > modes[key]["duration_s"]:
                modes[key] = {"ts_local": key[0], "mode": key[1], "duration_s": dur}
    for name, kind in (("reservoirChange", "cambio_cartuccia"), ("setSiteChange", "cambio_set")):
        for p in series.get(name) or []:
            if p.get("timestamp"):
                ts = fmt_local(parse_glooko_local(p["timestamp"]))
                events[(ts, kind)] = {"ts_local": ts, "kind": kind}
    return list(glucose.values()), list(modes.values()), list(events.values())


def profile_settings(profile: dict) -> dict:
    """Solo fuso orario e soglie glicemiche dal profilo Glooko: niente dati
    anagrafici. Le soglie Glooko sono in mg/dL x 100."""
    user = (profile or {}).get("currentUser") or (profile or {}).get("currentPatient") or {}
    pref = user.get("preference") or {}
    out = {}
    if user.get("timezone"):
        out["timezone"] = user["timezone"]
    for key, src in (("soglia_bassa", "normalGlucoseMin"),
                     ("soglia_alta", "afterMealNormalGlucoseMax"),
                     ("soglia_molto_bassa", "seriousLow"),
                     ("soglia_molto_alta", "seriousHigh")):
        v = pref.get(src)
        if isinstance(v, (int, float)) and v > 0:
            out[key] = str(round(v / 100))
    if pref.get("meterUnits"):
        out["unita"] = pref["meterUnits"]
    return out
