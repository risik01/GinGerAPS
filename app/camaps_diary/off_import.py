"""Import in blocco dei prodotti Open Food Facts venduti in Italia.

Open Food Facts non pubblica un export separato per paese: l'unico export
in blocco e' il file mondiale (circa 1,2 GB compresso). Qui lo si legge in
streaming (senza mai scriverlo per intero su disco) e si tengono solo le
righe taggate Italia con nome e carboidrati validi. Dati aperti, licenza
ODbL (Open Food Facts), citata nella fonte di ogni alimento importato.

E' un'operazione lunga (minuti, non secondi): gira in un thread a parte,
lo stato si legge da kv (off_import_stato/alimenti/errore).
"""

import csv
import gzip
import io
import logging

import requests

from . import __version__

CSV_URL = "https://static.openfoodfacts.org/data/en.openfoodfacts.org.products.csv.gz"
NEEDED_COLS = ("code", "product_name", "brands", "countries_en", "carbohydrates_100g",
               "sugars_100g", "fiber_100g", "proteins_100g", "fat_100g", "energy-kcal_100g")
LOG_EVERY = 200_000

log = logging.getLogger("diario")


class OffImportError(Exception):
    """Errore nell'importare i prodotti Open Food Facts."""


def _num(text: str, lo: float = 0.0, hi: float = 100.0):
    """Numero, scartato se fuori da un intervallo plausibile (i dati di OFF sono
    inseriti dagli utenti: capita un valore per 100 ml invece che per 100 g,
    un'unita' sbagliata, ecc.)."""
    if not text:
        return None
    try:
        v = float(text)
    except ValueError:
        return None
    if v != v:  # NaN
        return None
    return v if lo <= v <= hi else None


def import_italy(store, contact: str = "", log_fn=log.info, max_rows: int | None = None) -> dict:
    ua = f"DiarioCamAPS/{__version__} ({contact})" if contact else f"DiarioCamAPS/{__version__} (import Italia)"
    try:
        resp = requests.get(CSV_URL, headers={"User-Agent": ua}, stream=True, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise OffImportError(f"Impossibile scaricare l'export Open Food Facts: {exc}") from exc

    gz = gzip.GzipFile(fileobj=resp.raw)
    text = io.TextIOWrapper(gz, encoding="utf-8", errors="replace")
    csv.field_size_limit(10_000_000)  # alcuni campi di testo libero superano il limite predefinito (128 KB)
    reader = csv.reader(text, delimiter="\t")
    it = iter(reader)
    try:
        header = next(it)
    except StopIteration:
        raise OffImportError("L'export Open Food Facts e' vuoto o non leggibile.")
    idx = {name: i for i, name in enumerate(header)}
    missing = [c for c in NEEDED_COLS if c not in idx]
    if missing:
        raise OffImportError(f"Colonne mancanti nell'export Open Food Facts: {', '.join(missing)}")

    def cell(row, col):
        i = idx[col]
        return row[i].strip() if i < len(row) else ""

    rows, seen_codes, n, kept, scartate = [], set(), 0, 0, 0
    while True:
        try:
            row = next(it)
        except StopIteration:
            break
        except csv.Error:
            scartate += 1
            continue  # riga malformata nel dump: si salta e si riprende dalla successiva
        n += 1
        if max_rows and n > max_rows:
            break
        if n % LOG_EVERY == 0:
            log_fn(f"-> import Open Food Facts: {n} righe lette, {kept} italiane tenute finora")
        countries = cell(row, "countries_en")
        if "italy" not in countries.lower():
            continue
        name = cell(row, "product_name")
        carbs = _num(cell(row, "carbohydrates_100g"))
        if not name or carbs is None or not 0 <= carbs <= 100:
            continue
        code = cell(row, "code") or None
        if not code or code in seen_codes:
            continue  # senza codice a barre non si puo' deduplicare in un reimport
        seen_codes.add(code)
        sugars = _num(cell(row, "sugars_100g"))
        if sugars is not None and sugars > carbs + 2:
            sugars = None  # gli zuccheri non possono superare i carboidrati totali
        rows.append((
            name[:200], cell(row, "brands")[:200] or None, round(carbs, 2),
            sugars, _num(cell(row, "fiber_100g")),
            _num(cell(row, "proteins_100g")), _num(cell(row, "fat_100g")),
            _num(cell(row, "energy-kcal_100g"), hi=900), code,
        ))
        kept += 1

    if not rows:
        raise OffImportError("Nessun prodotto italiano valido trovato nell'export.")

    log_fn(f"-> import Open Food Facts: {n} righe lette, {scartate} scartate per formato, "
           f"{kept} alimenti italiani da salvare")
    store.db.executemany(
        "INSERT INTO food(name, brand, carbs_per_100g, sugars_per_100g, fiber_per_100g, "
        "protein_per_100g, fat_per_100g, kcal_per_100g, barcode, source, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'openfoodfacts_import', CURRENT_TIMESTAMP) "
        "ON CONFLICT(barcode) DO UPDATE SET "
        "name = excluded.name, brand = excluded.brand, carbs_per_100g = excluded.carbs_per_100g, "
        "sugars_per_100g = excluded.sugars_per_100g, fiber_per_100g = excluded.fiber_per_100g, "
        "protein_per_100g = excluded.protein_per_100g, fat_per_100g = excluded.fat_per_100g, "
        "kcal_per_100g = excluded.kcal_per_100g, updated_at = CURRENT_TIMESTAMP "
        "WHERE food.source = 'openfoodfacts_import'",
        rows)
    store.db.commit()
    return {"righe_lette": n, "righe_scartate": scartate, "alimenti_italiani": kept}


def count(store) -> int:
    return store.db.execute(
        "SELECT COUNT(*) FROM food WHERE source = 'openfoodfacts_import'").fetchone()[0]
