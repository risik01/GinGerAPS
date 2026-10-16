"""Foto dei pasti: salvataggio su disco e stima nutrizionale via Claude (Anthropic).

Non importa diary.py (eviterebbe un ciclo: diary include le foto nel pasto).
La stima IA e' sempre una bozza: chi usa l'app decide se e come riportarla
nei carboidrati del pasto, non viene mai scritta da sola.
"""

import base64
import io
import json
import uuid
from pathlib import Path

import anthropic
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from .errors import DiaryError
from .store import Store

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:  # pragma: no cover - fallback se il pacchetto non c'e'
    pass

MAX_DIM = 1200
JPEG_QUALITY = 85
DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 2500
TIMEOUT_S = 30.0


class FoodItem(BaseModel):
    name_it: str = Field(description="Nome generico in italiano, es. 'pasta al pomodoro' (non marche)")
    category: str = Field(description="primo|secondo|contorno|pane|frutta|dolce|bevanda|altro")
    portion_label: str = Field(description='Es. "piatto medio", "2 fette", "una ciotola"')
    grams_estimate: int
    grams_min: int
    grams_max: int
    carbs_g: float = Field(description="Carboidrati stimati per la porzione (grams_estimate), non per 100 g")
    kcal: float = Field(ge=0, description="Calorie stimate per la porzione (grams_estimate)")
    protein_g: float = Field(ge=0, description="Proteine in grammi per la porzione")
    fat_g: float = Field(ge=0, description="Grassi totali in grammi per la porzione, condimenti compresi")
    sugars_g: float = Field(ge=0, description="Zuccheri semplici in grammi per la porzione (compresi nei carboidrati)")
    fiber_g: float = Field(ge=0, description="Fibre in grammi per la porzione")
    confidence: float = Field(ge=0, le=1)
    reason_short: str = Field(description="Massimo una frase: perche' questa stima")


class PhotoAnalysis(BaseModel):
    items: list[FoodItem]
    reference_object: str = Field(description='"forchetta"|"mano"|"carta"|"nessuno"')
    plate_size_guess: str = Field(description='"piccolo"|"medio"|"grande"|"sconosciuto"')
    warnings: list[str] = Field(default_factory=list)


SYSTEM_PROMPT_IT = (
    "Sei un assistente per il conteggio dei carboidrati di una persona con diabete di tipo 1 in Italia. "
    "Conosci la cucina italiana e le porzioni tipiche italiane.\n\n"
    "Regole:\n"
    "- Elenca ogni alimento visibile separatamente, con nomi generici italiani (non marche commerciali).\n"
    "- Stima i grammi usando come riferimento piatto, posate o mani visibili nella foto.\n"
    "- Dai sempre un intervallo min-max oltre alla stima puntuale.\n"
    "- Sii prudente e onesto sulla affidabilita': se il piatto e' parzialmente nascosto, sfocato, o il "
    "condimento non e' valutabile, abbassa la confidenza e scrivilo in reason_short o in warnings.\n"
    "- Calcola i carboidrati sulla porzione stimata usando valori medi delle tabelle nutrizionali italiane.\n"
    "- Per ogni alimento stima anche calorie, proteine, grassi (compresi olio e condimenti visibili), zuccheri "
    "e fibre della stessa porzione, con le stesse tabelle. Gli zuccheri sono compresi nei carboidrati. "
    "Le calorie devono essere coerenti con i macronutrienti (circa 4 kcal/g carboidrati e proteine, 9 kcal/g grassi).\n"
    "- Se la foto non mostra cibo, o e' troppo sfocata/vuota per dire qualcosa, restituisci una lista "
    "items vuota e spiega il motivo in warnings: non inventare alimenti per riempire la risposta.\n"
    "- Non dare MAI consigli su insulina, dosi, boli o terapia. Non commentare la salute della persona: "
    "il tuo unico compito e' stimare cosa c'e' nel piatto e i suoi valori nutrizionali."
)


def _build_user_prompt(hint: str | None) -> str:
    base = "Analizza la foto di questo pasto e stima gli alimenti presenti con i relativi carboidrati e valori nutrizionali."
    if hint and hint.strip():
        base += (f"\n\nLa persona ha scritto: \"{hint.strip()}\". Usa questa informazione come prioritaria: "
                  "se dice che si tratta di una pizza intera, non interpretarla come due fette.")
    return base


class VisionError(Exception):
    """Errore nel chiamare o interpretare la risposta di Claude."""


# Prezzi indicativi in USD per milione di token (input, output): possono cambiare,
# qui servono solo per una stima approssimativa mostrata all'utente, non per la fattura.
PREZZI_INDICATIVI = {
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-opus-4-8": (15.0, 75.0),
}


def _mese_corrente() -> str:
    import datetime
    return datetime.date.today().strftime("%Y-%m")


def _registra_costo(store: Store, model: str, usage) -> None:
    """Contatore 'analisi questo mese' con stima di costo, si azzera da solo al cambio mese."""
    mese = _mese_corrente()
    try:
        dati = json.loads(store.get("ia_foto_contatore") or "{}")
    except ValueError:
        dati = {}
    if dati.get("mese") != mese:
        dati = {"mese": mese, "n": 0, "costo_usd": 0.0}
    prezzo_in, prezzo_out = PREZZI_INDICATIVI.get(model, PREZZI_INDICATIVI[DEFAULT_MODEL])
    costo = (getattr(usage, "input_tokens", 0) or 0) / 1_000_000 * prezzo_in
    costo += (getattr(usage, "output_tokens", 0) or 0) / 1_000_000 * prezzo_out
    dati["n"] = dati.get("n", 0) + 1
    dati["costo_usd"] = round(dati.get("costo_usd", 0.0) + costo, 4)
    store.set("ia_foto_contatore", json.dumps(dati))


