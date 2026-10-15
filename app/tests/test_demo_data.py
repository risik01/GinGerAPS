"""Test di Fase 6: dati sintetici della modalita' demo."""

from camaps_diary import demo_data


def test_seed_demo_data_popola_glicemie_boli_basale_e_pasti(store):
    demo_data.seed_demo_data(store)
    n_glucose = store.db.execute("SELECT COUNT(*) FROM glucose").fetchone()[0]
    n_bolus = store.db.execute("SELECT COUNT(*) FROM bolus").fetchone()[0]
    n_basal = store.db.execute("SELECT COUNT(*) FROM basal").fetchone()[0]
    n_meal = store.db.execute("SELECT COUNT(*) FROM meal").fetchone()[0]
    assert n_glucose > 1000  # ~30 giorni * ~288 letture/giorno
    assert n_bolus > 0
    assert n_basal > 0
    assert n_meal > 0
    assert store.get("demo_seminato") == "1"


def test_seed_demo_data_glicemie_in_un_intervallo_plausibile(store):
    demo_data.seed_demo_data(store)
    righe = store.db.execute("SELECT mgdl FROM glucose").fetchall()
    valori = [r["mgdl"] for r in righe]
    assert min(valori) >= 55
    assert max(valori) <= 260


def test_seed_demo_data_e_deterministica(store, tmp_path):
    from camaps_diary.store import Store
    demo_data.seed_demo_data(store)
    prima = [dict(r) for r in store.db.execute("SELECT * FROM glucose ORDER BY ts_local LIMIT 5")]

    store2 = Store(tmp_path / "altro.db")
    demo_data.seed_demo_data(store2)
    dopo = [dict(r) for r in store2.db.execute("SELECT * FROM glucose ORDER BY ts_local LIMIT 5")]
    store2.close()

    assert prima == dopo
