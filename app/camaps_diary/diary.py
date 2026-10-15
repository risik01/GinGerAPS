"""Diario pasti e database alimenti personale."""

import json
from datetime import datetime

from . import foto as foto_mod
from .errors import DiaryError
from .store import Store

MEAL_TYPES = ("colazione", "spuntino", "pranzo", "merenda", "cena", "fuori_pasto", "ipoglicemia")


def _round(v, nd=1):
    return None if v is None else round(float(v), nd)


def _food_dict(row) -> dict:
    keys = row.keys()
    portions_raw = row["default_portions"] if "default_portions" in keys else None
    try:
        portions = json.loads(portions_raw) if portions_raw else []
    except ValueError:
        portions = []
    return {
        "id": row["id"],
        "nome": row["name"],
        "marca": row["brand"],
        "carbo_100g": row["carbs_per_100g"],
        "zuccheri_100g": row["sugars_per_100g"] if "sugars_per_100g" in keys else None,
        "fibra_100g": row["fiber_per_100g"] if "fiber_per_100g" in keys else None,
        "proteine_100g": row["protein_per_100g"] if "protein_per_100g" in keys else None,
        "grassi_100g": row["fat_per_100g"] if "fat_per_100g" in keys else None,
        "kcal_100g": row["kcal_per_100g"] if "kcal_per_100g" in keys else None,
        "porzione_g": row["portion_g"],
        "porzioni": portions,
        "porzioni_n": row["servings"] if "servings" in keys else None,
        "fonte": row["source"],
        "barcode": row["barcode"],
        "codice": row["ref_code"],
        "usi": row["uses"] if "uses" in keys else None,
    }


def _opt_num(data: dict, key: str):
    v = data.get(key)
    if v in (None, ""):
        return None
    try:
        return round(float(str(v).replace(",", ".")), 2)
    except (TypeError, ValueError):
        return None


def _clean_food(data: dict) -> dict:
    name = (data.get("nome") or "").strip()
    if not name:
        raise DiaryError("Scrivi il nome dell'alimento.")
    try:
        carbs = float(str(data.get("carbo_100g")).replace(",", "."))
    except (TypeError, ValueError):
        raise DiaryError("Indica i carboidrati per 100 g con un numero.")
    if not 0 <= carbs <= 100:
        raise DiaryError("I carboidrati per 100 g devono essere tra 0 e 100.")
    portion = data.get("porzione_g")
    if portion in (None, ""):
        portion = None
    else:
        try:
            portion = float(str(portion).replace(",", "."))
        except ValueError:
            raise DiaryError("La porzione deve essere un numero di grammi.")
        if portion <= 0:
            portion = None
    barcode = (data.get("barcode") or "").strip() or None
    ref = data.get("codice")
    portions = []
    for p in data.get("porzioni") or []:
        label = (p.get("label") or "").strip()
        try:
            grams = float(str(p.get("grammi")).replace(",", "."))
        except (TypeError, ValueError):
            continue
        if label and grams > 0:
            portions.append({"label": label, "grammi": round(grams, 1)})
    return {"name": name, "brand": (data.get("marca") or "").strip() or None,
            "carbs_per_100g": round(carbs, 2), "portion_g": portion,
            "sugars_per_100g": _opt_num(data, "zuccheri_100g"),
            "fiber_per_100g": _opt_num(data, "fibra_100g"),
            "protein_per_100g": _opt_num(data, "proteine_100g"),
            "fat_per_100g": _opt_num(data, "grassi_100g"),
            "kcal_per_100g": _opt_num(data, "kcal_100g"),
            "default_portions": json.dumps(portions) if portions else None,
            "source": (data.get("fonte") or "manuale").strip(), "barcode": barcode,
            "ref_code": str(ref).strip() if ref not in (None, "") else None}


# -- alimenti ----------------------------------------------------------------

FOOD_SELECT = """
SELECT f.*, (SELECT COUNT(*) FROM meal_item mi WHERE mi.food_id = f.id) AS uses
FROM food f
"""


def _fts_match_query(q: str) -> str | None:
    """Ogni parola diventa un prefisso ("past pom" -> "past"* AND "pom"*):
    trova alimenti mentre si scrive, senza dover finire la parola."""
    terms = [t.replace('"', '""') for t in q.split() if t]
    if not terms:
        return None
    return " AND ".join(f'"{t}"*' for t in terms)


