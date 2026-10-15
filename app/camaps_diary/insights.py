"""Insight settimanali (Fase 5): frasi semplici generate da template coi
numeri della settimana appena conclusa, mai una raccomandazione di dose o di
terapia. Regole deterministiche, nessuna IA coinvolta."""

from datetime import date, timedelta

from . import report
from .analysis import day_payload, period_summary
from .store import Store

MEAL_LABELS = report.MEAL_LABELS

# Fasce orarie usate per individuare quando si concentrano iper/ipo: non
# corrispondono esattamente ai pasti (l'orario dei pasti e' libero), ma danno
# un riferimento di lettura immediato.
FASCE = (
    ("notte", 23, 6),
    ("mattina", 6, 11),
    ("dopo pranzo", 11, 15),
    ("pomeriggio", 15, 19),
    ("dopo cena", 19, 23),
)


def _fascia_di(ora: int) -> str:
    for nome, a, b in FASCE:
        if a < b:
            if a <= ora < b:
                return nome
        elif ora >= a or ora < b:  # fascia che attraversa la mezzanotte (notte)
            return nome
    return "altro"


def ultima_domenica(oggi: date) -> date:
    """La domenica piu' recente (oggi compreso se oggi e' domenica): la
    settimana di riferimento degli insight e' sempre una settimana intera."""
    return oggi - timedelta(days=(oggi.weekday() + 1) % 7)


def _fasce_critiche(store: Store, day_from: date, day_to: date, soglia_bassa: float, soglia_alta: float):
    giorni_iper = {nome: 0 for nome, _, _ in FASCE}
    giorni_ipo = {nome: 0 for nome, _, _ in FASCE}
    n_giorni_con_dati = 0
    d = day_from
    while d <= day_to:
        rows = list(store.db.execute(
            "SELECT ts_local, mgdl FROM glucose WHERE ts_local >= ? AND ts_local < ?",
            (f"{d.isoformat()} 00:00:00", f"{(d + timedelta(days=1)).isoformat()} 00:00:00")))
        if rows:
            n_giorni_con_dati += 1
            fasce_iper_oggi, fasce_ipo_oggi = set(), set()
            for r in rows:
                fascia = _fascia_di(int(r["ts_local"][11:13]))
                if r["mgdl"] > soglia_alta:
                    fasce_iper_oggi.add(fascia)
                if r["mgdl"] < soglia_bassa:
                    fasce_ipo_oggi.add(fascia)
            for f in fasce_iper_oggi:
                giorni_iper[f] += 1
            for f in fasce_ipo_oggi:
                giorni_ipo[f] += 1
        d += timedelta(days=1)
    return giorni_iper, giorni_ipo, n_giorni_con_dati


def _pasti_settimana(store: Store, day_from: date, day_to: date) -> list[dict]:
    pasti = []
    d = day_from
    while d <= day_to:
        payload = day_payload(store, d)
        for m in payload["pasti"]:
            a = m.get("analisi") or {}
            if a.get("in_attesa") or a.get("ipo"):
                continue
            inizio, picco = a.get("glicemia_inizio"), a.get("picco")
            if inizio is None or picco is None:
                continue
            pasti.append({
                "giorno": payload["giorno"], "ora": m["ora"][11:16], "tipo": m["tipo"],
                "glicemia_inizio": inizio, "picco": picco, "salita": picco - inizio,
            })
        d += timedelta(days=1)
    return pasti


def _boli_senza_pasto(store: Store, day_from: date, day_to: date) -> int:
    n = 0
    d = day_from
    while d <= day_to:
        payload = day_payload(store, d)
        n += sum(1 for v in payload["voci"] if v["tipo"] == "bolo" and v.get("pasto_id") is None)
        d += timedelta(days=1)
    return n


def _pasti_senza_foto(store: Store, day_from: date, day_to: date) -> tuple[int, int]:
    d, n_tot, n_senza = day_from, 0, 0
    while d <= day_to:
        payload = day_payload(store, d)
        for m in payload["pasti"]:
            if m["tipo"] == "ipoglicemia":
                continue
            n_tot += 1
            if not m.get("foto"):
                n_senza += 1
        d += timedelta(days=1)
    return n_senza, n_tot


def _passi_tir(giorni: list[dict]) -> dict | None:
    """Confronto descrittivo (non causale) tra i giorni con piu' e meno passi."""
    con_dati = [g for g in giorni if g.get("passi") is not None and g.get("tir_pct") is not None]
    if len(con_dati) < 4:
        return None
    ordinati = sorted(con_dati, key=lambda g: g["passi"])
    meta = len(ordinati) // 2
    bassi, alti = ordinati[:meta], ordinati[-meta:]
    return {
        "tir_pochi_passi": round(sum(g["tir_pct"] for g in bassi) / len(bassi), 1),
        "tir_molti_passi": round(sum(g["tir_pct"] for g in alti) / len(alti), 1),
    }


