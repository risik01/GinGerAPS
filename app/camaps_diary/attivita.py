"""Passi di oggi, letti da un'entita' di Home Assistant (es. da uno smartwatch
collegato con un'automazione del telefono). L'add-on chiama il proprio
Supervisor, che fa da proxy verso l'API di Home Assistant Core: servono le
opzioni 'hassio_api' e 'homeassistant_api' nel config.yaml.
"""

import requests

from .store import Store
from .timeutil import now_local

CORE_API = "http://supervisor/core/api"


class StepsError(Exception):
    """Errore nel leggere i passi da Home Assistant."""


def _fetch_state(token: str, entity_id: str) -> str | None:
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
    return res.json().get("state")


def sync_today(store: Store, token: str, entity_id: str, tz: str) -> int | None:
    """Legge l'entita' e aggiorna il contatore passi del giorno locale corrente.

    L'entita' tipica (sensore modello su input_number aggiornato da un'automazione
    del telefono) riflette "i passi di oggi finora": se interroghiamo appena dopo
    la mezzanotte e il telefono non ha ancora risincronizzato, il valore potrebbe
    essere ancora quello di ieri per qualche minuto. Si autocorregge al giro dopo.
    """
    state = _fetch_state(token, entity_id)
    if state in (None, "unknown", "unavailable"):
        return None
    try:
        passi = int(float(state))
    except (TypeError, ValueError):
        raise StepsError(f"Valore non numerico per '{entity_id}': {state!r}")
    giorno = now_local(tz).date().isoformat()
    store.db.execute(
        "INSERT INTO activity_steps(giorno, passi, aggiornato_utc) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(giorno) DO UPDATE SET passi = excluded.passi, aggiornato_utc = excluded.aggiornato_utc",
        (giorno, passi))
    store.db.commit()
    return passi