def search_foods(store: Store, q: str = "", limit: int = 40) -> list[dict]:
    q = (q or "").strip()
    if q:
        match = _fts_match_query(q)
        rows = store.db.execute(
            "SELECT f.*, (SELECT COUNT(*) FROM meal_item mi WHERE mi.food_id = f.id) AS uses "
            "FROM food_fts JOIN food f ON f.id = food_fts.rowid "
            "WHERE food_fts MATCH ? ORDER BY uses DESC, f.name LIMIT ?", (match, limit))
    else:
        rows = store.db.execute(FOOD_SELECT + " ORDER BY uses DESC, f.name LIMIT ?", (limit,))
    return [_food_dict(r) for r in rows]


def frequent_foods(store: Store, limit: int = 20) -> list[dict]:
    """Alimenti e ricette usati di recente, ordinati per quante volte e quanto
    di recente: alimenta la scheda "Frequenti" dell'editor pasto."""
    rows = store.db.execute(
        "SELECT f.*, COUNT(mi.id) AS uses, MAX(m.ts_local) AS last_used "
        "FROM food f JOIN meal_item mi ON mi.food_id = f.id JOIN meal m ON m.id = mi.meal_id "
        "GROUP BY f.id ORDER BY uses DESC, last_used DESC LIMIT ?", (limit,))
    return [_food_dict(r) for r in rows]


def get_food(store: Store, food_id: int) -> dict | None:
    row = store.db.execute(FOOD_SELECT + " WHERE f.id = ?", (food_id,)).fetchone()
    return _food_dict(row) if row else None


def food_by_barcode(store: Store, barcode: str) -> dict | None:
    row = store.db.execute(FOOD_SELECT + " WHERE f.barcode = ?", (barcode,)).fetchone()
    return _food_dict(row) if row else None


def create_food(store: Store, data: dict) -> dict:
    f = _clean_food(data)
    if f["barcode"]:
        existing = food_by_barcode(store, f["barcode"])
        if existing:
            return existing
    if f["ref_code"] and f["source"] == "ciqual":
        row = store.db.execute(FOOD_SELECT + " WHERE f.source = 'ciqual' AND f.ref_code = ?",
                               (f["ref_code"],)).fetchone()
        if row:
            return _food_dict(row)
    cur = store.db.execute(
        "INSERT INTO food(name, brand, carbs_per_100g, portion_g, source, barcode, ref_code, updated_at, "
        "sugars_per_100g, fiber_per_100g, protein_per_100g, fat_per_100g, kcal_per_100g, default_portions) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?)",
        (f["name"], f["brand"], f["carbs_per_100g"], f["portion_g"], f["source"], f["barcode"],
         f["ref_code"], f["sugars_per_100g"], f["fiber_per_100g"], f["protein_per_100g"],
         f["fat_per_100g"], f["kcal_per_100g"], f["default_portions"]))
    store.db.commit()
    return get_food(store, cur.lastrowid)


def update_food(store: Store, food_id: int, data: dict) -> dict:
    if not get_food(store, food_id):
        raise DiaryError("Alimento non trovato.")
    f = _clean_food(data)
    store.db.execute(
        "UPDATE food SET name = ?, brand = ?, carbs_per_100g = ?, portion_g = ?, source = ?, "
        "barcode = ?, ref_code = COALESCE(?, ref_code), updated_at = CURRENT_TIMESTAMP, "
        "sugars_per_100g = ?, fiber_per_100g = ?, protein_per_100g = ?, fat_per_100g = ?, "
        "kcal_per_100g = ?, default_portions = ? WHERE id = ?",
        (f["name"], f["brand"], f["carbs_per_100g"], f["portion_g"], f["source"], f["barcode"],
         f["ref_code"], f["sugars_per_100g"], f["fiber_per_100g"], f["protein_per_100g"],
         f["fat_per_100g"], f["kcal_per_100g"], f["default_portions"], food_id))
    store.db.commit()
    return get_food(store, food_id)


def delete_food(store: Store, food_id: int) -> None:
    """I pasti gia' registrati mantengono descrizione e grammi/carboidrati."""
    store.db.execute("UPDATE meal_item SET food_id = NULL WHERE food_id = ?", (food_id,))
    store.db.execute("DELETE FROM food WHERE id = ?", (food_id,))
    store.db.commit()


# -- ricette personali --------------------------------------------------------

def recipe_items(store: Store, food_id: int) -> list[dict]:
    rows = store.db.execute("SELECT * FROM recipe_item WHERE food_id = ? ORDER BY id", (food_id,))
    return [{"id": r["id"], "alimento_id": r["ingredient_food_id"], "descrizione": r["description"],
             "grammi": r["grams"], "carbo_g": r["carbs_g"]} for r in rows]


