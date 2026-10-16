"""Analisi descrittive per il report PDF: dettaglio dei giorni notevoli e consigli.

Vincolo del progetto (non negoziabile): nessuna indicazione di dose, di rapporti
insulina/carboidrati o di terapia. Qui ci sono solo osservazioni sui dati del
periodo e suggerimenti alimentari/di stile di vita di carattere generale, da
discutere con diabetologo e dietista. Ogni osservazione nasce da numeri presenti
nel diario: se il dato manca, l'osservazione non viene scritta.
"""

from statistics import mean, median

MEAL_LABELS = {
    "colazione": "Colazione", "spuntino": "Spuntino", "pranzo": "Pranzo",
    "merenda": "Merenda", "cena": "Cena", "fuori_pasto": "Fuori pasto", "ipoglicemia": "Correzione ipo",
}

# riferimenti alimentari generali (linee guida di nutrizione per la popolazione)
GRASSI_ENERGIA_MAX_PCT = 35
ZUCCHERI_ENERGIA_MAX_PCT = 15
FIBRE_G_MIN = 25
PROTEINE_ENERGIA_MIN_PCT = 12
PASTO_GRASSO_G = 25
PASSI_ATTIVO = 7000


def hhmm(minuti: float) -> str:
    minuti = int(round(minuti)) % 1440
    return f"{minuti // 60:02d}:{minuti % 60:02d}"


def _durata(minuti: float) -> str:
    minuti = int(round(minuti))
    return f"{minuti // 60} h {minuti % 60:02d} min" if minuti >= 60 else f"{minuti} min"


def _fmt_g(giorno_iso: str) -> str:
    a, m, g = giorno_iso.split("-")
    return f"{g}/{m}/{a}"


def _g(v) -> str:
    return f"{v:.0f}" if abs(v) >= 10 else f"{v:.1f}"


def _min_pasto(p: dict) -> int:
    return int(p["ora"][:2]) * 60 + int(p["ora"][3:5])


def _episodi(glicemie: list, soglia: float, gap_max: float = 15, sopra: bool = False) -> list[dict]:
    """Tratti consecutivi di letture sotto (o sopra) soglia."""
    out, cur = [], None
    for mm, v in glicemie:
        ok = v > soglia if sopra else v < soglia
        if ok and cur is not None and mm - cur["fine"] <= gap_max:
            cur["fine"] = mm
            cur["n"] += 1
            if (v > cur["estremo"]) if sopra else (v < cur["estremo"]):
                cur["estremo"], cur["estremo_min"] = v, mm
        elif ok:
            cur = {"inizio": mm, "fine": mm, "n": 1, "estremo": v, "estremo_min": mm}
            out.append(cur)
        else:
            cur = None
    return out


def _pasti_alimentari(blocco: dict) -> list[dict]:
    return [p for p in blocco["pasti"] if p.get("tipo_key") != "ipoglicemia"]


def _descrizione_pasto(p: dict) -> str:
    n = p.get("nutrienti") or {}
    s = f"{p['tipo']} alle {p['ora']} ({_g(p['carbo_dichiarati_g'])} g di carboidrati"
    if n.get("grassi") is not None:
        s += f", grassi {_g(n['grassi'])} g"
    return s + ")"


# -- giorni notevoli: cosa e' stato rilevato ------------------------------------

