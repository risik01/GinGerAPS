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
