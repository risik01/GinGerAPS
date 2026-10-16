"""Tabella Ciqual (ANSES, Francia): circa 3.000 alimenti generici con i
carboidrati disponibili per 100 g (fibra esclusa, come nelle etichette UE).

I dati sono aperti e riutilizzabili citando la fonte. L'add-on li scarica
una volta sola dall'archivio pubblico Zenodo dove ANSES li pubblica, e tiene
solo carboidrati, zuccheri, fibra, calorie, proteine e grassi.

I nomi sono in francese e inglese: la ricerca accetta parole italiane e le
traduce con il dizionario SYNONYMS qui sotto.
"""

import codecs
import html
import logging
import re
import unicodedata

import requests

ZENODO_LATEST = "https://zenodo.org/api/records/17550132/versions/latest"
CONST = {31000: "carbo", 32000: "zuccheri", 34100: "fibra", 328: "kcal", 25000: "proteine", 40000: "grassi"}

log = logging.getLogger("diario")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ciqual (
    code     INTEGER PRIMARY KEY,
    nome_fr  TEXT,
    nome_en  TEXT,
    carbo    REAL,
    zuccheri REAL,
    fibra    REAL,
    ricerca  TEXT NOT NULL,
    ricerca_en TEXT
);
"""

STOPWORDS = {"al", "alla", "allo", "alle", "ai", "agli", "con", "di", "del", "della", "dei", "delle",
             "e", "ed", "in", "da", "la", "il", "lo", "le", "i", "gli", "un", "una", "per", "senza"}

# Parole italiane -> radici in francese e inglese (senza accenti)
SYNONYMS = {
    "pasta": ["pates", "pasta"], "integrale": ["complet", "wholemeal", "wholegrain", "whole"],
    "integrali": ["complet", "wholemeal", "wholegrain", "whole"],
    "cotta": ["cuit", "cooked"], "cotto": ["cuit", "cooked"], "cotte": ["cuit", "cooked"],
    "cotti": ["cuit", "cooked"], "cruda": ["cru", "raw"], "crudo": ["cru", "raw"],
    "crude": ["cru", "raw"], "crudi": ["cru", "raw"], "secca": ["sec", "dry", "dried"],
    "secco": ["sec", "dry", "dried"], "secchi": ["sec", "dry", "dried"],
    "fresca": ["frais", "fresh"], "fresco": ["frais", "fresh"],
    "riso": ["riz", "rice"], "pane": ["pain", "bread"], "panino": ["sandwich", "pain", "roll"],
    "patata": ["pomme de terre", "potato"], "patate": ["pomme de terre", "potato"],
    "pizza": ["pizza"], "pomodoro": ["tomate", "tomato"], "sugo": ["sauce"], "salsa": ["sauce"],
    "mela": ["pomme", "apple"], "mele": ["pomme", "apple"], "pera": ["poire", "pear"],
    "banana": ["banane", "banana"], "arancia": ["orange"], "arance": ["orange"],
    "mandarino": ["mandarine", "clementine"], "uva": ["raisin", "grape"], "pesca": ["peche", "peach"],
    "fragole": ["fraise", "strawberr"], "fragola": ["fraise", "strawberr"], "kiwi": ["kiwi"],
    "ananas": ["ananas", "pineapple"], "albicocca": ["abricot", "apricot"],
    "ciliegie": ["cerise", "cherr"], "melone": ["melon"], "anguria": ["pasteque", "watermelon"],
    "fichi": ["figue", "fig"], "prugna": ["prune", "plum"], "prugne": ["prune", "plum"],
    "frutta": ["fruit"], "verdura": ["legume", "vegetable"], "verdure": ["legume", "vegetable"],
    "latte": ["lait", "milk"], "yogurt": ["yaourt", "yogurt", "yoghurt"],
    "formaggio": ["fromage", "cheese"], "burro": ["beurre", "butter"],
    "uovo": ["oeuf", "egg"], "uova": ["oeuf", "egg"],
    "biscotti": ["biscuit"], "biscotto": ["biscuit"], "fette": ["biscotte", "rusk", "toast"],
    "biscottate": ["biscotte", "rusk"], "cereali": ["cereale", "cereal"],
    "fiocchi": ["flocon", "flake"], "avena": ["avoine", "oat"], "muesli": ["muesli"],
    "zucchero": ["sucre", "sugar"], "miele": ["miel", "honey"],
    "marmellata": ["confiture", "jam"], "confettura": ["confiture", "jam"],
    "cioccolato": ["chocolat", "chocolate"], "gelato": ["glace", "ice cream"],
    "torta": ["gateau", "cake"], "cornetto": ["croissant"], "brioche": ["brioche"],
    "crostata": ["tarte", "tart"], "farina": ["farine", "flour"],
    "polenta": ["polenta", "semoule de mais", "maize"], "couscous": ["couscous"],
    "gnocchi": ["gnocchi"], "lasagne": ["lasagne", "lasagna"], "ravioli": ["ravioli"],
    "tortellini": ["tortellini"], "fagioli": ["haricot", "bean"], "ceci": ["pois chiche", "chickpea"],
    "lenticchie": ["lentille", "lentil"], "piselli": ["petit pois", "pea"],
    "legumi": ["legumineuse", "pulse"], "carote": ["carotte", "carrot"],
    "zucchine": ["courgette"], "insalata": ["salade", "lettuce"], "mais": ["mais", "corn"],
    "succo": ["jus", "juice"], "birra": ["biere", "beer"], "vino": ["vin", "wine"],
    "crackers": ["cracker"], "cracker": ["cracker"], "grissini": ["gressin", "breadstick"],
    "focaccia": ["fougasse", "focaccia"], "piadina": ["wrap", "tortilla"],
    "pollo": ["poulet", "chicken"], "carne": ["viande", "meat"], "pesce": ["poisson", "fish"],
    "prosciutto": ["jambon", "ham"], "bianco": ["blanc", "white"], "bianca": ["blanc", "white"],
    "intero": ["entier", "whole"], "scremato": ["ecreme", "skimmed"],
    "parzialmente": ["demi", "semi"], "fritte": ["frit", "fried"], "fritto": ["frit", "fried"],
    "lesso": ["bouilli", "boiled", "cuit a l'eau"], "bollito": ["bouilli", "boiled", "cuit a l'eau"],
    "forno": ["four", "baked", "roast"], "olio": ["huile", "oil"], "dolce": ["dessert", "sweet"],
    "merendina": ["gateau", "cake", "snack"], "patatine": ["chips", "crisps", "frites"],
    "noci": ["noix", "walnut"], "mandorle": ["amande", "almond"], "nocciole": ["noisette", "hazelnut"],
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text.lower()).strip()


def ensure_schema(store) -> None:
    store.db.executescript(SCHEMA)
    cols = {r[1] for r in store.db.execute("PRAGMA table_info(ciqual)")}
    if "ricerca_en" not in cols:
        store.db.execute("ALTER TABLE ciqual ADD COLUMN ricerca_en TEXT")
    for extra in ("kcal", "proteine", "grassi"):
        if extra not in cols:
            store.db.execute(f"ALTER TABLE ciqual ADD COLUMN {extra} REAL")
    store.db.commit()


def needs_macros(store) -> bool:
    """True se la tabella e' stata importata prima di aggiungere calorie/proteine/grassi."""
    ensure_schema(store)
    return store.db.execute("SELECT COUNT(*) FROM ciqual").fetchone()[0] > 0 and \
        store.db.execute("SELECT COUNT(*) FROM ciqual WHERE kcal IS NOT NULL").fetchone()[0] == 0