def costo_mese(store: Store) -> dict:
    try:
        dati = json.loads(store.get("ia_foto_contatore") or "{}")
    except ValueError:
        dati = {}
    if dati.get("mese") != _mese_corrente():
        return {"mese": _mese_corrente(), "n": 0, "costo_usd": 0.0}
    return dati


def _meal_exists(store: Store, meal_id: int) -> bool:
    return store.db.execute("SELECT 1 FROM meal WHERE id = ?", (meal_id,)).fetchone() is not None


def _process_image(data: bytes) -> bytes:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
    except Exception as exc:
        raise DiaryError("Il file non e' una foto leggibile.") from exc
    img.thumbnail((MAX_DIM, MAX_DIM))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def save_photo(store: Store, photos_dir: Path, meal_id: int, data: bytes) -> dict:
    if not _meal_exists(store, meal_id):
        raise DiaryError("Pasto non trovato.")
    jpeg = _process_image(data)
    filename = f"{uuid.uuid4().hex}.jpg"
    photos_dir.mkdir(parents=True, exist_ok=True)
    (photos_dir / filename).write_bytes(jpeg)
    cur = store.db.execute("INSERT INTO meal_photo(meal_id, filename) VALUES (?, ?)", (meal_id, filename))
    store.db.commit()
    return get_photo(store, cur.lastrowid)


def _photo_dict(row) -> dict:
    analisi = json.loads(row["analisi_json"]) if row["analisi_json"] else None
    return {
        "id": row["id"],
        "pasto_id": row["meal_id"],
        "filename": row["filename"],
        "stato_analisi": row["analisi_stato"] or "nessuna",
        "errore_analisi": row["analisi_errore"],
        "analisi": analisi,
    }


def _photo_row(store: Store, photo_id: int):
    return store.db.execute("SELECT * FROM meal_photo WHERE id = ?", (photo_id,)).fetchone()


def get_photo(store: Store, photo_id: int) -> dict | None:
    row = _photo_row(store, photo_id)
    return _photo_dict(row) if row else None


def list_photos(store: Store, meal_id: int) -> list[dict]:
    rows = store.db.execute("SELECT * FROM meal_photo WHERE meal_id = ? ORDER BY id", (meal_id,))
    return [_photo_dict(r) for r in rows]


def delete_photo(store: Store, photos_dir: Path, photo_id: int) -> None:
    row = _photo_row(store, photo_id)
    if not row:
        return
    (photos_dir / row["filename"]).unlink(missing_ok=True)
    store.db.execute("DELETE FROM meal_photo WHERE id = ?", (photo_id,))
    store.db.commit()


def _set_analysis(store: Store, photo_id: int, stato: str, analisi: dict | None, errore: str | None) -> dict | None:
    store.db.execute(
        "UPDATE meal_photo SET analisi_stato = ?, analisi_json = ?, analisi_errore = ? WHERE id = ?",
        (stato, json.dumps(analisi, ensure_ascii=False) if analisi is not None else None, errore, photo_id))
    store.db.commit()
    return get_photo(store, photo_id)


def _call_claude(api_key: str, model: str, image_bytes: bytes, hint: str | None,
                  client: "anthropic.Anthropic | None" = None) -> PhotoAnalysis:
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    client = client or anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT_S)
    kwargs = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT_IT,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": _build_user_prompt(hint)},
            ],
        }],
        output_format=PhotoAnalysis,
    )
    retried = False
    while True:
        try:
            resp = client.messages.parse(**kwargs)
            break
        except anthropic.AuthenticationError as exc:
            raise VisionError("Chiave API Anthropic non valida: controllala nella configurazione dell'add-on.") from exc
        except anthropic.RateLimitError as exc:
            raise VisionError("Limite di richieste Anthropic raggiunto: riprova tra poco.") from exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            if retried:
                raise VisionError(f"Impossibile contattare Claude dopo un tentativo di nuovo: {exc}") from exc
            retried = True
            continue
        except anthropic.APIStatusError as exc:
            raise VisionError(f"Errore da Claude ({exc.status_code}): {str(exc)[:200]}") from exc

    if resp.stop_reason == "refusal":
        raise VisionError("Il modello si e' rifiutato di analizzare questa foto.")
    for block in resp.content:
        if getattr(block, "type", None) == "text" and getattr(block, "parsed_output", None) is not None:
            return block.parsed_output, resp.usage
    raise VisionError("Risposta inattesa da Claude: nessuna stima strutturata nel risultato.")


def analyze(store: Store, photos_dir: Path, photo_id: int, api_key: str, model: str,
            hint: str | None = None) -> dict:
    row = _photo_row(store, photo_id)
    if not row:
        raise DiaryError("Foto non trovata.")
    _set_analysis(store, photo_id, "in_corso", None, None)
    try:
        if not api_key:
            raise VisionError("Aggiungi la chiave API Anthropic nella configurazione dell'add-on per usare questa funzione.")
        path = photos_dir / row["filename"]
        try:
            image_bytes = path.read_bytes()
        except OSError as exc:
            raise VisionError("Il file della foto non e' piu' disponibile.") from exc
        model = model or DEFAULT_MODEL
        stima, usage = _call_claude(api_key, model, image_bytes, hint)
    except VisionError as exc:
        _set_analysis(store, photo_id, "errore", None, str(exc))
        raise
    _registra_costo(store, model, usage)
    return _set_analysis(store, photo_id, "ok", stima.model_dump(), None)
