"""Test di Fase 2: riepilogo Calendario mensile e "le altre volte che l'ho mangiato"."""

from datetime import date

from camaps_diary import analysis, diary


def _glucose_rows(ts_local_start, minuti_valori):
    """minuti_valori: lista di (minuti da ts_local_start, mg/dL)."""
    from datetime import timedelta
    from camaps_diary.timeutil import fmt_local, parse_local
    base = parse_local(ts_local_start)
    return [(fmt_local(base + timedelta(minutes=m)), v) for m, v in minuti_valori]


def _insert_glucose(store, rows):
    store.db.executemany(
        "INSERT INTO glucose(ts_local, ts_utc, mgdl) VALUES (?, ?, ?)",
        [(ts, ts.replace(" ", "T") + "Z", v) for ts, v in rows])
    store.db.commit()


def test_calendar_month_restituisce_tutti_i_giorni_del_mese(store):
    righe = analysis.calendar_month(store, 2026, 2)  # febbraio, 28 giorni
    assert len(righe) == 28
    assert righe[0]["giorno"] == "2026-02-01"
    assert righe[-1]["giorno"] == "2026-02-28"


def test_calendar_month_porta_la_foto_del_pasto_principale(store, tmp_path):
    pane = diary.create_food(store, {"nome": "Pane", "carbo_100g": 50})
    # due pasti lo stesso giorno: quello con piu' carboidrati deve "vincere" la foto
    pasto_piccolo = diary.save_meal(store, {"ora": "2026-03-05T08:00", "tipo": "colazione",
                                            "voci": [{"alimento_id": pane["id"], "grammi": 20}]})
    pasto_grande = diary.save_meal(store, {"ora": "2026-03-05T13:00", "tipo": "pranzo",
                                           "voci": [{"alimento_id": pane["id"], "grammi": 200}]})
    from camaps_diary import foto
    from PIL import Image
    import io
    buf = io.BytesIO(); Image.new("RGB", (4, 4)).save(buf, format="JPEG")
    foto_piccolo = foto.save_photo(store, tmp_path, pasto_piccolo["id"], buf.getvalue())
    buf2 = io.BytesIO(); Image.new("RGB", (4, 4)).save(buf2, format="JPEG")
    foto_grande = foto.save_photo(store, tmp_path, pasto_grande["id"], buf2.getvalue())

    righe = analysis.calendar_month(store, 2026, 3)
    giorno = next(r for r in righe if r["giorno"] == "2026-03-05")
    assert giorno["foto_id"] == foto_grande["id"]


def test_food_occurrences_un_alimento_mangiato_5_volte_da_5_curve(store):
    pasta = diary.create_food(store, {"nome": "Pasta", "carbo_100g": 75})
    for giorno in range(1, 6):
        ts = f"2026-04-{giorno:02d}T13:00"
        diary.save_meal(store, {"ora": ts, "tipo": "pranzo",
                                "voci": [{"alimento_id": pasta["id"], "grammi": 100}]})
        _insert_glucose(store, _glucose_rows(ts.replace("T", " ") + ":00", [
            (-10, 120), (0, 130), (30, 160), (60, 190), (90, 170), (120, 140), (150, 120),
        ]))

    risultato = analysis.food_occurrences(store, pasta["id"])
    assert risultato["riepilogo"]["n"] == 5
    assert len(risultato["occorrenze"]) == 5
    # picco a +60 min con valore 190 per ogni occorrenza
    for occ in risultato["occorrenze"]:
        assert occ["picco"] == 190
        assert occ["picco_min"] == 60
    assert risultato["riepilogo"]["picco_medio"] == 190
    assert risultato["riepilogo"]["tempo_al_picco_medio"] == 60


def test_food_occurrences_alimento_mai_mangiato_restituisce_vuoto(store):
    pasta = diary.create_food(store, {"nome": "Pasta", "carbo_100g": 75})
    risultato = analysis.food_occurrences(store, pasta["id"])
    assert risultato["occorrenze"] == []
    assert risultato["riepilogo"]["n"] == 0
    assert risultato["riepilogo"]["picco_medio"] is None


def test_food_occurrences_rispetta_il_limite(store):
    pane = diary.create_food(store, {"nome": "Pane", "carbo_100g": 50})
    for giorno in range(1, 13):  # 12 volte, limite di default 10
        diary.save_meal(store, {"ora": f"2026-05-{giorno:02d}T08:00", "tipo": "colazione",
                                "voci": [{"alimento_id": pane["id"], "grammi": 50}]})
    risultato = analysis.food_occurrences(store, pane["id"])
    assert risultato["riepilogo"]["n"] == 10