def _synonyms(tok: str) -> list[str]:
    """Cerca la parola, poi le forme singolari/plurali piu' comuni."""
    for cand in (tok, tok[:-1] + "a", tok[:-1] + "o", tok[:-1] + "e", tok[:-1] + "i"):
        if cand in SYNONYMS:
            return SYNONYMS[cand]
    return []


def _number(text: str | None):
    if text is None:
        return None
    t = text.strip().replace(",", ".")
    if not t or t in ("-", "missing"):
        return None
    if t.lower() == "traces":
        return 0.0
    t = t.lstrip("<").strip()
    try:
        return float(t)
    except ValueError:
        return None


def _field(block: str, tag: str):
    m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", block, re.S)
    return html.unescape(m.group(1)) if m else None


def _encoding(head: bytes) -> str:
    m = re.search(rb'encoding="([^"]+)"', head[:200])
    enc = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        codecs.lookup(enc)
        return enc
    except LookupError:
        return "utf-8"


def _iter_blocks(resp, tag: str):
    """Legge un file XML grande a pezzi e restituisce i blocchi <TAG>...</TAG>."""
    decoder = None
    buf = ""
    pattern = re.compile(rf"<{tag}>(.*?)</{tag}>", re.S)
    for chunk in resp.iter_content(chunk_size=1 << 20):
        if decoder is None:
            decoder = codecs.getincrementaldecoder(_encoding(chunk))(errors="replace")
        buf += decoder.decode(chunk)
        last = 0
        for m in pattern.finditer(buf):
            yield m.group(1)
            last = m.end()
        buf = buf[last:]
    if decoder is not None:
        buf += decoder.decode(b"", final=True)
        for m in pattern.finditer(buf):
            yield m.group(1)


def _pick(files, prefix):
    for f in files:
        name = f.get("key", "")
        if name.endswith(".xml") and name.startswith(prefix):
            if prefix == "alim_" and name.startswith("alim_grp_"):
                continue
            return f
    return None


