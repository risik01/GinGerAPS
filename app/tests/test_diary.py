"""Test delle funzioni di diary.py: alimenti, porzioni, frequenti, ricette, pasti."""

import pytest

from camaps_diary import diary


def test_create_food_round_trip_con_porzioni(store):
    food = diary.create_food(store, {
        "nome": "Pasta", "carbo_100g": "75",
        "porzioni": [{"label": "piatto", "grammi": "80"}, {"label": "piatto abbondante", "grammi": 120}],
    })
    assert food["carbo_100g"] == 75.0
    assert food["porzioni"] == [{"label": "piatto", "grammi": 80.0}, {"label": "piatto abbondante", "grammi": 120.0}]

    ricaricato = diary.get_food(store, food["id"])
    assert ricaricato["porzioni"] == food["porzioni"]


class TestSearchFoods:
    """Ricerca testuale via FTS5 (vedi store.py): prefisso per ogni parola,
    non più una scansione LIKE su tutta la tabella."""

    def test_trova_per_prefisso(self, store):
        diary.create_food(store, {"nome": "Pasta integrale", "carbo_100g": 70})
        diary.create_food(store, {"nome": "Patate al forno", "carbo_100g": 17})
        risultati = diary.search_foods(store, "past")
        nomi = [f["nome"] for f in risultati]
        assert "Pasta integrale" in nomi
        assert "Patate al forno" not in nomi  # "pat" non e' un prefisso di "patate" cercando "past"

    def test_due_parole_richiedono_entrambi_i_prefissi(self, store):
        diary.create_food(store, {"nome": "Pasta al pomodoro", "carbo_100g": 25})
        diary.create_food(store, {"nome": "Pasta al pesto", "carbo_100g": 30})
        risultati = diary.search_foods(store, "pasta pom")
        nomi = [f["nome"] for f in risultati]
        assert nomi == ["Pasta al pomodoro"]

    def test_non_sensibile_ad_accenti_e_maiuscole(self, store):
        diary.create_food(store, {"nome": "Caffè macchiato", "carbo_100g": 1})
        assert len(diary.search_foods(store, "caffe")) == 1
        assert len(diary.search_foods(store, "CAFFÈ")) == 1

    def test_cerca_anche_nella_marca(self, store):
        diary.create_food(store, {"nome": "Biscotti frollini", "marca": "Mulino Bianco", "carbo_100g": 60})
        assert len(diary.search_foods(store, "mulino")) == 1

    def test_modifica_alimento_aggiorna_la_ricerca(self, store):
        food = diary.create_food(store, {"nome": "Yogurt bianco", "carbo_100g": 5})
        assert len(diary.search_foods(store, "bianco")) == 1
        diary.update_food(store, food["id"], {"nome": "Yogurt alla vaniglia", "carbo_100g": 12})
        assert diary.search_foods(store, "bianco") == []
        assert len(diary.search_foods(store, "vaniglia")) == 1

    def test_eliminare_alimento_lo_rimuove_dalla_ricerca(self, store):
        food = diary.create_food(store, {"nome": "Mortadella IGP", "carbo_100g": 1})
        assert len(diary.search_foods(store, "mortadella")) == 1
        diary.delete_food(store, food["id"])
        assert diary.search_foods(store, "mortadella") == []

    def test_nessun_risultato_restituisce_lista_vuota(self, store):
        diary.create_food(store, {"nome": "Pane", "carbo_100g": 50})
        assert diary.search_foods(store, "zzznonesistente") == []

    def test_backfill_indicizza_le_righe_inserite_prima_che_lo_store_esistesse(self, tmp_path):
        """Simula un database con righe food gia' presenti (es. da un vecchio
        import in blocco) prima che la tabella FTS venga creata/popolata per
        la prima volta: deve restare possibile cercarle dopo un riavvio."""
        import sqlite3
        from camaps_diary.store import Store

        db_path = tmp_path / "vecchio.db"
        # crea solo la tabella food "a mano", come se venisse da un bulk import
        # fatto prima che esistesse food_fts (o con un INSERT diretto, non via API)
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE food (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                   "carbs_per_100g REAL NOT NULL, source TEXT, barcode TEXT UNIQUE, "
                   "created_at TEXT DEFAULT CURRENT_TIMESTAMP, brand TEXT)")
        con.execute("INSERT INTO food(id, name, carbs_per_100g) VALUES (1, 'Pasta integrale', 70)")
        con.commit()
        con.close()

        store = Store(db_path)  # prima apertura: deve far partire il backfill
        try:
            risultati = diary.search_foods(store, "pasta")
            assert len(risultati) == 1
            assert risultati[0]["nome"] == "Pasta integrale"
        finally:
            store.close()

    def test_query_vuota_ordina_per_uso(self, store):
        pane = diary.create_food(store, {"nome": "Pane", "carbo_100g": 50})
        diary.create_food(store, {"nome": "Pasta", "carbo_100g": 75})
        diary.save_meal(store, {"ora": "2026-10-15T08:00", "tipo": "colazione",
                                "voci": [{"alimento_id": pane["id"], "grammi": 50}]})
        risultati = diary.search_foods(store, "")
        assert risultati[0]["nome"] == "Pane"  # il piu' usato viene prima


