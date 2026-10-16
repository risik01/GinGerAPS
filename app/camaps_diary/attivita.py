"""Passi di oggi, letti da un'entita' di Home Assistant (es. da uno smartwatch
collegato con un'automazione del telefono). L'add-on chiama il proprio
Supervisor, che fa da proxy verso l'API di Home Assistant Core: servono le
opzioni 'hassio_api' e 'homeassistant_api' nel config.yaml.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .store import Store
from .timeutil import now_local

CORE_API = "http://supervisor/core/api"


class StepsError(Exception):
    """Errore nel leggere i passi da Home Assistant."""


def _fetch_state(token: str, entity_id: str) -> dict | None:
    if not token:
        raise StepsError("Supervisor non disponibile: manca SUPERVISOR_TOKEN nel container.")
    try:
        res = requests.get(f"{CORE_API}/states/{entity_id}", timeout=10, headers={
            "Authorization": f"Bearer {token}",
            "content-type": "application/json",
        })
    except requests.RequestException as exc:
        raise StepsError(f"Impossibile contattare Home Assistant: {exc}") from exc
    if res.status_code == 404:
        raise StepsError(f"Entita' '{entity_id}' non trovata in Home Assistant.")
    if res.status_code in (401, 403):
        raise StepsError("Permesso negato: nel config.yaml dell'add-on servono "
                         "'hassio_api: true' e 'homeassistant_api: true'.")
    if not res.ok:
        raise StepsError(f"Errore da Home Assistant ({res.status_code}).")
    return res.json()


def sync_today(store: Store, token: str, entity_id: str, tz: str) -> int | None:
    """Legge l'entita' e aggiorna il contatore passi del giorno locale corrente.

    L'entita' tipica (sensore modello su input_number aggiornato da un'automazione
    del telefono) riflette "i passi di oggi finora", ma il telefono non sempre la
    aggiorna subito dopo la mezzanotte: per diverse ore di fila puo' restare
    ferma al totale finale di ieri. Senza un controllo, quel valore vecchio
    verrebbe copiato come se fosse "i passi di oggi" (bug segnalato dall'utente:
    il contatore sembrava non essersi azzerato). Si usa quindi last_updated
    dell'entita' per sapere se e' davvero stata toccata oggi: se l'ultimo
    aggiornamento e' di ieri (o prima), i passi di oggi sono considerati non
    ancora disponibili, non il valore vecchio.
    """
    dati = _fetch_state(token, entity_id)
    state = dati.get("state")
    if state in (None, "unknown", "unavailable"):
        return None
    oggi = now_local(tz).date()
    last_updated = dati.get("last_updated") or dati.get("last_changed")
    if last_updated:
        try:
            t_agg = datetime.fromisoformat(last_updated.replace("Z", "+00:00")).astimezone(ZoneInfo(tz))
            if t_agg.date() < oggi:
                return None  # il sensore non e' ancora stato aggiornato per oggi
        except ValueError:
            pass
    try:
        passi = int(float(state))
    except (TypeError, ValueError):
        raise StepsError(f"Valore non numerico per '{entity_id}': {state!r}")
    giorno = oggi.isoformat()
    store.db.execute(
        "INSERT INTO activity_steps(giorno, passi, aggiornato_utc) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(giorno) DO UPDATE SET passi = excluded.passi, aggiornato_utc = excluded.aggiornato_utc",
        (giorno, passi))
    store.db.commit()
    return passi