def import_ciqual(store, http: requests.Session | None = None, latest_url: str = ZENODO_LATEST) -> int:
    """Scarica e importa la tabella. Restituisce il numero di alimenti."""
    http = http or requests.Session()
    ensure_schema(store)
    meta = http.get(latest_url, timeout=60, headers={"Accept": "application/json"})
    meta.raise_for_status()
    data = meta.json()
    files = data.get("files") or []
    alim_f, compo_f = _pick(files, "alim_"), _pick(files, "compo_")
    if not alim_f or not compo_f:
        raise RuntimeError("Nell'archivio Ciqual mancano i file degli alimenti o della composizione")
    version = (data.get("metadata") or {}).get("version") or str(data.get("id", ""))

    foods = {}
    with http.get(alim_f["links"]["self"], stream=True, timeout=120) as r:
        r.raise_for_status()
        for block in _iter_blocks(r, "ALIM"):
            code = _field(block, "alim_code")
            if code and code.strip().isdigit():
                foods[int(code)] = {"fr": _field(block, "alim_nom_fr"), "en": _field(block, "alim_nom_eng"),
                                    "carbo": None, "zuccheri": None, "fibra": None,
                                    "kcal": None, "proteine": None, "grassi": None}
    log.info("Ciqual: %d alimenti", len(foods))

    with http.get(compo_f["links"]["self"], stream=True, timeout=300) as r:
        r.raise_for_status()
        for block in _iter_blocks(r, "COMPO"):
            const = _field(block, "const_code")
            if not const or not const.strip().isdigit() or int(const) not in CONST:
                continue
            code = _field(block, "alim_code")
            if not code or not code.strip().isdigit() or int(code) not in foods:
                continue
            foods[int(code)][CONST[int(const)]] = _number(_field(block, "teneur"))

    rows = [(code, f["fr"], f["en"], f["carbo"], f["zuccheri"], f["fibra"],
             f["kcal"], f["proteine"], f["grassi"],
             normalize(f"{f['fr'] or ''} | {f['en'] or ''}"), normalize(f["en"] or ""))
            for code, f in foods.items() if f["carbo"] is not None]
    if not rows:
        raise RuntimeError("La tabella Ciqual scaricata non contiene valori di carboidrati")
    store.db.execute("DELETE FROM ciqual")
    store.db.executemany(
        "INSERT INTO ciqual(code, nome_fr, nome_en, carbo, zuccheri, fibra, kcal, proteine, grassi, "
        "ricerca, ricerca_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows)
    store.db.commit()
    # Gli alimenti Ciqual gia' nel diario nascono con i soli carboidrati: ora che
    # la tabella ha anche calorie/proteine/grassi, li completa (e i pasti che li usano).
    for col, src in (("kcal_per_100g", "kcal"), ("protein_per_100g", "proteine"), ("fat_per_100g", "grassi"),
                     ("sugars_per_100g", "zuccheri"), ("fiber_per_100g", "fibra")):
        store.db.execute(
            f"UPDATE food SET {col} = (SELECT c.{src} FROM ciqual c WHERE c.code = CAST(food.ref_code AS INTEGER)) "
            f"WHERE source = 'ciqual' AND {col} IS NULL AND ref_code IS NOT NULL")
    store.db.commit()
    store.set("nutrienti_backfill_v1", "")
    store._backfill_nutrienti()
    store.set("ciqual_versione", version)
    store.set("ciqual_alimenti", len(rows))
    return len(rows)


def count(store) -> int:
    ensure_schema(store)
    return store.db.execute("SELECT COUNT(*) FROM ciqual").fetchone()[0]


def search(store, query: str, limit: int = 25) -> list[dict]:
    ensure_schema(store)
    tokens = [t for t in re.split(r"[\s,;]+", normalize(query)) if len(t) >= 2 and t not in STOPWORDS]
    if not tokens:
        return []
    clauses, params, score, score_params = [], [], [], []
    for tok in tokens:
        alts = [tok] + [normalize(a) for a in _synonyms(tok)]
        clauses.append("(" + " OR ".join("ricerca LIKE ?" for _ in alts) + ")")
        params += [f"%{a}%" for a in alts]
        # punteggio: quante parole compaiono nel nome inglese (piu' leggibile)
        score.append("(CASE WHEN " + " OR ".join("IFNULL(ricerca_en, '') LIKE ?" for _ in alts) + " THEN 1 ELSE 0 END)")
        score_params += [f"%{a}%" for a in alts]
    rows = store.db.execute(
        f"SELECT *, ({' + '.join(score)}) AS punti FROM ciqual WHERE {' AND '.join(clauses)} "
        "ORDER BY punti DESC, LENGTH(COALESCE(nome_en, nome_fr)) LIMIT ?",
        (*score_params, *params, limit))
    return [{
        "codice": r["code"],
        "nome": r["nome_en"] or r["nome_fr"],
        "nome_fr": r["nome_fr"],
        "carbo_100g": r["carbo"],
        "zuccheri_100g": r["zuccheri"],
        "fibra_100g": r["fibra"],
        "kcal_100g": r["kcal"],
        "proteine_100g": r["proteine"],
        "grassi_100g": r["grassi"],
        "fonte": "ciqual",
    } for r in rows]