def test_meal_senza_voci_e_permesso(store):
    """Un pasto puo' nascere con solo una foto: zero alimenti non deve fallire."""
    pasto = diary.save_meal(store, {"ora": "2026-10-15T08:00", "tipo": "colazione", "voci": []})
    assert pasto["voci"] == []
    assert pasto["carbo_g"] == 0


def test_voce_libera_senza_grammi_rifiutata_senza_descrizione(store):
    with pytest.raises(diary.DiaryError):
        diary.save_meal(store, {"ora": "2026-10-15T08:00", "tipo": "colazione",
                                "voci": [{"descrizione": "", "carbo_g": 10}]})


def test_frequenti_ordina_per_usi_e_recenza(store):
    pane = diary.create_food(store, {"nome": "Pane", "carbo_100g": 50})
    mela = diary.create_food(store, {"nome": "Mela", "carbo_100g": 12})

    # Pane usato 2 volte, Mela usata 1 volta: Pane deve venire prima
    diary.save_meal(store, {"ora": "2026-10-13T08:00", "tipo": "colazione",
                            "voci": [{"alimento_id": pane["id"], "grammi": 50}]})
    diary.save_meal(store, {"ora": "2026-10-14T08:00", "tipo": "colazione",
                            "voci": [{"alimento_id": pane["id"], "grammi": 50}]})
    diary.save_meal(store, {"ora": "2026-10-15T08:00", "tipo": "colazione",
                            "voci": [{"alimento_id": mela["id"], "grammi": 100}]})

    freq = diary.frequent_foods(store)
    assert [f["nome"] for f in freq] == ["Pane", "Mela"]
    assert freq[0]["usi"] == 2
    assert freq[1]["usi"] == 1


def test_frequenti_vuoto_se_nessun_pasto(store):
    diary.create_food(store, {"nome": "Pane", "carbo_100g": 50})
    assert diary.frequent_foods(store) == []


def test_ricetta_calcola_macro_aggregati_per_100g(store):
    pasta = diary.create_food(store, {"nome": "Pasta", "carbo_100g": 75})
    # 160 g di pasta (120 g carbo) + 200 g di pomodoro (8 g carbo) = 360 g, 128 g carbo
    ricetta = diary.save_recipe(store, {
        "nome": "Pasta al pomodoro", "porzioni_n": 2,
        "voci": [
            {"alimento_id": pasta["id"], "grammi": 160},
            {"descrizione": "Pomodoro", "carbo_g": 8, "grammi": 200},
        ],
    })
    assert ricetta["fonte"] == "ricetta"
    assert ricetta["carbo_100g"] == pytest.approx(128 / 360 * 100, abs=0.01)
    assert ricetta["porzione_g"] == pytest.approx(180.0)  # 360 g / 2 porzioni
    assert ricetta["porzioni_n"] == 2
    assert len(ricetta["ingredienti"]) == 2


