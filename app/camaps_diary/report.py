"""Report PDF per il diabetologo, formato AGP a una pagina piu' pagine di dettaglio.

Principio non negoziabile del progetto (vedi roadmap): questo report non contiene
mai indicazioni di dose o di terapia, solo numeri descrittivi del periodo scelto.
Le metriche di periodo (TIR, media, insulina, carboidrati) si calcolano con lo
stesso metodo (media pesata per numero di letture, giorni <70% di copertura CGM
esclusi dalle medie) usato dalla scheda Andamento: cosi' i numeri nel PDF
coincidono sempre con quelli mostrati nell'app per lo stesso intervallo.
"""

import html
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .analysis import agp_profile, day_payload
from . import report_analisi as ra
from .diary import notes_for_day
from .store import Store
from .timeutil import fmt_local, now_local, parse_local

STATIC_DIR = Path(__file__).with_name("static")

MEAL_LABELS = {
    "colazione": "Colazione", "spuntino": "Spuntino", "pranzo": "Pranzo",
    "merenda": "Merenda", "cena": "Cena", "fuori_pasto": "Fuori pasto", "ipoglicemia": "Correzione ipo",
}

# Obiettivi di consenso internazionale per l'AGP (ADA/ATTD), usati solo come
# riferimento visivo accanto ai numeri della persona: non sono soglie di allarme.
CONSENSUS = {"tir_pct": 70, "sotto_pct": 4, "molto_sotto_pct": 1, "molto_sopra_pct": 5, "cv_pct": 36}

DISCLAIMER = ("Strumento di registrazione e visualizzazione. Non fornisce indicazioni "
              "terapeutiche. Le stime nutrizionali sono approssimative.")

COVERAGE_MIN_PCT = 70


# -- metriche di periodo ------------------------------------------------------

def _wavg(full_days: list[dict], key: str):
    n = sum(d["letture_cgm"] for d in full_days)
    if not n:
        return None
    return sum((d.get(key) or 0) * d["letture_cgm"] for d in full_days) / n


def _avg(rows: list[dict], key: str):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def _pooled_mean_sd(full_days: list[dict]):
    """Media e deviazione standard sulle letture vere e proprie dei giorni
    qualificanti, ricostruite dalle medie/deviazioni giornaliere (esatto: la
    varianza di un insieme unito si calcola dalla somma dei quadrati per
    gruppo, non e' un'approssimazione)."""
    n = sum(d["letture_cgm"] for d in full_days)
    if not n:
        return None, None
    mean = sum((d["media_mgdl"] or 0) * d["letture_cgm"] for d in full_days) / n
    sumsq = sum(d["letture_cgm"] * ((d.get("dev_std_mgdl") or 0) ** 2 + (d["media_mgdl"] or 0) ** 2)
                for d in full_days)
    var = max(sumsq / n - mean ** 2, 0.0)
    return mean, var ** 0.5


def period_metrics(giorni: list[dict]) -> dict:
    full = [g for g in giorni if (g.get("copertura_cgm_pct") or 0) >= COVERAGE_MIN_PCT]
    mean, sd = _pooled_mean_sd(full)
    with_meals = [g for g in giorni if g.get("carbo_mangiati_g") is not None]
    with_steps = [g for g in giorni if g.get("passi") is not None]
    return {
        "giorni_totali": len(giorni),
        "giorni_con_dati": len(full),
        "media_mgdl": round(mean) if mean else None,
        "gmi_pct": round(3.31 + 0.02392 * mean, 1) if mean else None,
        "cv_pct": round(100 * sd / mean, 1) if sd and mean else None,
        "tir_pct": round(_wavg(full, "tir_pct"), 1) if full else None,
        "sotto_pct": round(_wavg(full, "sotto_pct"), 1) if full else None,
        "molto_sotto_pct": round(_wavg(full, "molto_sotto_pct"), 1) if full else None,
        "sopra_pct": round(_wavg(full, "sopra_pct"), 1) if full else None,
        "molto_sopra_pct": round(_wavg(full, "molto_sopra_pct"), 1) if full else None,
        "insulina_U": round(_avg(full, "totale_U"), 1) if full else None,
        "bolo_U": round(_avg(full, "boli_U"), 1) if full else None,
        "basale_U": round(_avg(full, "basale_U"), 1) if full else None,
        "carbo_mangiati_g": round(_avg(with_meals, "carbo_mangiati_g"), 0) if with_meals else None,
        "carbo_camaps_g": round(_avg(full, "carbo_camaps_g"), 0) if full else None,
        "passi": round(_avg(with_steps, "passi"), 0) if with_steps else None,
    }


# -- giorni notevoli -----------------------------------------------------------

def _hyper_minutes(store: Store, giorno_iso: str) -> float:
    """Minuti con glicemia sopra 250 mg/dL in quel giorno (gap fra letture
    successive attribuito all'iperglicemia se la lettura che lo chiude e'
    sopra soglia, con lo stesso tetto di 5 minuti per buco usato altrove per
    la copertura CGM)."""
    d = date.fromisoformat(giorno_iso)
    start, end = datetime.combine(d, time()), datetime.combine(d, time()) + timedelta(days=1)
    rows = list(store.db.execute(
        "SELECT ts_local, mgdl FROM glucose WHERE ts_local >= ? AND ts_local < ? ORDER BY ts_local",
        (fmt_local(start), fmt_local(end))))
    total = 0.0
    prev_t = None
    for row in rows:
        t = parse_local(row["ts_local"])
        if prev_t is not None and row["mgdl"] > 250:
            total += min((t - prev_t).total_seconds(), 300)
        prev_t = t
    return total / 60


def notable_days(store: Store, giorni: list[dict]) -> list[dict]:
    out = []
    for g in giorni:
        giorno = g["giorno"]
        note = [n["testo"] for n in notes_for_day(store, giorno)]
        if not g["letture_cgm"]:
            out.append({"giorno": giorno, "tipo": "senza_dati", "dettaglio": "Nessun dato dal sensore", "note": note})
            continue
        if (g.get("molto_sotto_pct") or 0) > 0:
            out.append({"giorno": giorno, "tipo": "ipo",
                        "dettaglio": f"{round(g['molto_sotto_pct'])}% delle letture sotto 54 mg/dL",
                        "note": note})
        minuti = _hyper_minutes(store, giorno)
        if minuti >= 120:
            out.append({"giorno": giorno, "tipo": "iper",
                        "dettaglio": f"{round(minuti)} minuti sopra 250 mg/dL", "note": note})
    return out


# -- pasti e dettaglio giornaliero ----------------------------------------------

