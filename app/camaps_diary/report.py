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


# -- tabella pasti --------------------------------------------------------------

def meal_rows(store: Store, day_from: date, day_to: date) -> list[dict]:
    rows = []
    d = day_from
    while d <= day_to:
        payload = day_payload(store, d)
        for m in payload["pasti"]:
            a = m.get("analisi") or {}
            if a.get("in_attesa"):
                continue
            alimenti = ", ".join(v["descrizione"] for v in m["voci"]) if m["voci"] else "—"
            camaps_g = a.get("carbo_ipo_camaps_g") if a.get("ipo") else a.get("carbo_dichiarati_g")
            rows.append({
                "giorno": payload["giorno"], "ora": m["ora"][11:16],
                "tipo": MEAL_LABELS.get(m["tipo"], m["tipo"]),
                "alimenti": alimenti,
                "carbo_dichiarati_g": m["carbo_g"],
                "carbo_camaps_g": camaps_g,
                "bolo_U": a.get("bolo_U"),
                "picco_2h": a.get("glicemia_2h"),
                "foto_id": m["foto"][0]["id"] if m.get("foto") else None,
            })
        d += timedelta(days=1)
    return rows


# -- raccolta dati completa ------------------------------------------------------

def build_report_data(store: Store, day_from: date, day_to: date, kind: str,
                       domande_visita: str = "") -> dict:
    from .analysis import period_summary
    giorni = period_summary(store, day_from, day_to)
    return {
        "generato": now_local(),
        "day_from": day_from, "day_to": day_to, "kind": kind,
        "giorni": giorni,
        "metriche": period_metrics(giorni),
        "agp": agp_profile(store, day_from, day_to),
        "notevoli": notable_days(store, giorni),
        "pasti": meal_rows(store, day_from, day_to),
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

    notevoli_html = "".join(
        f"<li><b>{_fmt_g(n['giorno'])}</b> — {esc(n['dettaglio'])}"
        f"{' · ' + esc('; '.join(n['note'])) if n['note'] else ''}</li>"
        for n in data["notevoli"]
    ) or "<li class='muted'>Nessun giorno notevole nel periodo.</li>"

    pasti_html = "".join(
        f"<tr><td>{_fmt_g(p['giorno'])}</td><td>{esc(p['ora'])}</td><td>{esc(p['tipo'])}</td>"
        f"<td>{esc(p['alimenti'])}</td><td>{num(p['carbo_dichiarati_g'])}</td>"
        f"<td>{num(p['carbo_camaps_g'])}</td><td>{num(p['bolo_U'])}</td><td>{num(p['picco_2h'])}</td></tr>"
        for p in data["pasti"]
    ) or "<tr><td colspan='8' class='muted'>Nessun pasto registrato nel periodo.</td></tr>"

    domande = (f"<div class='visit-questions'><h3>Domande per la visita</h3>"
               f"<p>{esc(data['domande_visita'])}</p></div>") if data["domande_visita"] else ""

    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<style>
{_font_face_css()}
* {{ box-sizing: border-box; }}
body {{ font-family: "Atkinson", sans-serif; font-size: 11px; color: #17242d; margin: 0; }}
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
ul.notevoli {{ margin: 4px 0; padding-left: 16px; }}
.muted {{ color: #6b6b63; }}
.visit-questions {{ margin-top: 14px; border-top: 1px solid #e4e4e0; padding-top: 8px; }}
.disclaimer {{ margin-top: 2px; }}
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

<h2>Giorni del periodo</h2>
{_week_grid_html(data['giorni'])}

<div style="page-break-before: always;"></div>
<h2>Pasti del periodo</h2>
<table>
<thead><tr><th>Giorno</th><th>Ora</th><th>Tipo</th><th>Alimenti</th>
  <th>Dichiarati</th><th>CamAPS</th><th>Bolo U</th><th>A 2h</th></tr></thead>
<tbody>{pasti_html}</tbody>
</table>

<h2>Giorni notevoli</h2>
<ul class="notevoli">{notevoli_html}</ul>

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