def _rilevato_ipo(blocco, ep_list, passi_medi) -> list[str]:
    out = []
    glic, voci = blocco["glicemie"], blocco["voci"]
    for ep in ep_list:
        durata = max(ep["fine"] - ep["inizio"], 5)
        riga = (f"Minimo {ep['estremo']:.0f} mg/dL alle {hhmm(ep['estremo_min'])}; "
                f"circa {_durata(durata)} sotto 54 mg/dL (dalle {hhmm(ep['inizio'])} alle {hhmm(ep['fine'])}).")
        if ep["inizio"] < 360:
            riga += " Episodio notturno."
        out.append(riga)
        boli = [v for v in voci if v["tipo"] == "bolo" and ep["inizio"] - 240 <= v["min"] <= ep["inizio"]]
        if boli:
            b = boli[-1]
            out.append(f"Ultimo bolo prima dell'episodio: {b['U']:g} U alle {b['ora']}, "
                       f"{_durata(ep['inizio'] - b['min'])} prima (carboidrati inseriti: {_g(b['carbo_g'])} g).")
        pasti = [p for p in _pasti_alimentari(blocco) if ep["inizio"] - 300 <= _min_pasto(p) <= ep["inizio"]]
        if pasti:
            out.append("Pasto nelle 5 ore precedenti: " + _descrizione_pasto(pasti[-1]) + ".")
        if not boli and not pasti:
            out.append("Nessun bolo né pasto registrato nelle ore precedenti.")
    trattamenti = [p for p in blocco["pasti"] if p.get("tipo_key") == "ipoglicemia"]
    for p in trattamenti:
        out.append(f"Trattamento registrato alle {p['ora']}: {_g(p['carbo_dichiarati_g'] or 0)} g di carboidrati.")
    passi = blocco["riepilogo"].get("passi")
    if passi and passi_medi and passi > 1.3 * passi_medi:
        out.append(f"Giornata più attiva del solito: {passi:.0f} passi contro una media di {passi_medi:.0f}.")
    return out


def _rilevato_iper(blocco, ep_list, alta) -> list[str]:
    out = []
    ep = max(ep_list, key=lambda e: e["fine"] - e["inizio"])
    durata = ep["fine"] - ep["inizio"]
    out.append(f"Glicemia sopra 250 mg/dL per circa {_durata(durata)} (dalle {hhmm(ep['inizio'])} "
               f"alle {hhmm(ep['fine'])}), massimo {ep['estremo']:.0f} mg/dL alle {hhmm(ep['estremo_min'])}.")
    pasti = [p for p in _pasti_alimentari(blocco) if ep["inizio"] - 300 <= _min_pasto(p) <= ep["fine"]]
    if not pasti:
        out.append("Nessun pasto registrato nelle 5 ore prima della salita: potrebbe mancare qualche annotazione "
                   "(spuntini, bevande zuccherate) oppure la salita non dipende da un pasto.")
    for p in pasti:
        n = p.get("nutrienti") or {}
        out.append("Pasto collegato: " + _descrizione_pasto(p) + ".")
        if n.get("grassi") is not None and n["grassi"] >= PASTO_GRASSO_G:
            out.append(f"Il pasto delle {p['ora']} era ricco di grassi ({_g(n['grassi'])} g): i grassi rallentano lo "
                       "svuotamento dello stomaco e possono rendere la salita più lenta e prolungata.")
        if (p.get("carbo_dichiarati_g") or 0) >= 15 and not p.get("bolo_U") and not p.get("in_attesa"):
            out.append(f"Per il pasto delle {p['ora']} non risulta nessun bolo collegato.")
        cam = p.get("carbo_camaps_g")
        if p.get("carbo_diario_g") is not None and cam is not None and abs(p["carbo_diario_g"] - cam) >= 15:
            out.append(f"Carboidrati del diario {_g(p['carbo_diario_g'])} g contro {_g(cam)} g inseriti in CamAPS.")
    return out