def test_ricetta_rifiuta_voci_libere_senza_grammi(store):
    with pytest.raises(diary.DiaryError):
        diary.save_recipe(store, {
            "nome": "Pasticcio", "porzioni_n": 1,
            "voci": [{"descrizione": "Qualcosa", "carbo_g": 10}],
        })


def test_modifica_ricetta_ricalcola_e_sostituisce_ingredienti(store):
    riso = diary.create_food(store, {"nome": "Riso", "carbo_100g": 28})
    ricetta = diary.save_recipe(store, {
        "nome": "Risotto", "porzioni_n": 3,
        "voci": [{"alimento_id": riso["id"], "grammi": 300}],
    })
    modificata = diary.save_recipe(store, {
        "nome": "Risotto bianco", "porzioni_n": 2,
        "voci": [{"alimento_id": riso["id"], "grammi": 300}],
    }, ricetta["id"])
    assert modificata["id"] == ricetta["id"]
    assert modificata["nome"] == "Risotto bianco"
    assert modificata["porzione_g"] == pytest.approx(150.0)  # 300 g / 2 porzioni
    assert len(diary.recipe_items(store, ricetta["id"])) == 1


class TestNutrienti:
    """Calorie, proteine, grassi, zuccheri e fibre accanto ai carboidrati."""

    def _pasta(self, store):
        return diary.create_food(store, {
            "nome": "Pasta", "carbo_100g": 75, "proteine_100g": 12, "grassi_100g": 1.5,
            "zuccheri_100g": 3, "fibra_100g": 3, "kcal_100g": 350})

    def test_voce_con_alimento_calcola_nutrienti_dai_grammi(self, store):
        pasta = self._pasta(store)
        pasto = diary.save_meal(store, {"ora": "2026-10-16T13:00", "tipo": "pranzo",
                                        "voci": [{"alimento_id": pasta["id"], "grammi": 80}]})
        n = pasto["voci"][0]["nutrienti"]
        assert n == {"kcal": 280.0, "proteine": 9.6, "grassi": 1.2, "zuccheri": 2.4, "fibra": 2.4}
        assert pasto["nutrienti"]["kcal"] == 280.0
        assert pasto["nutrienti"]["senza_dato"]["kcal"] == 0

    def test_dato_mancante_non_diventa_zero_e_si_segnala(self, store):
        pasta = self._pasta(store)
        senza = diary.create_food(store, {"nome": "Sconosciuto", "carbo_100g": 10})
        pasto = diary.save_meal(store, {"ora": "2026-10-16T13:00", "tipo": "pranzo", "voci": [
            {"alimento_id": pasta["id"], "grammi": 100}, {"alimento_id": senza["id"], "grammi": 50}]})
        assert pasto["voci"][1]["nutrienti"]["kcal"] is None
        assert pasto["nutrienti"]["kcal"] == 350.0
        assert pasto["nutrienti"]["senza_dato"]["kcal"] == 1
        assert pasto["nutrienti"]["carbo"] == 80.0

    def test_voce_libera_accetta_nutrienti_indicati(self, store):
        pasto = diary.save_meal(store, {"ora": "2026-10-16T13:00", "tipo": "cena", "voci": [{
            "descrizione": "Pizza", "carbo_g": 80, "nutrienti": {"kcal": "700", "grassi": 25, "proteine": "x", "fibra": -3}}]})
        n = pasto["voci"][0]["nutrienti"]
        assert n["kcal"] == 700.0 and n["grassi"] == 25.0
        assert n["proteine"] is None and n["fibra"] is None  # non numerico / negativo: scartati

    def test_ricetta_aggrega_solo_se_tutti_gli_ingredienti_hanno_il_dato(self, store):
        pasta = self._pasta(store)
        senza = diary.create_food(store, {"nome": "Sugo misterioso", "carbo_100g": 5})
        completa = diary.save_recipe(store, {"nome": "Pasta in bianco", "voci": [
            {"alimento_id": pasta["id"], "grammi": 100}]})
        assert completa["kcal_100g"] == 350.0 and completa["proteine_100g"] == 12.0
        parziale = diary.save_recipe(store, {"nome": "Pasta al sugo", "voci": [
            {"alimento_id": pasta["id"], "grammi": 100}, {"alimento_id": senza["id"], "grammi": 100}]})
        assert parziale["kcal_100g"] is None

    def test_riepilogo_dieta_per_giorno_e_media(self, store):
        from datetime import date
        pasta = self._pasta(store)
        for giorno, g in (("2026-10-15", 100), ("2026-10-16", 200)):
            diary.save_meal(store, {"ora": f"{giorno}T13:00", "tipo": "pranzo",
                                    "voci": [{"alimento_id": pasta["id"], "grammi": g}]})
        r = diary.diet_summary(store, date(2026, 10, 14), date(2026, 10, 16))
        assert [d["giorno"] for d in r["giorni"]] == ["2026-10-15", "2026-10-16"]
        assert r["giorni"][1]["kcal"] == 700.0
        assert r["media"]["kcal"] == 525.0 and r["giorni_con_pasti"] == 2

    def test_pasti_vecchi_ricevono_i_nutrienti_dal_backfill(self, store):
        pasta = self._pasta(store)
        pasto = diary.save_meal(store, {"ora": "2026-10-16T13:00", "tipo": "pranzo",
                                        "voci": [{"alimento_id": pasta["id"], "grammi": 100}]})
        store.db.execute("UPDATE meal_item SET kcal = NULL, protein_g = NULL, fat_g = NULL, "
                         "sugars_g = NULL, fiber_g = NULL")
        store.set("nutrienti_backfill_v1", "")
        store._backfill_nutrienti()
        assert diary.get_meal(store, pasto["id"])["voci"][0]["nutrienti"]["kcal"] == 350.0

    def test_ricalcolo_completa_voci_vuote_e_ricalcola_i_giorni_recenti(self, store):
        pasta = self._pasta(store)
        for giorno in ("2026-10-10", "2026-10-15"):
            diary.save_meal(store, {"ora": f"{giorno}T13:00", "tipo": "pranzo",
                                    "voci": [{"alimento_id": pasta["id"], "grammi": 100}]})
        # voci salvate "all'antica": senza valori; sul vecchio di 10/10 uno modificato a mano
        store.db.execute("UPDATE meal_item SET kcal = NULL, protein_g = NULL, fat_g = NULL, "
                         "sugars_g = NULL, fiber_g = NULL")
        store.db.execute("UPDATE meal_item SET kcal = 1 WHERE meal_id = 1")  # non vuota, vecchia: resta com'e'
        rep = diary.ricalcola_nutrienti(store, None, "2026-10-15")
        assert rep["voci_aggiornate"] == 1
        assert diary.get_meal(store, 1)["voci"][0]["nutrienti"]["kcal"] == 1.0
        assert diary.get_meal(store, 2)["voci"][0]["nutrienti"]["kcal"] == 350.0

    def test_ricalcolo_elenca_alimenti_senza_dati(self, store):
        senza = diary.create_food(store, {"nome": "Senza dati", "carbo_100g": 10})
        diary.save_meal(store, {"ora": "2026-10-15T13:00", "tipo": "pranzo",
                                "voci": [{"alimento_id": senza["id"], "grammi": 50}]})
        assert diary.ricalcola_nutrienti(store)["alimenti_senza_dati"] == ["Senza dati"]