def save_recipe(store: Store, data: dict, food_id: int | None = None) -> dict:
    """Ricetta personale: ingredienti pesati -> un alimento con macro aggregati
    per 100 g, con gli ingredienti conservati per poterla rivedere dopo.
    Si inserisce nei pasti come un alimento qualsiasi (fonte='ricetta')."""
    name = (data.get("nome") or "").strip()
    if not name:
        raise DiaryError("Dai un nome alla ricetta.")
    try:
        servings = max(1, int(data.get("porzioni_n") or 1))
    except (TypeError, ValueError):
        servings = 1
    items = _clean_items(store, data.get("voci"))
    if not items:
        raise DiaryError("Aggiungi almeno un ingrediente alla ricetta.")
    if any(it["grams"] is None for it in items):
        raise DiaryError("Per la ricetta servono i grammi di ogni ingrediente: niente voci libere.")
    grams_total = sum(it["grams"] for it in items)
    carbs_total = sum(it["carbs_g"] for it in items)
    if grams_total <= 0:
        raise DiaryError("Indica i grammi di ogni ingrediente per calcolare la ricetta.")
    carbs_100g = round(carbs_total / grams_total * 100, 2)
    portion = round(grams_total / servings, 1)

    if food_id is None:
        cur = store.db.execute(
            "INSERT INTO food(name, carbs_per_100g, portion_g, source, servings, updated_at) "
            "VALUES (?, ?, ?, 'ricetta', ?, CURRENT_TIMESTAMP)",
            (name, carbs_100g, portion, servings))
        food_id = cur.lastrowid
    else:
        if not get_food(store, food_id):
            raise DiaryError("Ricetta non trovata.")
        store.db.execute(
            "UPDATE food SET name = ?, carbs_per_100g = ?, portion_g = ?, servings = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (name, carbs_100g, portion, servings, food_id))
        store.db.execute("DELETE FROM recipe_item WHERE food_id = ?", (food_id,))
    store.db.executemany(
        "INSERT INTO recipe_item(food_id, ingredient_food_id, description, grams, carbs_g) "
        "VALUES (?, ?, ?, ?, ?)",
        [(food_id, it["food_id"], it["description"], it["grams"], it["carbs_g"]) for it in items])
    store.db.commit()
    food = get_food(store, food_id)
    food["ingredienti"] = recipe_items(store, food_id)
    return food


# -- pasti -------------------------------------------------------------------

def _parse_ts(value: str) -> str:
    value = (value or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise DiaryError("Data e ora del pasto non valide.")


def _clean_items(store: Store, items) -> list[dict]:
    # Lista vuota ammessa: un pasto puo' nascere con solo una foto, i carboidrati
    # arrivano dopo a mano o dalla stima IA.
    if not items:
        return []
    out = []
    for it in items:
        food_id = it.get("alimento_id")
        grams = it.get("grammi")
        grams = None if grams in (None, "") else float(str(grams).replace(",", "."))
        desc = (it.get("descrizione") or "").strip()
        if food_id:
            food = get_food(store, int(food_id))
            if not food:
                raise DiaryError("Uno degli alimenti non esiste piu'.")
            if not grams or grams <= 0:
                raise DiaryError(f"Indica i grammi di {food['nome']}.")
            carbs = grams * food["carbo_100g"] / 100
            desc = desc or food["nome"]
            food_id = food["id"]
        else:
            try:
                carbs = float(str(it.get("carbo_g")).replace(",", "."))
            except (TypeError, ValueError):
                raise DiaryError("Per una voce libera indica i carboidrati in grammi.")
            if not desc:
                raise DiaryError("Dai un nome alla voce libera.")
            food_id = None
        if carbs < 0:
            raise DiaryError("I carboidrati non possono essere negativi.")
        out.append({"food_id": food_id, "description": desc, "grams": grams,
                    "carbs_g": round(carbs, 1)})
    return out


def _meal_dict(store: Store, row) -> dict:
    keys = row.keys()
    items = [{
        "id": r["id"], "alimento_id": r["food_id"], "descrizione": r["description"],
        "grammi": r["grams"], "carbo_g": r["carbs_g"],
    } for r in store.db.execute("SELECT * FROM meal_item WHERE meal_id = ? ORDER BY id", (row["id"],))]
    analisi_raw = row["analysis_json"] if "analysis_json" in keys else None
    try:
        analisi = json.loads(analisi_raw) if analisi_raw else None
    except ValueError:
        analisi = None
    return {"id": row["id"], "ora": row["ts_local"], "tipo": row["meal_type"],
            "note": row["notes"], "voci": items,
            "carbo_g": _round(sum(i["carbo_g"] for i in items)),
            "foto": foto_mod.list_photos(store, row["id"]),
            "creato_da": row["created_from"] if "created_from" in keys else None,
            "analisi_foto": analisi}


def get_meal(store: Store, meal_id: int) -> dict | None:
    row = store.db.execute("SELECT * FROM meal WHERE id = ?", (meal_id,)).fetchone()
    return _meal_dict(store, row) if row else None


def meals_between(store: Store, start: str, end: str) -> list[dict]:
    rows = store.db.execute("SELECT * FROM meal WHERE ts_local >= ? AND ts_local < ? ORDER BY ts_local",
                            (start, end))
    return [_meal_dict(store, r) for r in rows]


def save_meal(store: Store, data: dict, meal_id: int | None = None) -> dict:
    ts = _parse_ts(data.get("ora"))
    meal_type = data.get("tipo")
    if meal_type not in MEAL_TYPES:
        raise DiaryError("Scegli il tipo di pasto.")
    items = _clean_items(store, data.get("voci"))
    notes = (data.get("note") or "").strip() or None
    creato_da = data.get("creato_da")
    analisi = data.get("analisi_foto")
    analisi_json = json.dumps(analisi, ensure_ascii=False) if analisi is not None else None
    if meal_id is None:
        cur = store.db.execute(
            "INSERT INTO meal(ts_local, meal_type, notes, created_from, analysis_json) VALUES (?, ?, ?, ?, ?)",
            (ts, meal_type, notes, creato_da or "manual", analisi_json))
        meal_id = cur.lastrowid
    else:
        if not get_meal(store, meal_id):
            raise DiaryError("Pasto non trovato.")
        store.db.execute(
            "UPDATE meal SET ts_local = ?, meal_type = ?, notes = ?, "
            "created_from = COALESCE(?, created_from), "
            "analysis_json = COALESCE(?, analysis_json) WHERE id = ?",
            (ts, meal_type, notes, creato_da, analisi_json, meal_id))
        store.db.execute("DELETE FROM meal_item WHERE meal_id = ?", (meal_id,))
    store.db.executemany(
        "INSERT INTO meal_item(meal_id, food_id, description, grams, carbs_g) VALUES (?, ?, ?, ?, ?)",
        [(meal_id, i["food_id"], i["description"], i["grams"], i["carbs_g"]) for i in items])
    store.db.commit()
    return get_meal(store, meal_id)


def delete_meal(store: Store, meal_id: int) -> None:
    store.db.execute("DELETE FROM meal WHERE id = ?", (meal_id,))
    store.db.commit()


# -- note del giorno ----------------------------------------------------------

NOTE_KINDS = ("sintomo", "attivita", "malattia", "visita", "altro")


def _note_dict(row) -> dict:
    return {"id": row["id"], "ora": row["ts_local"], "testo": row["text"], "tipo": row["kind"]}


def notes_for_day(store: Store, day: str) -> list[dict]:
    rows = store.db.execute(
        "SELECT * FROM notes WHERE substr(ts_local, 1, 10) = ? ORDER BY ts_local", (day,))
    return [_note_dict(r) for r in rows]


def create_note(store: Store, data: dict) -> dict:
    testo = (data.get("testo") or "").strip()
    if not testo:
        raise DiaryError("Scrivi il testo della nota.")
    kind = data.get("tipo") or "altro"
    if kind not in NOTE_KINDS:
        raise DiaryError("Tipo di nota non valido.")
    ts = _parse_ts(data.get("ora")) if data.get("ora") else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = store.db.execute("INSERT INTO notes(ts_local, text, kind) VALUES (?, ?, ?)", (ts, testo, kind))
    store.db.commit()
    row = store.db.execute("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _note_dict(row)


def delete_note(store: Store, note_id: int) -> None:
    store.db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    store.db.commit()


# -- ricerca nel diario --------------------------------------------------------

def search_diary(store: Store, q: str, limit: int = 100) -> list[dict]:
    """'pizza' -> tutti i pasti (con la data) in cui compare quel testo o quell'alimento."""
    terms = [t for t in (q or "").split() if t]
    if not terms:
        return []
    where = " AND ".join("(mi.description LIKE ? OR IFNULL(f.name, '') LIKE ?)" for _ in terms)
    params = []
    for t in terms:
        params += [f"%{t}%", f"%{t}%"]
    rows = store.db.execute(
        f"SELECT DISTINCT m.id AS meal_id, m.ts_local, m.meal_type "
        f"FROM meal_item mi JOIN meal m ON m.id = mi.meal_id LEFT JOIN food f ON f.id = mi.food_id "
        f"WHERE {where} ORDER BY m.ts_local DESC LIMIT ?", (*params, limit))
    return [{"pasto_id": r["meal_id"], "giorno": r["ts_local"][:10], "ora": r["ts_local"][11:16],
             "tipo": r["meal_type"]} for r in rows]
