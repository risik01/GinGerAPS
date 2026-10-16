"""Nutrienti oltre ai carboidrati: calorie, proteine, grassi, zuccheri, fibre.

Ogni voce di un pasto conserva i propri valori (calcolati al salvataggio dai
valori per 100 g dell'alimento, oppure indicati a mano / dalla stima IA).
Un valore sconosciuto resta None e non viene mai trattato come zero: i totali
sommano solo i valori noti e dicono quante voci non avevano il dato, cosi'
un totale incompleto non passa per completo.
"""

# chiave API -> (colonna in meal_item/recipe_item, colonna per 100 g in food)
NUTRIENTI = {
    "kcal": ("kcal", "kcal_per_100g"),
    "proteine": ("protein_g", "protein_per_100g"),
    "grassi": ("fat_g", "fat_per_100g"),
    "zuccheri": ("sugars_g", "sugars_per_100g"),
    "fibra": ("fiber_g", "fiber_per_100g"),
}

# Chiavi dei valori per 100 g nel dizionario di un alimento (vedi diary._food_dict)
CHIAVI_100G = {
    "kcal": "kcal_100g",
    "proteine": "proteine_100g",
    "grassi": "grassi_100g",
    "zuccheri": "zuccheri_100g",
    "fibra": "fibra_100g",
}


def per_100g(food: dict) -> dict:
    return {k: food.get(CHIAVI_100G[k]) for k in NUTRIENTI}


def scala(per100: dict, grammi) -> dict:
    """Valori per `grammi` di alimento; None dove manca il dato per 100 g."""
    if not grammi or grammi <= 0:
        return {k: None for k in NUTRIENTI}
    return {k: (None if v is None else round(float(v) * grammi / 100, 1)) for k, v in per100.items()}


def pulisci(valori) -> dict:
    """Valori indicati dall'utente o dall'IA per una voce libera: numeri >= 0 oppure None."""
    out = {}
    valori = valori if isinstance(valori, dict) else {}
    for k in NUTRIENTI:
        v = valori.get(k)
        if v in (None, ""):
            out[k] = None
            continue
        try:
            n = float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            out[k] = None
            continue
        out[k] = round(n, 1) if 0 <= n <= 5000 else None
    return out


def da_riga(row) -> dict:
    """Valori di una riga meal_item/recipe_item."""
    keys = row.keys()
    return {k: (row[col] if col in keys else None) for k, (col, _) in NUTRIENTI.items()}


def somma(voci: list[dict]) -> dict:
    """Totale su una lista di voci [{carbo_g, nutrienti: {...}}].

    Restituisce {carbo, kcal, proteine, ..., senza_dato: {kcal: n, ...}, voci: n}.
    """
    tot = {"carbo": round(sum((v.get("carbo_g") or 0) for v in voci), 1)}
    senza = {}
    for k in NUTRIENTI:
        noti = [v["nutrienti"][k] for v in voci if v["nutrienti"].get(k) is not None]
        tot[k] = round(sum(noti), 1) if noti else None
        senza[k] = len(voci) - len(noti)
    tot["senza_dato"] = senza
    tot["voci"] = len(voci)
    return tot


def somma_totali(totali: list[dict]) -> dict:
    """Totale su piu' totali gia' calcolati (es. i pasti di un giorno)."""
    out = {"carbo": round(sum(t["carbo"] for t in totali), 1), "voci": sum(t["voci"] for t in totali)}
    senza = {}
    for k in NUTRIENTI:
        noti = [t[k] for t in totali if t[k] is not None]
        out[k] = round(sum(noti), 1) if noti else None
        senza[k] = sum(t["senza_dato"][k] for t in totali)
    out["senza_dato"] = senza
    return out