def arricchisci_notevoli(notevoli: list[dict], blocchi: list[dict], giorni: list[dict],
                         metriche: dict, soglie: dict) -> list[dict]:
    """Aggiunge a ogni giorno notevole l'elenco `rilevato` e i giorni di tipo
    `tir_basso` e `migliore`, ordinando tutto per data."""
    per_giorno = {b["giorno"]: b for b in blocchi}
    passi_medi = metriche.get("passi")
    out = []
    visti = set()
    for n in notevoli:
        b = per_giorno.get(n["giorno"])
        voce = dict(n)
        rilevato: list[str] = []
        if b and n["tipo"] == "ipo":
            rilevato = _rilevato_ipo(b, _episodi(b["glicemie"], 54), passi_medi)
        elif b and n["tipo"] == "iper":
            rilevato = _rilevato_iper(b, _episodi(b["glicemie"], 250, sopra=True), soglie.get("alta", 180))
        elif n["tipo"] == "senza_dati":
            rilevato = ["Nessuna lettura del sensore: il giorno non entra nelle medie del periodo."]
        voce["rilevato"] = rilevato
        out.append(voce)
        visti.add(n["giorno"])

    qualificanti = [g for g in giorni if (g.get("copertura_cgm_pct") or 0) >= 70 and g.get("tir_pct") is not None]
    for g in qualificanti:
        if g["giorno"] in visti or g["tir_pct"] >= 50:
            continue
        b = per_giorno.get(g["giorno"])
        r = [f"Tempo nel range {g['tir_pct']:.0f}% (sopra range {(g.get('sopra_pct') or 0) + (g.get('molto_sopra_pct') or 0):.0f}%)."]
        if g.get("media_mgdl"):
            r.append(f"Glicemia media {g['media_mgdl']:.0f} mg/dL.")
        if b:
            pasti = _pasti_alimentari(b)
            if pasti:
                carbo = sum(p["carbo_dichiarati_g"] or 0 for p in pasti)
                r.append(f"{len(pasti)} pasti registrati, {_g(carbo)} g di carboidrati nel giorno.")
            alti = [p for p in pasti if p.get("picco") and p["picco"] > 250]
            for p in alti:
                r.append(f"Dopo {p['tipo'].lower()} delle {p['ora']} il picco è stato {p['picco']:.0f} mg/dL.")
        out.append({"giorno": g["giorno"], "tipo": "tir_basso",
                    "dettaglio": f"Tempo nel range {g['tir_pct']:.0f}%", "note": [], "rilevato": r})
    if len(qualificanti) >= 3:
        best = max(qualificanti, key=lambda g: g["tir_pct"])
        if best["tir_pct"] >= 70:
            b = per_giorno.get(best["giorno"])
            r = [f"Tempo nel range {best['tir_pct']:.0f}%, glicemia media {best.get('media_mgdl') or 0:.0f} mg/dL."]
            if b:
                pasti = _pasti_alimentari(b)
                n = b.get("nutrienti") or {}
                if pasti:
                    r.append(f"{len(pasti)} pasti registrati"
                             + (f", {n['kcal']:.0f} kcal" if n.get("kcal") is not None else "")
                             + f", {_g(n.get('carbo') or 0)} g di carboidrati.")
            if best.get("passi"):
                r.append(f"{best['passi']:.0f} passi nella giornata.")
            r.append("Può servire da giornata di riferimento per confrontare le abitudini dei giorni meno buoni.")
            out.append({"giorno": best["giorno"], "tipo": "migliore",
                        "dettaglio": f"Tempo nel range {best['tir_pct']:.0f}%", "note": [], "rilevato": r})
    return sorted(out, key=lambda x: x["giorno"])


# -- profilo alimentare del periodo ------------------------------------------------

def profilo_alimentare(blocchi: list[dict]) -> dict:
    giorni = []
    for b in blocchi:
        n = b.get("nutrienti")
        if n and n.get("kcal") is not None and _pasti_alimentari(b):
            giorni.append({"giorno": b["giorno"], **n, "pasti": len(_pasti_alimentari(b)), "riepilogo": b["riepilogo"]})

    def media(k):
        v = [g[k] for g in giorni if g.get(k) is not None]
        return round(mean(v), 1) if v else None

    medie = {k: media(k) for k in ("kcal", "carbo", "proteine", "grassi", "zuccheri", "fibra")}
    quote = None
    if medie["kcal"] and medie["proteine"] is not None and medie["grassi"] is not None:
        c, p, f = (medie["carbo"] or 0) * 4, medie["proteine"] * 4, medie["grassi"] * 9
        tot = c + p + f
        if tot > 0:
            quote = {"carbo": 100 * c / tot, "proteine": 100 * p / tot, "grassi": 100 * f / tot}
    return {"giorni": giorni, "medie": medie, "quote": quote}


