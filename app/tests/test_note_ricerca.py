"""Test di Fase 2 parte 2: note del giorno e ricerca nel diario."""

import pytest

from camaps_diary import diary


def test_crea_e_legge_nota_del_giorno(store):
    nota = diary.create_note(store, {"ora": "2026-10-15T09:00", "testo": "Febbre lieve", "tipo": "malattia"})
    assert nota["tipo"] == "malattia"
    note = diary.notes_for_day(store, "2026-10-15")
    assert len(note) == 1
    assert note[0]["testo"] == "Febbre lieve"


def test_nota_senza_testo_rifiutata(store):
    with pytest.raises(diary.DiaryError):
        diary.create_note(store, {"ora": "2026-10-15T09:00", "testo": "  ", "tipo": "altro"})


def test_nota_tipo_non_valido_rifiutata(store):
    with pytest.raises(diary.DiaryError):
        diary.create_note(store, {"ora": "2026-10-15T09:00", "testo": "Corsa 5km", "tipo": "sport-strano"})


def test_nota_tipo_default_altro(store):
    nota = diary.create_note(store, {"ora": "2026-10-15T09:00", "testo": "Giornata normale"})
    assert nota["tipo"] == "altro"


def test_elimina_nota(store):
    nota = diary.create_note(store, {"ora": "2026-10-15T09:00", "testo": "Da cancellare"})
    diary.delete_note(store, nota["id"])
    assert diary.notes_for_day(store, "2026-10-15") == []


def test_note_di_giorni_diversi_non_si_mescolano(store):
    diary.create_note(store, {"ora": "2026-10-14T09:00", "testo": "Ieri"})
    diary.create_note(store, {"ora": "2026-10-15T09:00", "testo": "Oggi"})
    assert [n["testo"] for n in diary.notes_for_day(store, "2026-10-15")] == ["Oggi"]
    assert [n["testo"] for n in diary.notes_for_day(store, "2026-10-14")] == ["Ieri"]


def test_ricerca_trova_pasti_per_alimento_del_database(store):
    pizza = diary.create_food(store, {"nome": "Pizza margherita", "carbo_100g": 30})
    diary.save_meal(store, {"ora": "2026-09-01T20:00", "tipo": "cena",
                            "voci": [{"alimento_id": pizza["id"], "grammi": 300}]})
    diary.save_meal(store, {"ora": "2026-09-10T20:00", "tipo": "cena",
                            "voci": [{"alimento_id": pizza["id"], "grammi": 250}]})
    risultati = diary.search_diary(store, "pizza")
    assert len(risultati) == 2
    assert risultati[0]["giorno"] == "2026-09-10"  # piu' recente prima


def test_ricerca_trova_pasti_per_voce_libera(store):
    diary.save_meal(store, {"ora": "2026-09-05T13:00", "tipo": "pranzo",
                            "voci": [{"descrizione": "Pizza fatta in casa", "carbo_g": 60}]})
    risultati = diary.search_diary(store, "pizza")
    assert len(risultati) == 1
    assert risultati[0]["giorno"] == "2026-09-05"


def test_ricerca_senza_testo_restituisce_vuoto(store):
    assert diary.search_diary(store, "") == []
    assert diary.search_diary(store, "   ") == []


def test_ricerca_senza_corrispondenze(store):
    diary.create_food(store, {"nome": "Pane", "carbo_100g": 50})
    assert diary.search_diary(store, "sushi") == []