def _meal_row(payload_giorno: str, m: dict) -> dict:
    """Una riga pasto per la tabella del periodo: sempre presente, anche se
    i dati CamAPS di quell'ora non sono ancora arrivati da Glooko (prima si
    saltava il pasto del tutto in quel caso, facendolo sembrare sparito)."""
    a = m.get("analisi") or {}
    alimenti = ", ".join(v["descrizione"] for v in m["voci"]) if m["voci"] else "—"
    in_attesa = bool(a.get("in_attesa"))
    camaps_g = None if in_attesa else (a.get("carbo_ipo_camaps_g") if a.get("ipo") else a.get("carbo_dichiarati_g"))
    return {
        "giorno": payload_giorno, "ora": m["ora"][11:16],
        "tipo": MEAL_LABELS.get(m["tipo"], m["tipo"]),
        "alimenti": alimenti,
        "carbo_dichiarati_g": m["carbo_g"],
        "carbo_camaps_g": camaps_g,
        "bolo_U": None if in_attesa else a.get("bolo_U"),
        "picco_2h": None if in_attesa else a.get("glicemia_2h"),
        "in_attesa": in_attesa,
        "foto_id": m["foto"][0]["id"] if m.get("foto") else None,
        "tipo_key": m["tipo"],
        "carbo_diario_g": m["carbo_g"],
        "nutrienti": m.get("nutrienti") or {},
        "voci": [{"descrizione": v["descrizione"], "grammi": v.get("grammi"), "carbo_g": v["carbo_g"],
                  "nutrienti": v.get("nutrienti") or {}, "ia": bool(v.get("nutrienti_ia"))} for v in m["voci"]],
        "picco": None if in_attesa else a.get("picco"),
        "glicemia_inizio": None if in_attesa else a.get("glicemia_inizio"),
    }


def day_blocks(store: Store, day_from: date, day_to: date, giorni_summary: list[dict]) -> list[dict]:
    """Un blocco completo per ogni giorno del periodo (grafico della glicemia
    coi pasti segnati, e i pasti di quel giorno con tutte le informazioni):
    un solo giro su day_payload, riusato sia per la tabella pasti del periodo
    sia per il dettaglio giorno per giorno."""
    riepilogo_per_giorno = {g["giorno"]: g for g in giorni_summary}
    blocchi = []
    d = day_from
    while d <= day_to:
        payload = day_payload(store, d)
        pasti = [_meal_row(payload["giorno"], m) for m in payload["pasti"]]
        blocchi.append({
            "giorno": payload["giorno"],
            "riepilogo": riepilogo_per_giorno.get(payload["giorno"], {}),
            "glicemie": payload["glicemie"],
            "voci": payload["voci"],
            "soglie": payload["soglie"],
            "pasti": pasti,
            "nutrienti": payload.get("nutrienti") or {},
        })
        d += timedelta(days=1)
    return blocchi


# -- raccolta dati completa ------------------------------------------------------

def build_report_data(store: Store, day_from: date, day_to: date, kind: str,
                       domande_visita: str = "") -> dict:
    from .analysis import period_summary, steps_summary
    giorni = period_summary(store, day_from, day_to)
    blocchi = day_blocks(store, day_from, day_to, giorni)
    metriche = period_metrics(giorni)
    soglie = {"bassa": float(store.get("soglia_bassa", 70)), "alta": float(store.get("soglia_alta", 180))}
    notevoli = ra.arricchisci_notevoli(notable_days(store, giorni), blocchi, giorni, metriche, soglie)
    return {
        "generato": now_local(),
        "day_from": day_from, "day_to": day_to, "kind": kind,
        "giorni": giorni,
        "passi": steps_summary(store, day_from, day_to),
        "metriche": metriche,
        "agp": agp_profile(store, day_from, day_to),
        "notevoli": notevoli,
        "consigli": ra.costruisci_consigli(blocchi, giorni, metriche, soglie),
        "giorni_dettaglio": blocchi,
        "pasti": [p for b in blocchi for p in b["pasti"]],
        "domande_visita": (domande_visita or "").strip(),
        "consensus": CONSENSUS,
    }


# -- disegno AGP (SVG inline, stesso stile grafico dell'app) --------------------

