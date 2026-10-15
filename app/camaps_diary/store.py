"""Database SQLite del diario.

Le tabelle glucose / bolus / basal / camaps_mode / device_event arrivano da
Glooko; food / meal / meal_item sono il diario pasti inserito nell'app.
Gli orari sono salvati in ora locale (ts_local) e in UTC (ts_utc).
"""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS glucose (
    ts_local TEXT PRIMARY KEY,
    ts_utc   TEXT NOT NULL,
    mgdl     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bolus (
    guid               TEXT PRIMARY KEY,
    ts_local           TEXT NOT NULL,
    ts_utc             TEXT NOT NULL,
    insulin_delivered  REAL NOT NULL DEFAULT 0,
    insulin_programmed REAL,
    carbs_input        REAL NOT NULL DEFAULT 0,
    rec_carbs          REAL,
    rec_correction     REAL,
    rec_total          REAL,
    insulin_on_board   REAL,
    bolus_type         TEXT,
    soft_deleted       INTEGER NOT NULL DEFAULT 0,
    updated_at         TEXT,
    raw                TEXT
);
CREATE INDEX IF NOT EXISTS bolus_ts ON bolus(ts_local);
CREATE TABLE IF NOT EXISTS basal (
    guid         TEXT PRIMARY KEY,
    ts_local     TEXT NOT NULL,
    ts_utc       TEXT NOT NULL,
    duration_s   INTEGER NOT NULL,
    rate         REAL NOT NULL,
    soft_deleted INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS basal_ts ON basal(ts_local);
CREATE TABLE IF NOT EXISTS camaps_mode (
    ts_local   TEXT NOT NULL,
    mode       TEXT NOT NULL,
    duration_s INTEGER NOT NULL,
    PRIMARY KEY (ts_local, mode)
);
CREATE TABLE IF NOT EXISTS device_event (
    ts_local TEXT NOT NULL,
    kind     TEXT NOT NULL,
    PRIMARY KEY (ts_local, kind)
);
CREATE TABLE IF NOT EXISTS food (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    carbs_per_100g REAL NOT NULL,
    source         TEXT,
    barcode        TEXT UNIQUE,
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS food_name ON food(name);
CREATE TABLE IF NOT EXISTS meal (
    id         INTEGER PRIMARY KEY,
    ts_local   TEXT NOT NULL,
    meal_type  TEXT NOT NULL,
    notes      TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS meal_ts ON meal(ts_local);
CREATE TABLE IF NOT EXISTS meal_item (
    id          INTEGER PRIMARY KEY,
    meal_id     INTEGER NOT NULL REFERENCES meal(id) ON DELETE CASCADE,
    food_id     INTEGER REFERENCES food(id),
    description TEXT,
    grams       REAL,
    carbs_g     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS meal_photo (
    id             INTEGER PRIMARY KEY,
    meal_id        INTEGER NOT NULL REFERENCES meal(id) ON DELETE CASCADE,
    filename       TEXT NOT NULL,
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    analisi_stato  TEXT,
    analisi_json   TEXT,
    analisi_errore TEXT
);
CREATE INDEX IF NOT EXISTS meal_photo_meal ON meal_photo(meal_id);
CREATE TABLE IF NOT EXISTS activity_steps (
    giorno         TEXT PRIMARY KEY,
    passi          INTEGER NOT NULL,
    aggiornato_utc TEXT
);
CREATE TABLE IF NOT EXISTS recipe_item (
    id          INTEGER PRIMARY KEY,
    food_id     INTEGER NOT NULL REFERENCES food(id) ON DELETE CASCADE,
    ingredient_food_id INTEGER REFERENCES food(id),
    description TEXT,
    grams       REAL,
    carbs_g     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS recipe_item_food ON recipe_item(food_id);
CREATE TABLE IF NOT EXISTS notes (
    id       INTEGER PRIMARY KEY,
    ts_local TEXT NOT NULL,
    text     TEXT NOT NULL,
    kind     TEXT NOT NULL DEFAULT 'altro'
);
CREATE INDEX IF NOT EXISTS notes_ts ON notes(ts_local);
CREATE TABLE IF NOT EXISTS report (
    id          INTEGER PRIMARY KEY,
    created_utc TEXT NOT NULL,
    day_from    TEXT NOT NULL,
    day_to      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    filename    TEXT NOT NULL
);
"""


# Colonne aggiunte dopo la prima versione: (tabella, colonna, definizione)
MIGRATIONS = (
    ("food", "brand", "TEXT"),
    ("food", "portion_g", "REAL"),
    ("food", "updated_at", "TEXT"),
    ("food", "ref_code", "TEXT"),
    ("food", "sugars_per_100g", "REAL"),
    ("food", "fiber_per_100g", "REAL"),
    ("food", "protein_per_100g", "REAL"),
    ("food", "fat_per_100g", "REAL"),
    ("food", "kcal_per_100g", "REAL"),
    ("food", "default_portions", "TEXT"),
    ("food", "servings", "INTEGER"),
    ("meal", "created_from", "TEXT"),
    ("meal", "analysis_json", "TEXT"),
)


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)
        self._migrate()
        from .ciqual import ensure_schema
        ensure_schema(self)

    def _migrate(self):
        for table, column, definition in MIGRATIONS:
            cols = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        self.db.commit()

    def close(self):
        self.db.close()

    # -- chiave/valore (cursori di sync, impostazioni) ------------------------

    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value) -> None:
        self.db.execute("INSERT INTO kv(key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, None if value is None else str(value)))
        self.db.commit()

    # -- upsert ---------------------------------------------------------------

    def _upsert(self, table: str, rows: list, key_cols: tuple) -> int:
        if not rows:
            return 0
        cols = list(rows[0].keys())
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in key_cols)
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
               f"ON CONFLICT({', '.join(key_cols)}) DO UPDATE SET {updates}")
        self.db.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        self.db.commit()
        return len(rows)

    def upsert_glucose(self, rows):
        return self._upsert("glucose", rows, ("ts_local",))

    def upsert_bolus(self, rows):
        return self._upsert("bolus", rows, ("guid",))

    def upsert_basal(self, rows):
        return self._upsert("basal", rows, ("guid",))

    def upsert_modes(self, rows):
        return self._upsert("camaps_mode", rows, ("ts_local", "mode"))

    def upsert_events(self, rows):
        if not rows:
            return 0
        self.db.executemany("INSERT OR IGNORE INTO device_event(ts_local, kind) VALUES (?, ?)",
                            [(r["ts_local"], r["kind"]) for r in rows])
        self.db.commit()
        return len(rows)

    def last_glucose_local(self):
        row = self.db.execute("SELECT MAX(ts_local) AS m FROM glucose").fetchone()
        return row["m"] if row else None

    def counts(self) -> dict:
        return {t: self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("glucose", "bolus", "basal", "camaps_mode", "device_event",
                          "food", "meal", "meal_photo")}
