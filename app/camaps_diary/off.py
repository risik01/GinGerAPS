"""Ricerca prodotti su Open Food Facts (dati aperti, licenza ODbL).

Limiti del servizio: 10 ricerche e 15 letture prodotto al minuto per IP.
Qui si resta sotto i limiti con un intervallo minimo tra le chiamate e una
cache in memoria.
"""

import logging
import threading
import time

import requests

from . import __version__

SEARCH_URL = "https://it.openfoodfacts.org/cgi/search.pl"
PRODUCT_URL = "https://world.openfoodfacts.org/api/v2/product/{code}"
FIELDS = "code,product_name,product_name_it,brands,nutriments,serving_quantity,quantity"
MIN_SEARCH_INTERVAL = 7.0
MIN_PRODUCT_INTERVAL = 4.5
CACHE_TTL = 3600

log = logging.getLogger("diario")


class OffError(Exception):
    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


UNREACHABLE = "Open Food Facts non è raggiungibile in questo momento. Riprova tra qualche minuto."


class OpenFoodFacts:
    def __init__(self, contact: str = ""):
        ua = f"DiarioCamAPS/{__version__}"
        ua += f" ({contact})" if contact else " (uso personale)"
        self.http = requests.Session()
        self.http.headers["User-Agent"] = ua
        self._lock = threading.Lock()
        self._last = {"search": 0.0, "product": 0.0}
        self._cache = {}

    def _wait_slot(self, kind: str, interval: float):
        with self._lock:
            wait = self._last[kind] + interval - time.monotonic()
            if wait > 0:
                raise OffError(f"Troppe ricerche ravvicinate: riprova tra {int(wait) + 1} secondi.", 429)
            self._last[kind] = time.monotonic()

    def _cached(self, key):
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            return hit[1]
        return None

    @staticmethod
    def _product(p: dict) -> dict | None:
        carbs = (p.get("nutriments") or {}).get("carbohydrates_100g")
        name = (p.get("product_name_it") or p.get("product_name") or "").strip()
        if carbs is None or not name:
            return None
        try:
            carbs = round(float(carbs), 1)
        except (TypeError, ValueError):
            return None
        serving = p.get("serving_quantity")
        try:
            serving = round(float(serving)) if serving else None
        except (TypeError, ValueError):
            serving = None
        nutr = p.get("nutriments") or {}

        def num(key, hi=1000):
            try:
                v = float(nutr.get(key))
            except (TypeError, ValueError):
                return None
            return round(v, 1) if 0 <= v <= hi else None

        zuccheri = num("sugars_100g", 100)
        if zuccheri is not None and zuccheri > carbs + 2:
            zuccheri = None  # gli zuccheri non possono superare i carboidrati totali
        return {
            "barcode": p.get("code"),
            "nome": name,
            "marca": (p.get("brands") or "").split(",")[0].strip() or None,
            "carbo_100g": carbs,
            "zuccheri_100g": zuccheri,
            "fibra_100g": num("fiber_100g", 100),
            "proteine_100g": num("proteins_100g", 100),
            "grassi_100g": num("fat_100g", 100),
            "kcal_100g": num("energy-kcal_100g", 900),
            "porzione_g": serving,
            "confezione": p.get("quantity"),
            "fonte": "openfoodfacts",
        }

    def search(self, query: str) -> list[dict]:
        query = query.strip()
        if len(query) < 3:
            raise OffError("Scrivi almeno 3 lettere per cercare su Open Food Facts.", 400)
        key = ("s", query.lower())
        cached = self._cached(key)
        if cached is not None:
            return cached
        self._wait_slot("search", MIN_SEARCH_INTERVAL)
        try:
            resp = self.http.get(SEARCH_URL, timeout=20, params={
                "search_terms": query, "search_simple": 1, "action": "process",
                "json": 1, "page_size": 24, "fields": FIELDS})
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("Open Food Facts: %s", exc)
            raise OffError(UNREACHABLE) from exc
        results = [r for r in (self._product(p) for p in data.get("products") or []) if r]
        self._cache[key] = (time.monotonic(), results)
        return results

    def barcode(self, code: str) -> dict | None:
        code = "".join(ch for ch in code if ch.isdigit())
        if len(code) < 6:
            raise OffError("Il codice a barre deve avere almeno 6 cifre.", 400)
        key = ("p", code)
        cached = self._cached(key)
        if cached is not None:
            return cached
        self._wait_slot("product", MIN_PRODUCT_INTERVAL)
        try:
            resp = self.http.get(PRODUCT_URL.format(code=code), params={"fields": FIELDS}, timeout=20)
            if resp.status_code == 404:
                result = None
            else:
                resp.raise_for_status()
                data = resp.json()
                result = self._product(data.get("product") or {}) if data.get("status") == 1 else None
        except (requests.RequestException, ValueError) as exc:
            log.warning("Open Food Facts: %s", exc)
            raise OffError(UNREACHABLE) from exc
        self._cache[key] = (time.monotonic(), result)
        return result