def _agp_svg(bins: list[dict], low: float, high: float) -> str:
    W, H = 640, 220
    m = {"l": 40, "r": 10, "t": 10, "b": 24}
    pw, ph = W - m["l"] - m["r"], H - m["t"] - m["b"]
    y_min, y_max = 40, 300

    def x(mm):
        return m["l"] + mm / 1440 * pw

    def y(v):
        v = min(max(v, y_min), y_max)
        return m["t"] + (1 - (v - y_min) / (y_max - y_min)) * ph

    with_data = [b for b in bins if b.get("n")]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">']
    parts.append(f'<rect x="{m["l"]}" y="{y(high):.1f}" width="{pw:.1f}" '
                  f'height="{(y(low) - y(high)):.1f}" fill="#d9f0ec"/>')
    for h in range(0, 25, 6):
        xx = x(h * 60)
        parts.append(f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{m["t"]}" y2="{m["t"] + ph}" stroke="#e4e4e0"/>')
        parts.append(f'<text x="{xx:.1f}" y="{m["t"] + ph + 16}" font-size="9" '
                     f'text-anchor="middle" fill="#6b6b63">{h}:00</text>')

    def band(lo_key, hi_key, opacity):
        if not with_data:
            return ""
        top = "M" + " L".join(f'{x(b["min"]):.1f},{y(b[hi_key]):.1f}' for b in with_data)
        bot = " L".join(f'{x(b["min"]):.1f},{y(b[lo_key]):.1f}' for b in reversed(with_data))
        return f'<path d="{top} L{bot} Z" fill="#17242d" opacity="{opacity}"/>'

    parts.append(band("p10", "p90", 0.12))
    parts.append(band("p25", "p75", 0.28))
    if with_data:
        med = "M" + " L".join(f'{x(b["min"]):.1f},{y(b["p50"]):.1f}' for b in with_data)
        parts.append(f'<path d="{med}" fill="none" stroke="#17242d" stroke-width="2.2"/>')
    parts.append("</svg>")
    return "".join(parts)


def _day_svg(glicemie: list, voci: list, low: float, high: float) -> str:
    """Grafico compatto di un solo giorno: linea della glicemia, banda del
    range, boli (triangoli) e carboidrati senza bolo (punti) — la stessa
    lettura rapida del grafico "Giorno" dell'app, in formato da stampa."""
    W, H = 600, 110
    m = {"l": 30, "r": 8, "t": 14, "b": 16}
    pw, ph = W - m["l"] - m["r"], H - m["t"] - m["b"]
    y_min, y_max = 40, 300

    def x(mm):
        return m["l"] + mm / 1440 * pw

    def y(v):
        v = min(max(v, y_min), y_max)
        return m["t"] + (1 - (v - y_min) / (y_max - y_min)) * ph

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">']
    parts.append(f'<rect x="{m["l"]}" y="{y(high):.1f}" width="{pw:.1f}" '
                  f'height="{(y(low) - y(high)):.1f}" fill="#d9f0ec"/>')
    for h in range(0, 25, 6):
        xx = x(h * 60)
        parts.append(f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{m["t"]}" y2="{m["t"] + ph}" stroke="#e4e4e0"/>')
        parts.append(f'<text x="{xx:.1f}" y="{m["t"] + ph + 12}" font-size="8" '
                     f'text-anchor="middle" fill="#6b6b63">{h}</text>')
    if glicemie:
        d = "M" + " L".join(f"{x(mm):.1f},{y(v):.1f}" for mm, v in glicemie)
        parts.append(f'<path d="{d}" fill="none" stroke="#17242d" stroke-width="1.3"/>')
    for v in voci:
        xx = x(v["min"])
        if v["tipo"] == "bolo":
            parts.append(f'<path d="M{xx-4:.1f},{m["t"]-7} L{xx+4:.1f},{m["t"]-7} L{xx:.1f},{m["t"]-1} Z" fill="#0e9f8e"/>')
        elif v["tipo"] == "ipo":
            parts.append(f'<circle cx="{xx:.1f}" cy="{m["t"]-4}" r="2.5" fill="#c5433f"/>')
        else:
            parts.append(f'<circle cx="{xx:.1f}" cy="{m["t"]-4}" r="2" fill="#9a5f07"/>')
    parts.append("</svg>")
    return "".join(parts)


def _steps_svg(giorni: list[dict]) -> str:
    """Barre dei passi di ogni giorno del periodo."""
    if not giorni:
        return ""
    W, H = 600, 90
    m = {"l": 36, "r": 8, "t": 6, "b": 16}
    pw, ph = W - m["l"] - m["r"], H - m["t"] - m["b"]
    valori = [g["passi"] for g in giorni]
    top = max(1000, max(valori) if valori else 1000)
    n = len(giorni)
    bw = pw / n
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">']
    for ref in (0, top / 2, top):
        yy = m["t"] + ph * (1 - ref / top)
        parts.append(f'<line x1="{m["l"]}" x2="{m["l"] + pw}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#e4e4e0"/>')
        parts.append(f'<text x="{m["l"] - 4}" y="{yy + 3:.1f}" font-size="8" text-anchor="end" '
                     f'fill="#6b6b63">{ref:.0f}</text>')
    for i, g in enumerate(giorni):
        h = ph * (g["passi"] / top)
        x0 = m["l"] + i * bw
        parts.append(f'<rect x="{x0 + bw * 0.12:.1f}" y="{m["t"] + ph - h:.1f}" width="{bw * 0.76:.1f}" '
                     f'height="{h:.1f}" fill="#0e9f8e"/>')
    parts.append("</svg>")
    return "".join(parts)


# -- alimentazione: ciambella, barre impilate, diario ----------------------------

C_CARBO, C_PROT, C_FAT = "#9a5f07", "#1c6fa0", "#a8407a"


def _quote_energia(t: dict):
    """Quota di energia da carboidrati/proteine/grassi (4-4-9 kcal/g), come nell'app."""
    if t.get("proteine") is None or t.get("grassi") is None:
        return None
    c, p, f = (t.get("carbo") or 0) * 4, t["proteine"] * 4, t["grassi"] * 9
    tot = c + p + f
    return (100 * c / tot, 100 * p / tot, 100 * f / tot) if tot > 0 else None


def _donut_svg(t: dict, size: int = 92) -> str:
    q = _quote_energia(t)
    r = 46
    C = 2 * 3.14159265 * r
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" width="{size}" height="{size}">',
             f'<circle cx="60" cy="60" r="{r}" fill="none" stroke="#e4e4e0" stroke-width="16"/>']
    if q:
        off = 0.0
        for colore, pct in zip((C_CARBO, C_PROT, C_FAT), q):
            ln = C * pct / 100
            parts.append(f'<circle cx="60" cy="60" r="{r}" fill="none" stroke="{colore}" stroke-width="16" '
                         f'stroke-dasharray="{ln:.2f} {C - ln:.2f}" stroke-dashoffset="{-off:.2f}" '
                         f'transform="rotate(-90 60 60)"/>')
            off += ln
    kcal = t.get("kcal")
    parts.append(f'<text x="60" y="62" text-anchor="middle" font-size="22" font-weight="700" fill="#17242d">'
                 f'{f"{kcal:.0f}" if kcal is not None else "–"}</text>'
                 f'<text x="60" y="80" text-anchor="middle" font-size="12" fill="#6b6b63">kcal</text></svg>')
    return "".join(parts)


def _stack_html(n: dict, carbo, larghezza_pct: float) -> str:
    """Barra orizzontale dell'energia da carboidrati/proteine/grassi."""
    if n.get("proteine") is None or n.get("grassi") is None:
        return ""
    segs = [(C_CARBO, (carbo or 0) * 4), (C_PROT, n["proteine"] * 4), (C_FAT, n["grassi"] * 9)]
    tot = sum(v for _, v in segs)
    if tot <= 0:
        return ""
    celle = "".join(f'<span style="display:block;height:7px;background:{c};width:{100 * v / tot:.1f}%"></span>'
                    for c, v in segs)
    return (f'<div style="display:flex;width:{max(6, larghezza_pct):.0f}%;height:7px;overflow:hidden;'
            f'border-radius:3px;margin-top:2px">{celle}</div>')


def _diet_chart_svg(righe: list[dict]) -> str:
    """Calorie al giorno per macronutriente (barre impilate), come nella scheda Andamento."""
    righe = [g for g in righe if g.get("proteine") is not None and g.get("grassi") is not None]
    if not righe:
        return ""
    W, H = 600, 110
    m = {"l": 40, "r": 8, "t": 6, "b": 16}
    pw, ph = W - m["l"] - m["r"], H - m["t"] - m["b"]
    segs = lambda g: [(C_CARBO, g["carbo"] * 4), (C_PROT, g["proteine"] * 4), (C_FAT, g["grassi"] * 9)]
    top = -(-max(500, max(sum(v for _, v in segs(g)) for g in righe)) // 500) * 500
    bw = pw / len(righe)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">']
    for ref in (0, top / 2, top):
        yy = m["t"] + ph * (1 - ref / top)
        parts.append(f'<line x1="{m["l"]}" x2="{m["l"] + pw}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#e4e4e0"/>')
        parts.append(f'<text x="{m["l"] - 4}" y="{yy + 3:.1f}" font-size="8" text-anchor="end" fill="#6b6b63">{ref:.0f}</text>')
    for i, g in enumerate(righe):
        y0 = m["t"] + ph
        for colore, v in segs(g):
            h = ph * v / top
            y0 -= h
            parts.append(f'<rect x="{m["l"] + i * bw + bw * 0.12:.1f}" y="{y0:.1f}" width="{bw * 0.76:.1f}" '
                         f'height="{h:.1f}" fill="{colore}"/>')
        if len(righe) <= 31:
            parts.append(f'<text x="{m["l"] + i * bw + bw / 2:.1f}" y="{H - 4}" font-size="7" text-anchor="middle" '
                         f'fill="#6b6b63">{g["giorno"][8:10]}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _legenda_html() -> str:
    return ("<p class='legend-line'><i class='sw' style='background:%s'></i>Carboidrati "
            "<i class='sw' style='background:%s'></i>Proteine <i class='sw' style='background:%s'></i>Grassi "
            "<span class='muted'>(quota di energia, 4-4-9 kcal/g)</span></p>") % (C_CARBO, C_PROT, C_FAT)


def _v(x, dec=0):
    return "–" if x is None else (f"{x:.{dec}f}" if abs(x) >= 10 or dec == 0 else f"{x:.1f}")


def _food_day_html(b: dict) -> str:
    esc = html.escape
    t = b["nutrienti"]
    pasti = [p for p in b["pasti"] if p["voci"]]
    r = b["riepilogo"]
    q = _quote_energia(t)
    pct = lambda i: f" · {q[i]:.0f}%" if q else ""
    kcal_max = max([p["nutrienti"].get("kcal") or 0 for p in pasti] + [1])
    barre = "".join(
        f"<div class='mbar'><span class='mbar-l'>{esc(p['tipo'])}</span>"
        f"<span class='mbar-b'>{_stack_html(p['nutrienti'], p['nutrienti'].get('carbo'), 100 * (p['nutrienti'].get('kcal') or 0) / kcal_max)}</span>"
        f"<span class='mbar-v'>{_v(p['nutrienti'].get('kcal'))}</span></div>"
        for p in pasti if p["nutrienti"].get("kcal") is not None) if len(pasti) > 1 else ""
    righe = []
    for p in pasti:
        n = p["nutrienti"]
        if p.get("in_attesa"):
            glic = "in attesa dei dati"
        else:
            glic = []
            if p.get("bolo_U"):
                glic.append(f"bolo {p['bolo_U']:.1f} U")
            if p.get("glicemia_inizio") is not None:
                glic.append(f"glicemia {p['glicemia_inizio']:.0f}"
                            + (f" → picco {p['picco']:.0f}" if p.get("picco") is not None else "") + " mg/dL")
            glic = " · ".join(glic) or "–"
        righe.append(
            f"<tr class='meal-row'><td><b>{esc(p['ora'])} {esc(p['tipo'])}</b></td><td></td>"
            f"<td>{_v(n.get('kcal'))}</td><td>{_v(n.get('carbo'), 1)}</td><td>{_v(n.get('proteine'), 1)}</td>"
            f"<td>{_v(n.get('grassi'), 1)}</td><td>{_v(n.get('zuccheri'), 1)}</td><td>{_v(n.get('fibra'), 1)}</td>"
            f"<td class='ins'>{esc(glic)}</td></tr>")
        mx = max([v["nutrienti"].get("kcal") or 0 for v in p["voci"]] + [1])
        for v in p["voci"]:
            vn = v["nutrienti"]
            barra = _stack_html(vn, v["carbo_g"], 100 * (vn.get("kcal") or 0) / mx)
            righe.append(
                f"<tr class='food-row'><td class='food-name'>{esc(v['descrizione'])}{' <i>(stima IA)</i>' if v['ia'] else ''}"
                f"{barra}</td><td>{_v(v['grammi'])}</td>"
                f"<td>{_v(vn.get('kcal'))}</td><td>{_v(v['carbo_g'], 1)}</td><td>{_v(vn.get('proteine'), 1)}</td>"
                f"<td>{_v(vn.get('grassi'), 1)}</td><td>{_v(vn.get('zuccheri'), 1)}</td><td>{_v(vn.get('fibra'), 1)}</td><td></td></tr>")
    ins = []
    if r.get("totale_U") is not None:
        ins.append(f"<span><b>{r['totale_U']:.1f} U</b> insulina totale</span>")
        if r.get("boli_U") is not None:
            ins.append(f"<span>{r['boli_U']:.1f} U boli · {(r.get('basale_U') or 0):.1f} U automatica</span>")
    if r.get("tir_pct") is not None:
        ins.append(f"<span>{r['tir_pct']:.0f}% nel range</span>")
    if r.get("passi") is not None:
        ins.append(f"<span>{r['passi']:.0f} passi</span>")
    parziale = ""
    sd = t.get("senza_dato") or {}
    mancanti = max([sd.get(k) or 0 for k in ("kcal", "proteine", "grassi")] + [0])
    if mancanti:
        parziale = f"<p class='muted small'>Per {mancanti} alimenti mancano alcuni valori nutrizionali: i totali sono parziali.</p>"
    return f"""
    <div class="food-day">
      <div class="fd-head"><b>{_fmt_g(b['giorno'])}</b><span class="fd-kcal">{_v(t.get('kcal'))} kcal</span></div>
      <div class="fd-top">
        <div class="fd-donut">{_donut_svg(t)}</div>
        <table class="fd-legend">
          <tr><td><i class="sw" style="background:{C_CARBO}"></i>Carboidrati</td><td><b>{_v(t.get('carbo'), 1)} g</b>{pct(0)}</td></tr>
          <tr><td><i class="sw" style="background:{C_PROT}"></i>Proteine</td><td><b>{_v(t.get('proteine'), 1)} g</b>{pct(1)}</td></tr>
          <tr><td><i class="sw" style="background:{C_FAT}"></i>Grassi</td><td><b>{_v(t.get('grassi'), 1)} g</b>{pct(2)}</td></tr>
          <tr><td>di cui zuccheri</td><td><b>{_v(t.get('zuccheri'), 1)} g</b></td></tr>
          <tr><td>Fibre</td><td><b>{_v(t.get('fibra'), 1)} g</b></td></tr>
        </table>
        <div class="fd-ins">{''.join(ins)}</div>
      </div>
      {parziale}
      {('<p class="small muted">Calorie per pasto</p>' + barre) if barre else ''}
      <table class="food-table">
        <thead><tr><th>Pasto / alimento</th><th>g</th><th>kcal</th><th>Carbo</th><th>Prot.</th><th>Grassi</th>
          <th>Zucch.</th><th>Fibre</th><th>Insulina e glicemia</th></tr></thead>
        <tbody>{''.join(righe)}</tbody>
      </table>
    </div>"""


def _diario_alimentare_html(data: dict) -> str:
    esc = html.escape
    prof = data["consigli"]["profilo"]
    med, giorni_cibo = prof["medie"], prof["giorni"]
    blocchi = [b for b in data["giorni_dettaglio"] if any(p["voci"] for p in b["pasti"])]
    if not blocchi:
        return "<h2>Diario alimentare</h2><p class='muted'>Nessun pasto registrato nel periodo.</p>"
    m = data["metriche"]
    quota = _quote_energia({"carbo": med["carbo"], "proteine": med["proteine"], "grassi": med["grassi"]}) \
        if med["proteine"] is not None and med["grassi"] is not None else None
    media_t = {"kcal": med["kcal"], "carbo": med["carbo"], "proteine": med["proteine"], "grassi": med["grassi"]}
    riga_giorni = "".join(
        f"<tr><td>{_fmt_g(g['giorno'])}</td><td>{g.get('pasti', '')}</td><td>{_v(g.get('kcal'))}</td>"
        f"<td>{_v(g.get('carbo'), 1)}</td><td>{_v(g.get('proteine'), 1)}</td><td>{_v(g.get('grassi'), 1)}</td>"
        f"<td>{_v(g.get('zuccheri'), 1)}</td><td>{_v(g.get('fibra'), 1)}</td>"
        f"<td>{_v(g['riepilogo'].get('totale_U'), 1)}</td>"
        f"<td>{_v(g['riepilogo'].get('tir_pct'))}{'%' if g['riepilogo'].get('tir_pct') is not None else ''}</td></tr>"
        for g in giorni_cibo)
    kpi = lambda lab, val, suf="": f"<div class='kpi'><dt>{lab}</dt><dd>{_v(val, 0 if val is not None and val >= 100 else 1)}{suf if val is not None else ''}</dd></div>"
    blocco_media = ""
    if giorni_cibo:
        blocco_media = f"""
    <h3>Media al giorno ({len(giorni_cibo)} {'giorno' if len(giorni_cibo) == 1 else 'giorni'} con pasti registrati)</h3>
    <div class="fd-top">
      <div class="fd-donut">{_donut_svg(media_t, 104)}</div>
      <div class="kpis kpis3">
        {kpi('Calorie', med['kcal'], ' kcal')}{kpi('Carboidrati', med['carbo'], ' g')}{kpi('Proteine', med['proteine'], ' g')}
        {kpi('Grassi', med['grassi'], ' g')}{kpi('Zuccheri', med['zuccheri'], ' g')}{kpi('Fibre', med['fibra'], ' g')}
        {kpi('Insulina totale', m.get('insulina_U'), ' U')}{kpi('Boli', m.get('bolo_U'), ' U')}{kpi('Insulina automatica', m.get('basale_U'), ' U')}
      </div>
    </div>
    {('<p class="small">Energia da carboidrati %.0f%% · proteine %.0f%% · grassi %.0f%%</p>' % quota) if quota else ''}
    {_legenda_html()}
    {_diet_chart_svg(giorni_cibo)}
    <table>
      <thead><tr><th>Giorno</th><th>Pasti</th><th>kcal</th><th>Carbo g</th><th>Prot. g</th><th>Grassi g</th>
        <th>Zucch. g</th><th>Fibre g</th><th>Insulina U</th><th>TIR</th></tr></thead>
      <tbody>{riga_giorni}</tbody>
    </table>"""
    giorni_html = "".join(_food_day_html(b) for b in blocchi)
    return f"""
    <h2>Diario alimentare</h2>
    <p class="muted">Per ogni giorno: calorie, carboidrati, proteine, grassi, zuccheri e fibre di ogni alimento e di ogni
      pasto, con l'insulina del giorno (boli e automatica) e la glicemia dopo il pasto. Valori nutrizionali
      indicativi: dove manca il dato compare "–" e i totali sono parziali.</p>
    {blocco_media}
    <h3 style="margin-top:12px">Giorno per giorno</h3>
    {_legenda_html()}
    {giorni_html}"""


def _notevoli_html(notevoli: list[dict]) -> str:
    esc = html.escape
    icone = {"ipo": "▼", "iper": "▲", "senza_dati": "○", "tir_basso": "◔", "migliore": "★"}
    etichette = {"ipo": "Ipoglicemia", "iper": "Iperglicemia sostenuta", "senza_dati": "Senza dati",
                 "tir_basso": "Tempo nel range basso", "migliore": "Giorno di riferimento (il migliore)"}
    if not notevoli:
        return "<p class='muted'>Nessun giorno notevole nel periodo: nessuna ipoglicemia grave, nessuna iperglicemia sostenuta.</p>"
    out = []
    for n in notevoli:
        rilevato = "".join(f"<li>{esc(r)}</li>" for r in n.get("rilevato") or [])
        note = f"<p class='nv-note'>Note del diario: {esc('; '.join(n['note']))}</p>" if n["note"] else ""
        out.append(
            f"<div class='notevole-card notevole-{n['tipo']}'>"
            f"<span class='notevole-icona'>{icone.get(n['tipo'], '•')}</span>"
            f"<div><b>{_fmt_g(n['giorno'])}</b> — {esc(etichette.get(n['tipo'], n['tipo']))}"
            f"<p>{esc(n['dettaglio'])}</p>"
            + (f"<p class='nv-title'>Cosa è stato rilevato</p><ul class='nv-list'>{rilevato}</ul>" if rilevato else "")
            + f"{note}</div></div>")
    return "".join(out)


def _consigli_html(consigli: dict) -> str:
    esc = html.escape
    etich = {"alta": "Priorità alta", "media": "Da considerare", "info": "Buona pratica"}
    sezioni = []
    for s in consigli["sezioni"]:
        voci = "".join(
            f"<div class='tip tip-{v['priorita']}'><div class='tip-head'><b>{esc(v['titolo'])}</b>"
            f"<span class='tip-badge'>{etich[v['priorita']]}</span></div>"
            f"<p>{esc(v['testo'])}</p>"
            + (f"<p class='tip-base'><b>Dai tuoi dati:</b> {esc(v['base'])}</p>" if v["base"] else "")
            + "</div>" for v in s["voci"])
        sezioni.append(f"<h3 class='tip-sec'>{esc(s['nome'])}</h3>{voci}")
    avviso = ("" if consigli["ha_dati_cibo"] else
              "<p class='muted'>Nel periodo non ci sono pasti con valori nutrizionali: i consigli sono quelli generali, "
              "senza riferimenti ai dati personali.</p>")
    return f"""
    <h2>Consigli per migliorare</h2>
    <p class="muted">Suggerimenti di alimentazione e stile di vita generati dai dati del periodo (alimenti, orari dei
      pasti, glicemia dopo i pasti, movimento). Sono indicazioni generali di buon senso nutrizionale:
      <b>non contengono dosi di insulina né modifiche della terapia</b>, che restano decisioni del diabetologo.
      Conviene discuterli con il diabetologo o con un dietista.</p>
    {avviso}
    {''.join(sezioni)}"""



def _week_grid_html(giorni: list[dict]) -> str:
    """Griglia settimanale di mini-giorni colorati per TIR (verde/ambra/rosso),
    grigio se sotto la soglia minima di copertura CGM."""
    if not giorni:
        return ""
    first = date.fromisoformat(giorni[0]["giorno"])
    pad = first.isoweekday() - 1  # lunedi' = 0
    cells = ["<span class='rwcell empty'></span>"] * pad
    for g in giorni:
        tir = g.get("tir_pct")
        copertura = g.get("copertura_cgm_pct") or 0
        if copertura < COVERAGE_MIN_PCT or tir is None:
            cls = "nodata"
        elif tir >= 70:
            cls = "good"
        elif tir >= 40:
            cls = "mid"
        else:
            cls = "low"
        giorno_num = date.fromisoformat(g["giorno"]).day
        cells.append(f"<span class='rwcell {cls}'>{giorno_num}</span>")
    return "<div class='rwgrid'>" + "".join(cells) + "</div>"


def _day_detail_html(block: dict, low: float, high: float) -> str:
    esc = html.escape
    r = block["riepilogo"]
    tir = r.get("tir_pct")
    media = r.get("media_mgdl")
    passi = r.get("passi")
    intestazione = (
        f"<span class='dd-tir {'nodata' if tir is None else ('good' if tir >= 70 else ('mid' if tir >= 40 else 'low'))}'>"
        f"{f'{tir:.0f}% nel range' if tir is not None else 'Senza dati'}</span>"
        + (f"<span>{media:.0f} mg/dL medi</span>" if media is not None else "")
        + (f"<span>{passi:.0f} passi</span>" if passi is not None else "")
    )
    if block["pasti"]:
        pasti_riga = " · ".join(
            f"{esc(p['ora'])} {esc(p['tipo'])}"
            + (f" ({p['carbo_dichiarati_g']:.0f} g"
               + (f", bolo {p['bolo_U']:.1f} U" if p.get('bolo_U') else "")
               + ")")
            for p in block["pasti"])
    else:
        pasti_riga = "<span class='muted'>Nessun pasto registrato.</span>"
    return f"""
    <div class="day-card">
      <div class="day-card-head">
        <b>{_fmt_g(block['giorno'])}</b> {intestazione}
      </div>
      {_day_svg(block['glicemie'], block['voci'], low, high)}
      <p class="day-card-meals">{pasti_riga}</p>
    </div>"""


def _font_face_css() -> str:
    fonts_dir = (STATIC_DIR / "fonts").resolve().as_uri()
    return f"""
    @font-face {{ font-family: "Atkinson"; font-weight: 400;
      src: url("{fonts_dir}/atkinson-hyperlegible-latin-400-normal.woff2") format("woff2"); }}
    @font-face {{ font-family: "Atkinson"; font-weight: 700;
      src: url("{fonts_dir}/atkinson-hyperlegible-latin-700-normal.woff2") format("woff2"); }}
    @font-face {{ font-family: "SpaceGrotesk"; font-weight: 700;
      src: url("{fonts_dir}/space-grotesk-700.woff2") format("woff2"); }}
    """


def _fmt_g(giorno_iso: str) -> str:
    return date.fromisoformat(giorno_iso).strftime("%d/%m/%Y")


def render_html(data: dict, soglie: dict) -> str:
    m = data["metriche"]
    low, high = soglie.get("bassa", 70), soglie.get("alta", 180)
    titolo_periodo = f"{_fmt_g(data['day_from'].isoformat())} – {_fmt_g(data['day_to'].isoformat())}"

    def pct(v):
        return f"{v:.0f}%" if v is not None else "–"

    def num(v, suffix=""):
        return f"{v:g}{suffix}" if v is not None else "–"

    barre = [
        ("sotto_pct", "TBR <70", m.get("sotto_pct"), CONSENSUS["sotto_pct"], "<"),
        ("molto_sotto_pct", "TBR <54", m.get("molto_sotto_pct"), CONSENSUS["molto_sotto_pct"], "<"),
        ("tir_pct", "TIR 70-180", m.get("tir_pct"), CONSENSUS["tir_pct"], ">"),
        ("molto_sopra_pct", "TAR >250", m.get("molto_sopra_pct"), CONSENSUS["molto_sopra_pct"], "<"),
    ]
    barre_html = "".join(
        f"<div class='metbar'><span class='mb-label'>{label}</span>"
        f"<span class='mb-val'>{pct(val)}</span>"
        f"<span class='mb-target'>obiettivo {op}{target}%</span></div>"
        for _, label, val, target, op in barre
    )

    esc = html.escape

    notevoli_html = _notevoli_html(data['notevoli'])

    giorni_dettaglio_html = "".join(_day_detail_html(b, low, high) for b in data["giorni_dettaglio"])

    passi = data["passi"]
    settimane_html = "".join(
        f"<tr><td>{_fmt_g(s['da'])} – {_fmt_g(s['a'])}</td><td>{num(s['totale'])}</td>"
        f"<td>{s['giorni_con_dati']}</td></tr>"
        for s in passi["per_settimana"]
    ) or "<tr><td colspan='3' class='muted'>Nessun dato sui passi nel periodo.</td></tr>"

    domande = (f"<div class='visit-questions'><h3>Domande per la visita</h3>"
               f"<p>{esc(data['domande_visita'])}</p></div>") if data["domande_visita"] else ""

    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<style>
{_font_face_css()}
* {{ box-sizing: border-box; }}
body {{ font-family: "Atkinson", sans-serif; font-size: 11px; color: #17242d; margin: 0; }}
h2, h3 {{ page-break-after: avoid; }}
h1, h2, h3 {{ font-family: "SpaceGrotesk", sans-serif; margin: 0 0 6px; }}
h1 {{ font-size: 20px; }} h2 {{ font-size: 15px; margin-top: 18px; }} h3 {{ font-size: 13px; }}
@page {{ size: A4; margin: 16mm 14mm 20mm; @bottom-center {{ content: element(footer); }} }}
.footer {{ position: running(footer); font-size: 8.5px; color: #6b6b63; text-align: center; }}
.header {{ display: flex; justify-content: space-between; align-items: baseline;
  border-bottom: 2px solid #17242d; padding-bottom: 6px; margin-bottom: 10px; }}
.kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 10px 0; }}
.kpi {{ border: 1px solid #e4e4e0; border-radius: 6px; padding: 6px 8px; }}
.kpi dt {{ font-size: 9.5px; color: #6b6b63; }}
.kpi dd {{ margin: 0; font-family: "SpaceGrotesk", sans-serif; font-size: 17px; font-weight: 700; }}
.metbars {{ margin: 10px 0; }}
.metbar {{ display: flex; align-items: baseline; gap: 8px; padding: 3px 0; border-bottom: 1px solid #f0f0ec; }}
.mb-label {{ width: 90px; font-weight: 700; }}
.mb-val {{ width: 50px; font-family: "SpaceGrotesk", sans-serif; }}
.mb-target {{ color: #6b6b63; font-size: 9.5px; }}
.rwgrid {{ display: flex; flex-wrap: wrap; gap: 3px; margin: 8px 0; }}
.rwcell {{ width: 20px; height: 20px; border-radius: 4px; font-size: 8.5px;
  display: flex; align-items: center; justify-content: center; color: #fff; }}
.rwcell.empty {{ visibility: hidden; }}
.rwcell.good {{ background: #0e9f8e; }} .rwcell.mid {{ background: #c98a1f; }}
.rwcell.low {{ background: #c5433f; }} .rwcell.nodata {{ background: #cfcfc7; color: #6b6b63; }}
table {{ width: 100%; border-collapse: collapse; font-size: 9.5px; }}
th, td {{ text-align: left; border-bottom: 1px solid #e4e4e0; padding: 3px 4px; }}
th {{ color: #6b6b63; font-weight: 700; }}
tr.in-attesa td {{ color: #6b6b63; font-style: italic; }}
.muted {{ color: #6b6b63; }}
.visit-questions {{ margin-top: 14px; border-top: 1px solid #e4e4e0; padding-top: 8px; }}
.disclaimer {{ margin-top: 2px; }}
.day-card {{ border: 1px solid #e4e4e0; border-radius: 6px; padding: 6px 8px; margin-bottom: 8px;
  page-break-inside: avoid; }}
.day-card-head {{ display: flex; align-items: baseline; gap: 10px; font-size: 10px; margin-bottom: 2px; }}
.day-card-head b {{ font-family: "SpaceGrotesk", sans-serif; }}
.dd-tir {{ font-weight: 700; padding: 1px 6px; border-radius: 999px; font-size: 9px; color: #fff; }}
.dd-tir.good {{ background: #0e9f8e; }} .dd-tir.mid {{ background: #c98a1f; }}
.dd-tir.low {{ background: #c5433f; }} .dd-tir.nodata {{ background: #cfcfc7; color: #6b6b63; }}
.day-card-meals {{ font-size: 9px; color: #42423c; margin: 2px 0 0; }}
.notevole-card {{ display: flex; gap: 8px; align-items: flex-start; border-radius: 6px;
  padding: 6px 8px; margin-bottom: 6px; border-left: 4px solid #cfcfc7; background: #f7f7f3; }}
.notevole-card p {{ margin: 2px 0 0; color: #42423c; }}
.notevole-icona {{ font-size: 13px; line-height: 1.3; }}
.notevole-ipo {{ border-left-color: #c5433f; }} .notevole-ipo .notevole-icona {{ color: #c5433f; }}
.notevole-iper {{ border-left-color: #c98a1f; }} .notevole-iper .notevole-icona {{ color: #c98a1f; }}
.notevole-senza_dati {{ border-left-color: #9a9a90; }} .notevole-senza_dati .notevole-icona {{ color: #9a9a90; }}
.small {{ font-size: 9px; }} .legend-line {{ font-size: 9px; margin: 4px 0; }}
.sw {{ display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin: 0 3px 0 6px; }}
.kpis3 {{ grid-template-columns: repeat(3, 1fr); flex: 1; margin: 0; }}
.food-day {{ border: 1px solid #e4e4e0; border-radius: 6px; padding: 6px 8px; margin-bottom: 10px; page-break-inside: avoid; }}
.fd-head {{ display: flex; justify-content: space-between; align-items: baseline; border-bottom: 1px solid #e4e4e0; padding-bottom: 3px; margin-bottom: 4px; }}
.fd-head b {{ font-family: "SpaceGrotesk", sans-serif; font-size: 12px; }}
.fd-kcal {{ font-family: "SpaceGrotesk", sans-serif; font-size: 14px; font-weight: 700; }}
.fd-top {{ display: flex; gap: 12px; align-items: center; }}
.fd-legend {{ width: auto; flex: 0 0 170px; }} .fd-legend td {{ border: 0; padding: 1px 4px; }}
.fd-ins {{ display: flex; flex-direction: column; gap: 2px; font-size: 9.5px; flex: 1; }}
.mbar {{ display: flex; align-items: center; gap: 6px; font-size: 9px; margin: 2px 0; }}
.mbar-l {{ width: 70px; }} .mbar-b {{ flex: 1; }} .mbar-v {{ width: 36px; text-align: right; font-weight: 700; }}
.food-table {{ margin-top: 6px; }} .food-table th, .food-table td {{ padding: 2px 3px; font-size: 8.5px; }}
.food-table td:nth-child(n+2):nth-child(-n+8), .food-table th:nth-child(n+2):nth-child(-n+8) {{ text-align: right; }}
tr.meal-row td {{ background: #f1f1ec; border-top: 1px solid #cfcfc7; }}
tr.food-row td {{ color: #42423c; }} .food-name i {{ color: #6b6b63; font-size: 8px; }}
td.ins {{ font-size: 8px; color: #42423c; }}
.nv-title {{ font-weight: 700; margin: 4px 0 1px !important; color: #17242d !important; }}
.nv-list {{ margin: 0; padding-left: 14px; color: #42423c; }} .nv-list li {{ margin: 1px 0; }}
.nv-note {{ font-style: italic; }}
.notevole-tir_basso {{ border-left-color: #c98a1f; }} .notevole-tir_basso .notevole-icona {{ color: #c98a1f; }}
.notevole-migliore {{ border-left-color: #0e9f8e; }} .notevole-migliore .notevole-icona {{ color: #0e9f8e; }}
.notevole-card {{ page-break-inside: avoid; }}
.tip-sec {{ margin-top: 12px; border-bottom: 1px solid #cfcfc7; padding-bottom: 2px; }}
.tip {{ border-left: 4px solid #cfcfc7; background: #f7f7f3; border-radius: 6px; padding: 5px 8px; margin: 5px 0; page-break-inside: avoid; }}
.tip p {{ margin: 2px 0 0; color: #42423c; }} .tip-base {{ color: #17242d !important; }}
.tip-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }}
.tip-badge {{ font-size: 8px; padding: 1px 6px; border-radius: 999px; background: #cfcfc7; color: #17242d; white-space: nowrap; }}
.tip-alta {{ border-left-color: #c5433f; }} .tip-alta .tip-badge {{ background: #c5433f; color: #fff; }}
.tip-media {{ border-left-color: #c98a1f; }} .tip-media .tip-badge {{ background: #c98a1f; color: #fff; }}
</style></head>
<body>
<div class="footer disclaimer">{DISCLAIMER}</div>

<div class="header">
  <div><h1>Diario CamAPS — Report</h1><span class="muted">Periodo: {titolo_periodo}</span></div>
  <div class="muted">Generato il {data['generato'].strftime('%d/%m/%Y %H:%M')}</div>
</div>

<div class="kpis">
  <div class="kpi"><dt>Glicemia media</dt><dd>{num(m['media_mgdl'], ' mg/dL')}</dd></div>
  <div class="kpi"><dt>GMI</dt><dd>{num(m['gmi_pct'], '%')}</dd></div>
  <div class="kpi"><dt>CV</dt><dd>{num(m['cv_pct'], '%')}</dd></div>
  <div class="kpi"><dt>Giorni con dati</dt><dd>{m['giorni_con_dati']}/{m['giorni_totali']}</dd></div>
</div>

<div class="metbars">{barre_html}</div>

<h2>Profilo giornaliero tipico (AGP)</h2>
{_agp_svg(data['agp'], low, high)}

<h2>Insulina e carboidrati (media al giorno)</h2>
<div class="kpis">
  <div class="kpi"><dt>Insulina totale</dt><dd>{num(m['insulina_U'], ' U')}</dd></div>
  <div class="kpi"><dt>di cui automatica</dt><dd>{num(m['basale_U'], ' U')}</dd></div>
  <div class="kpi"><dt>Carboidrati mangiati</dt><dd>{num(m['carbo_mangiati_g'], ' g')}</dd></div>
  <div class="kpi"><dt>Carboidrati in CamAPS</dt><dd>{num(m['carbo_camaps_g'], ' g')}</dd></div>
</div>

<h2>Passi</h2>
<div class="kpis">
  <div class="kpi"><dt>Totale nel periodo</dt><dd>{num(passi['totale_periodo'])}</dd></div>
  <div class="kpi"><dt>Media al giorno</dt><dd>{num(passi['media_giorno'])}</dd></div>
  <div class="kpi"><dt>Totale anno {passi['anno_in_corso']['anno']}</dt>
    <dd>{num(passi['anno_in_corso']['totale'])}<span class="muted" style="font-size:9px;font-weight:400">
      su {passi['anno_in_corso']['giorni_con_dati']} giorni con dati</span></dd></div>
</div>
{_steps_svg(passi['giorni'])}
<table>
<thead><tr><th>Settimana</th><th>Totale passi</th><th>Giorni con dati</th></tr></thead>
<tbody>{settimane_html}</tbody>
</table>

<h2>Giorni del periodo</h2>
{_week_grid_html(data['giorni'])}

<div style="page-break-before: always;"></div>
<h2>Dettaglio giorno per giorno</h2>
<p class="muted">Per ogni giorno: tempo nel range, glicemia media, passi, il grafico della
  giornata con boli (▲) e carboidrati senza bolo (●), e i pasti registrati con i grammi
  dichiarati e il bolo collegato.</p>
{giorni_dettaglio_html}

<div style="page-break-before: always;"></div>
{_diario_alimentare_html(data)}

<div style="page-break-before: always;"></div>
<h2>Giorni notevoli</h2>
<p class="muted">Giorni che si discostano dal resto del periodo, con ciò che l'analisi dei dati ha rilevato
  (glicemia, boli, pasti, movimento). Sono osservazioni descrittive, non giudizi né indicazioni di terapia.</p>
{notevoli_html}

<div style="page-break-before: always;"></div>
{_consigli_html(data['consigli'])}

{domande}
</body></html>"""


def render_pdf_bytes(data: dict, soglie: dict) -> bytes:
    from weasyprint import HTML
    html = render_html(data, soglie)
    return HTML(string=html, base_url=str(STATIC_DIR)).write_pdf()


# -- orchestrazione e persistenza -------------------------------------------------

def generate(store: Store, reports_dir: Path, day_from: date, day_to: date, kind: str,
             soglie: dict, domande_visita: str = "") -> dict:
    data = build_report_data(store, day_from, day_to, kind, domande_visita)
    pdf = render_pdf_bytes(data, soglie)
    reports_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.pdf"
    (reports_dir / filename).write_bytes(pdf)
    created = now_local()
    cur = store.db.execute(
        "INSERT INTO report(created_utc, day_from, day_to, kind, filename) VALUES (?, ?, ?, ?, ?)",
        (created.isoformat(), day_from.isoformat(), day_to.isoformat(), kind, filename))
    store.db.commit()
    if (domande_visita or "").strip():
        store.set("domande_visita", "")
    return {"id": cur.lastrowid, "created": created.isoformat(),
            "day_from": day_from.isoformat(), "day_to": day_to.isoformat(),
            "kind": kind, "filename": filename}


def get_report(store: Store, report_id: int) -> dict | None:
    row = store.db.execute("SELECT * FROM report WHERE id = ?", (report_id,)).fetchone()
    return dict(row) if row else None


def list_reports(store: Store, limit: int = 20) -> list[dict]:
    rows = store.db.execute("SELECT * FROM report ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]