def _alimenti_per_grassi(blocchi: list[dict]) -> list[dict]:
    agg: dict[str, dict] = {}
    for b in blocchi:
        for p in _pasti_alimentari(b):
            for v in p.get("voci") or []:
                f = (v.get("nutrienti") or {}).get("grassi")
                if f is None:
                    continue
                a = agg.setdefault(v["descrizione"].strip().lower(),
                                   {"nome": v["descrizione"].strip(), "grassi": 0.0, "volte": 0, "kcal": 0.0})
                a["grassi"] += f
                a["volte"] += 1
                a["kcal"] += (v.get("nutrienti") or {}).get("kcal") or 0
    return sorted(agg.values(), key=lambda a: -a["grassi"])


def pasti_con_salita(blocchi: list[dict], minimo: float = 80) -> list[dict]:
    """Pasti dopo i quali la glicemia e' salita di piu' (picco meno glicemia di partenza)."""
    out = []
    for b in blocchi:
        for p in _pasti_alimentari(b):
            if p.get("picco") is not None and p.get("glicemia_inizio") is not None:
                salita = p["picco"] - p["glicemia_inizio"]
                if salita >= minimo:
                    out.append({"giorno": b["giorno"], "salita": salita, **p})
    return sorted(out, key=lambda x: -x["salita"])


# -- consigli -------------------------------------------------------------------------

def _voce(sezione, titolo, testo, base=None, priorita="info"):
    return {"sezione": sezione, "titolo": titolo, "testo": testo, "base": base, "priorita": priorita}