def _genera_frasi(m: dict, m_prec: dict, giorni_iper: dict, giorni_ipo: dict, n_giorni_cgm: int,
                   pasti: list[dict], boli_liberi: int, pasti_senza_foto: int, pasti_tot: int,
                   passi_tir: dict | None, giorni: list[dict],
                   soglia_bassa: float, soglia_alta: float) -> list[str]:
    frasi = []

    if m["tir_pct"] is not None and m_prec["tir_pct"] is not None:
        diff = round(m["tir_pct"] - m_prec["tir_pct"], 1)
        if abs(diff) >= 1:
            frasi.append(
                f"Tempo nel range {'in aumento' if diff > 0 else 'in calo'} di {abs(diff):.0f} punti "
                f"rispetto alla settimana prima ({m_prec['tir_pct']:.0f}% → {m['tir_pct']:.0f}%).")
        else:
            frasi.append(f"Tempo nel range stabile, {m['tir_pct']:.0f}% (era {m_prec['tir_pct']:.0f}%).")
    elif m["tir_pct"] is not None:
        frasi.append(f"Tempo nel range di questa settimana: {m['tir_pct']:.0f}%. "
                     "Non ci sono dati della settimana precedente per il confronto.")

    if m["media_mgdl"] is not None and m_prec["media_mgdl"] is not None:
        diff = round(m["media_mgdl"] - m_prec["media_mgdl"])
        if abs(diff) >= 3:
            frasi.append(
                f"Glicemia media {'più alta' if diff > 0 else 'più bassa'} di {abs(diff)} mg/dL "
                f"rispetto alla settimana prima ({m_prec['media_mgdl']} → {m['media_mgdl']} mg/dL).")

    if m["cv_pct"] is not None and m_prec["cv_pct"] is not None:
        diff = round(m["cv_pct"] - m_prec["cv_pct"], 1)
        if abs(diff) >= 2:
            frasi.append(
                f"Variabilità (CV) {'aumentata' if diff > 0 else 'diminuita'} di {abs(diff):.0f} punti "
                f"({m_prec['cv_pct']:.0f}% → {m['cv_pct']:.0f}%).")

    if n_giorni_cgm:
        top_iper = max(giorni_iper.items(), key=lambda kv: kv[1])
        if top_iper[1] >= 3:
            frasi.append(
                f"Iperglicemia più frequente nella fascia \"{top_iper[0]}\": {top_iper[1]} giorni su "
                f"{n_giorni_cgm} con almeno una lettura sopra {soglia_alta:.0f} mg/dL in quella fascia.")
        top_ipo = max(giorni_ipo.items(), key=lambda kv: kv[1])
        if top_ipo[1] >= 2:
            frasi.append(
                f"Ipoglicemia più frequente nella fascia \"{top_ipo[0]}\": {top_ipo[1]} giorni su "
                f"{n_giorni_cgm} con almeno una lettura sotto {soglia_bassa:.0f} mg/dL in quella fascia.")

    if pasti:
        peggiore = max(pasti, key=lambda p: p["salita"])
        migliore = min(pasti, key=lambda p: p["salita"])
        frasi.append(
            f"Salita maggiore dopo il pasto delle {peggiore['ora']} del {report._fmt_g(peggiore['giorno'])} "
            f"({MEAL_LABELS.get(peggiore['tipo'], peggiore['tipo'])}): da {peggiore['glicemia_inizio']} "
            f"a {peggiore['picco']} mg/dL.")
        if migliore["salita"] != peggiore["salita"]:
            frasi.append(
                f"Risposta più contenuta dopo il pasto delle {migliore['ora']} del "
                f"{report._fmt_g(migliore['giorno'])} ({MEAL_LABELS.get(migliore['tipo'], migliore['tipo'])}): "
                f"da {migliore['glicemia_inizio']} a {migliore['picco']} mg/dL.")

    giorni_senza_dati = sum(1 for g in giorni if not g["letture_cgm"])
    if giorni_senza_dati:
        frasi.append(f"{giorni_senza_dati} giorni senza dati dal sensore questa settimana.")
    if pasti_tot and pasti_senza_foto:
        frasi.append(f"{pasti_senza_foto} pasti su {pasti_tot} registrati senza foto.")
    if boli_liberi:
        frasi.append(f"{boli_liberi} {'bolo' if boli_liberi == 1 else 'boli'} del microinfusore "
                     "senza un pasto collegato nel diario.")

    if passi_tir:
        diff = round(passi_tir["tir_molti_passi"] - passi_tir["tir_pochi_passi"], 1)
        if abs(diff) >= 3:
            frasi.append(
                f"Nei giorni con più passi il tempo nel range è stato {'più alto' if diff > 0 else 'più basso'} "
                f"di circa {abs(diff):.0f} punti rispetto ai giorni con meno passi "
                "(solo descrittivo, non una relazione causale).")

    if not frasi:
        frasi.append("Non ci sono ancora abbastanza dati questa settimana per un riepilogo.")
    return frasi


def weekly_insights(store: Store, week_end: date) -> dict:
    day_to = week_end
    day_from = day_to - timedelta(days=6)
    prev_to = day_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=6)

    giorni = period_summary(store, day_from, day_to)
    giorni_prec = period_summary(store, prev_from, prev_to)
    m = report.period_metrics(giorni)
    m_prec = report.period_metrics(giorni_prec)

    soglia_bassa = float(store.get("soglia_bassa", 70))
    soglia_alta = float(store.get("soglia_alta", 180))
    giorni_iper, giorni_ipo, n_giorni_cgm = _fasce_critiche(store, day_from, day_to, soglia_bassa, soglia_alta)
    pasti = _pasti_settimana(store, day_from, day_to)
    boli_liberi = _boli_senza_pasto(store, day_from, day_to)
    pasti_senza_foto, pasti_tot = _pasti_senza_foto(store, day_from, day_to)
    passi_tir = _passi_tir(giorni)

    frasi = _genera_frasi(m, m_prec, giorni_iper, giorni_ipo, n_giorni_cgm, pasti, boli_liberi,
                          pasti_senza_foto, pasti_tot, passi_tir, giorni, soglia_bassa, soglia_alta)

    return {
        "settimana_da": day_from.isoformat(), "settimana_a": day_to.isoformat(),
        "settimana_precedente_da": prev_from.isoformat(), "settimana_precedente_a": prev_to.isoformat(),
        "metriche": m, "metriche_precedenti": m_prec,
        "frasi": frasi,
    }
