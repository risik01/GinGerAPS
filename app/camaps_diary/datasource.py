"""Interfaccia comune alle sorgenti di dati CamAPS (Fase 6, principio della
sezione 1 della roadmap: "sorgenti dati intercambiabili").

Ogni sorgente sa solo RECUPERARE righe grezze in un periodo, pronte per
`Store.upsert_glucose/upsert_bolus/upsert_basal` (stesse colonne delle tabelle,
vedi store.py): la normalizzazione nello schema resta lì, cosi' `sync.py` puo'
orchestrare qualunque sorgente nello stesso modo.

Glooko resta un caso speciale: usa un meccanismo di sincronizzazione
incrementale a cursore (GUID + timestamp) molto piu' efficiente di un fetch
a intervallo, ed e' l'unica sorgente con accesso reale alla basale automatica
del microinfusore. Il suo adattatore (`GlookoDataSource` in sync.py) esiste
soprattutto per `status()` e per la selezione in Impostazioni; la sync vera
e propria di Glooko continua a usare `sync.sync()` direttamente, inalterata,
per non rischiare di rompere l'unica sorgente che l'utente usa davvero oggi.

Non tutte le sorgenti hanno tutti i dati: Nightscout e xDrip+ non espongono
necessariamente la basale automatica (dipende dal sistema AID collegato), e
xDrip+ di per se' non ha boli/carboidrati (e' un follower CGM). `fetch_basals`
e `fetch_boluses` hanno quindi un default che restituisce lista vuota, non
sono obbligatori da sovrascrivere.
"""

from abc import ABC, abstractmethod
from datetime import datetime


class DataSourceError(Exception):
    """Errore nel contattare o leggere la sorgente dati."""


class DataSource(ABC):
    kind: str = "generico"

    @abstractmethod
    def fetch_glucose(self, start: datetime, end: datetime) -> list[dict]:
        """Righe pronte per Store.upsert_glucose: ts_local, ts_utc, mgdl."""

    def fetch_boluses(self, start: datetime, end: datetime) -> list[dict]:
        """Righe pronte per Store.upsert_bolus. Vuoto se la sorgente non ha boli."""
        return []

    def fetch_basals(self, start: datetime, end: datetime) -> list[dict]:
        """Righe pronte per Store.upsert_basal. Vuoto se la sorgente non ha la basale."""
        return []

    @abstractmethod
    def status(self) -> dict:
        """{"ok": bool, "errore": str | None}: usato sia per il test di
        connessione in Impostazioni sia per /api/stato."""