def costruisci_consigli(blocchi: list[dict], giorni: list[dict], metriche: dict, soglie: dict) -> dict:
    """Consigli generali di alimentazione e stile di vita, ognuno con il dato del
    periodo che lo motiva (se c'e'). Mai dosi di insulina o cambi di terapia."""
    alta = soglie.get("alta", 180)
    prof = profilo_alimentare(blocchi)
    med, quote = prof["medie"], prof["quote"]
    voci: list[dict] = []
    n_giorni = len(prof["giorni"])

    # Dieta in generale
    if n_giorni:
        if quote and quote["grassi"] > GRASSI_ENERGIA_MAX_PCT:
            voci.append(_voce("Dieta", "Ridurre i grassi totali",
                              "Preferire cotture al forno, al vapore o alla griglia, condire a crudo con olio extravergine "
                              "misurato (un cucchiaio) e scegliere tagli magri di carne e pesce.",
                              f"I grassi forniscono in media il {quote['grassi']:.0f}% dell'energia "
                              f"(riferimento generale: sotto il {GRASSI_ENERGIA_MAX_PCT}%).", "alta"))
        if med["zuccheri"] is not None and med["kcal"]:
            pct = 100 * med["zuccheri"] * 4 / med["kcal"]
            if pct > ZUCCHERI_ENERGIA_MAX_PCT:
                voci.append(_voce("Dieta", "Ridurre gli zuccheri semplici",
                                  "Limitare succhi, bibite, biscotti, dolci e marmellate; la frutta intera è preferibile "
                                  "al succo perché le fibre rallentano l'assorbimento.",
                                  f"Gli zuccheri sono in media {med['zuccheri']:.0f} g al giorno ({pct:.0f}% dell'energia; "
                                  "includono anche quelli naturali di frutta e latte).", "media"))
        if med["fibra"] is not None and med["fibra"] < FIBRE_G_MIN:
            voci.append(_voce("Dieta", "Aumentare le fibre",
                              "Aggiungere verdure a ogni pasto principale, legumi 3-4 volte a settimana, cereali integrali "
                              "e frutta intera. Le fibre attenuano la salita della glicemia dopo i pasti.",
                              f"Fibre in media {med['fibra']:.0f} g al giorno (riferimento generale: almeno {FIBRE_G_MIN} g).",
                              "media"))
        if quote and quote["proteine"] < PROTEINE_ENERGIA_MIN_PCT:
            voci.append(_voce("Dieta", "Distribuire meglio le proteine",
                              "Inserire una fonte di proteine ad ogni pasto (uova, yogurt greco, legumi, pesce, carni magre, "
                              "formaggi freschi): aumentano la sazietà e aiutano a ridurre i carboidrati in eccesso.",
                              f"Le proteine forniscono in media il {quote['proteine']:.0f}% dell'energia "
                              f"(riferimento generale: almeno il {PROTEINE_ENERGIA_MIN_PCT}%).", "media"))
        if quote and quote["carbo"] > 55:
            voci.append(_voce("Dieta", "Rivedere la quota di carboidrati",
                              "Privilegiare carboidrati complessi e integrali, porzioni misurate e abbinati a verdure e proteine.",
                              f"I carboidrati forniscono in media il {quote['carbo']:.0f}% dell'energia.", "media"))
    voci.append(_voce("Dieta", "Struttura del piatto",
                      "Un modello semplice: metà piatto di verdure, un quarto di proteine, un quarto di carboidrati "
                      "preferibilmente integrali. Mangiare prima verdure e proteine e per ultimi i carboidrati "
                      "tende a ridurre il picco della glicemia dopo il pasto."))

    # Alimenti troppo grassi da evitare
    grassi = _alimenti_per_grassi(blocchi)
    pesanti = [a for a in grassi if a["grassi"] >= 10][:6]
    pasti_grassi = [(b["giorno"], p) for b in blocchi for p in _pasti_alimentari(b)
                    if (p.get("nutrienti") or {}).get("grassi") is not None and p["nutrienti"]["grassi"] >= PASTO_GRASSO_G]
    if pesanti:
        elenco = "; ".join(f"{a['nome']} ({a['grassi']:.0f} g di grassi in {a['volte']} "
                           f"{'volta' if a['volte'] == 1 else 'volte'})" for a in pesanti)
        voci.append(_voce("Alimenti da limitare", "Gli alimenti più ricchi di grassi del periodo",
                          "Ridurne la frequenza o la porzione e sostituirli con alternative più leggere: fritti e panature → "
                          "forno o piastra; salumi e formaggi stagionati → prosciutto magro, ricotta, mozzarella light; "
                          "dolci da forno e merendine → frutta o yogurt; salse e condimenti pronti → olio a crudo misurato.",
                          elenco + ".", "alta"))
    if pasti_grassi:
        voci.append(_voce("Alimenti da limitare", "Pasti con più di 25 g di grassi",
                          "Nei pasti molto grassi lo stomaco si svuota più lentamente: la glicemia può salire tardi e restare "
                          "alta più a lungo. Meglio distribuire i grassi nell'arco della giornata e non concentrarli in un solo pasto.",
                          f"{len(pasti_grassi)} pasti su {sum(len(_pasti_alimentari(b)) for b in blocchi)} "
                          f"superano i {PASTO_GRASSO_G} g di grassi (es. "
                          + ", ".join(f"{p['tipo'].lower()} del {_fmt_g(g)}: {p['nutrienti']['grassi']:.0f} g"
                                      for g, p in pasti_grassi[:3]) + ").", "media"))
        alti = [p["picco_2h"] for _, p in pasti_grassi if p.get("picco_2h") is not None]
        altri = [p["picco_2h"] for b in blocchi for p in _pasti_alimentari(b)
                 if p.get("picco_2h") is not None and (p.get("nutrienti") or {}).get("grassi") is not None
                 and p["nutrienti"]["grassi"] < PASTO_GRASSO_G]
        if len(alti) >= 2 and len(altri) >= 2:
            voci[-1]["base"] += (f" A 2 ore dai pasti più grassi la glicemia media è {mean(alti):.0f} mg/dL, "
                                 f"negli altri pasti {mean(altri):.0f} mg/dL.")

    # per tipo di pasto
    per_tipo: dict[str, list[dict]] = {}
    for b in blocchi:
        for p in _pasti_alimentari(b):
            per_tipo.setdefault(p["tipo_key"], []).append(p)

    def stat(pasti, chiave):
        v = [(p.get("nutrienti") or {}).get(chiave) for p in pasti]
        v = [x for x in v if x is not None]
        return mean(v) if v else None

    def salita_media(pasti):
        v = [p["picco"] - p["glicemia_inizio"] for p in pasti
             if p.get("picco") is not None and p.get("glicemia_inizio") is not None]
        return mean(v) if v else None

    col = per_tipo.get("colazione", [])
    base = None
    if col:
        c, z, s = stat(col, "carbo"), stat(col, "zuccheri"), salita_media(col)
        base = f"{len(col)} colazioni registrate, carboidrati medi {c:.0f} g" if c is not None else None
        if base and z is not None:
            base += f", zuccheri {z:.0f} g"
        if base and s is not None:
            base += f"; la glicemia sale in media di {s:.0f} mg/dL dopo la colazione"
        base = base + "." if base else None
        pesante = (c is not None and c >= 50) or (z is not None and z >= 20) or (s is not None and s >= 70)
    else:
        pesante = False
    voci.append(_voce("Colazione", "Una colazione più equilibrata" if pesante else "Come impostare la colazione",
                      "Costruirla attorno a proteine e fibre: yogurt greco o skyr non zuccherato con avena e frutta intera, "
                      "uova con pane integrale, ricotta e frutta secca. Ridurre succhi, cornetti, biscotti, marmellata e "
                      "cereali zuccherati, che fanno salire rapidamente la glicemia al mattino.",
                      base, "alta" if pesante else "info"))
    if not col and n_giorni:
        voci[-1]["base"] = "Nel periodo non risultano colazioni registrate."

    for chiave, titolo, testo in (
        ("pranzo", "Il pranzo",
         "Iniziare con verdure, poi proteine, e per ultimi i carboidrati; una porzione di cereali integrali o legumi "
         "al posto di pane e pasta raffinati. Una passeggiata di 10-15 minuti dopo il pasto aiuta a contenere il picco."),
        ("cena", "La cena",
         "Cena più leggera e non troppo tardi (idealmente almeno 2-3 ore prima di dormire): verdure, una fonte di proteine "
         "magre e una porzione contenuta di carboidrati. Evitare fritti, pizze molto condite e dessert a fine pasto, "
         "che possono tenere la glicemia alta durante la notte."),
    ):
        pasti = per_tipo.get(chiave, [])
        b = None
        if pasti:
            parti = [f"{len(pasti)} registrati"]
            for k, lab in (("kcal", "kcal"), ("carbo", "g di carboidrati"), ("grassi", "g di grassi")):
                v = stat(pasti, k)
                if v is not None:
                    parti.append(f"{v:.0f} {lab} in media")
            s = salita_media(pasti)
            if s is not None:
                parti.append(f"salita media della glicemia {s:.0f} mg/dL")
            b = ", ".join(parti) + "."
        voci.append(_voce("Pranzo" if chiave == "pranzo" else "Cena", titolo, testo, b))

    spunti = per_tipo.get("spuntino", []) + per_tipo.get("merenda", []) + per_tipo.get("fuori_pasto", [])
    b = None
    prior = "info"
    if spunti:
        c, z = stat(spunti, "carbo"), stat(spunti, "zuccheri")
        b = f"{len(spunti)} tra spuntini, merende e fuori pasto"
        if c is not None:
            b += f", carboidrati medi {c:.0f} g"
        if z is not None:
            b += f", zuccheri {z:.0f} g"
        senza_bolo = [p for p in spunti if (p.get("carbo_dichiarati_g") or 0) >= 15 and not p.get("bolo_U")
                      and not p.get("in_attesa")]
        if senza_bolo:
            b += f". In {len(senza_bolo)} di essi i carboidrati dichiarati (almeno 15 g) non hanno un bolo collegato: da segnalare al diabetologo"
            prior = "media"
        b += "."
    voci.append(_voce("Spuntini e merende", "Spuntini che non fanno salire la glicemia",
                      "Scegliere spuntini con proteine e fibre e pochi carboidrati: una manciata di frutta secca, yogurt non "
                      "zuccherato, formaggio fresco, verdure crude con hummus, un frutto intero. Evitare di mangiucchiare "
                      "zuccheri semplici fuori dai pasti, tranne quando servono a trattare un'ipoglicemia.", b, prior))

    # salite dopo i pasti
    salite = pasti_con_salita(blocchi)[:4]
    if salite:
        voci.append(_voce("Dopo i pasti", "I pasti con la salita più alta",
                          "Per questi pasti conviene osservare cosa si ripete (alimenti, porzioni, orario, assenza di verdure o "
                          "di movimento dopo) e parlarne con il diabetologo o il dietista.",
                          "; ".join(f"{_fmt_g(s['giorno'])} {s['tipo'].lower()} alle {s['ora']}: da {s['glicemia_inizio']:.0f} "
                                    f"a {s['picco']:.0f} mg/dL ({_g(s['carbo_dichiarati_g'])} g di carboidrati"
                                    + (f", {', '.join(v['descrizione'] for v in s['voci'][:3])}" if s.get("voci") else "")
                                    + ")" for s in salite) + ".", "alta"))

    # attivita' fisica
    passi = [g["passi"] for g in giorni if g.get("passi") is not None]
    base = None
    if passi:
        base = f"Passi medi {mean(passi):.0f} al giorno."
        tir_g = [(g["passi"], g["tir_pct"]) for g in giorni
                 if g.get("passi") is not None and g.get("tir_pct") is not None and (g.get("copertura_cgm_pct") or 0) >= 70]
        if len(tir_g) >= 6:
            soglia = median(p for p, _ in tir_g)
            su = [t for p, t in tir_g if p >= soglia]
            giu = [t for p, t in tir_g if p < soglia]
            if su and giu:
                base += (f" Nei giorni con più passi (da {soglia:.0f} in su) il tempo nel range medio è {mean(su):.0f}%, "
                         f"negli altri {mean(giu):.0f}%.")
    voci.append(_voce("Movimento", "Muoversi dopo i pasti",
                      "Una camminata di 10-15 minuti dopo i pasti principali riduce il picco di glicemia. "
                      f"Un obiettivo generale è arrivare a circa {PASSI_ATTIVO} passi al giorno, aumentando gradualmente. "
                      "Attenzione a pianificare l'attività con il diabetologo se in passato ci sono state ipoglicemie dopo il movimento.",
                      base, "media" if passi and mean(passi) < PASSI_ATTIVO else "info"))

    # registrazione
    senza_dati = sum(1 for b in blocchi for p in _pasti_alimentari(b)
                     if (p.get("nutrienti") or {}).get("kcal") is None)
    giorni_senza_pasti = sum(1 for b in blocchi if not _pasti_alimentari(b))
    if senza_dati or giorni_senza_pasti:
        parti = []
        if giorni_senza_pasti:
            parti.append(f"{giorni_senza_pasti} giorni senza pasti registrati")
        if senza_dati:
            parti.append(f"{senza_dati} pasti senza valori nutrizionali completi")
        voci.append(_voce("Diario", "Registrare di più per avere analisi più precise",
                          "Annotare ogni pasto con foto o alimenti e grammi rende più affidabili medie, confronti e consigli.",
                          "; ".join(parti) + ".", "info"))

    ordine = ["Dieta", "Alimenti da limitare", "Colazione", "Pranzo", "Cena", "Spuntini e merende",
              "Dopo i pasti", "Movimento", "Diario"]
    sezioni = [{"nome": nome, "voci": [v for v in voci if v["sezione"] == nome]} for nome in ordine]
    return {"sezioni": [s for s in sezioni if s["voci"]], "profilo": prof, "ha_dati_cibo": bool(n_giorni)}
