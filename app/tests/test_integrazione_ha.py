"""Test di Fase 4: riepilogo per la integrazione Home Assistant."""

from datetime import date, timedelta

from camaps_diary import analysis, diary
from camaps_diary.timeutil import fmt_local, now_local, parse_local


def _insert_glucose(store, rows):
    store.db.executemany(
        "INSERT INTO glucose(ts_local, ts_utc, mgdl) VALUES (?, ?, ?)",
        [(ts, ts.replace(" ", "T") + "Z", v) for ts, v in rows])
    store.db.commit()


def test_ha_snapshot_senza_dati_non_esplode(store):
    oggi = now_local().date()
    s = analysis.ha_snapshot(store, oggi)
    assert s["glicemia"]["mgdl"] is None
    assert s["glicemia"]["trend"] is None
    assert s["ultimo_pasto"] is None
    assert s["collegamento"]["ok"] is False
    assert s["collegamento"]["stato"] == "mai"


def test_glucose_trend_in_salita_rapida(store):
    oggi = now_local().date().isoformat()
    _insert_glucose(store, [
        (f"{oggi} 10:00:00", 100),
        (f"{oggi} 10:15:00", 150),  # +50 in 15 min = 3.3 mg/dL al minuto
    ])
    assert analysis._glucose_trend(store) == "in salita rapida"


def test_glucose_trend_in_salita_moderata(store):
    oggi = now_local().date().isoformat()
    _insert_glucose(store, [
        (f"{oggi} 10:00:00", 100),
        (f"{oggi} 10:15:00", 120),  # +20 in 15 min = 1.3 mg/dL al minuto
    ])
    assert analysis._glucose_trend(store) == "in salita"


def test_glucose_trend_stabile(store):
    oggi = now_local().date().isoformat()
    _insert_glucose(store, [
        (f"{oggi} 10:00:00", 120),
        (f"{oggi} 10:15:00", 122),
    ])
    assert analysis._glucose_trend(store) == "stabile"


def test_glucose_trend_in_discesa_rapida(store):
    oggi = now_local().date().isoformat()
    _insert_glucose(store, [
        (f"{oggi} 10:00:00", 160),
        (f"{oggi} 10:15:00", 110),
    ])
    assert analysis._glucose_trend(store) == "in discesa rapida"


def test_glucose_trend_con_una_sola_lettura_e_none(store):
    oggi = now_local().date().isoformat()
    _insert_glucose(store, [(f"{oggi} 10:00:00", 120)])
    assert analysis._glucose_trend(store) is None


def test_ha_snapshot_riporta_ultimo_pasto_e_glicemia(store):
    oggi = now_local().date()
    pasta = diary.create_food(store, {"nome": "Pasta", "carbo_100g": 75})
    diary.save_meal(store, {"ora": f"{oggi.isoformat()}T13:00", "tipo": "pranzo",
                            "voci": [{"alimento_id": pasta["id"], "grammi": 100}]})
    _insert_glucose(store, [(fmt_local(now_local() - timedelta(minutes=10)), 140)])
    s = analysis.ha_snapshot(store, oggi)
    assert s["glicemia"]["mgdl"] == 140
    assert s["glicemia"]["minuti_da_ultimo_dato"] in (9, 10, 11)  # tolleranza di arrotondamento
    assert s["ultimo_pasto"]["tipo"] == "pranzo"
    assert s["ultimo_pasto"]["carboidrati_g"] == 75.0


def test_ha_snapshot_riporta_ultimo_bolo(store):
    store.db.execute(
        "INSERT INTO bolus(guid, ts_local, ts_utc, insulin_delivered, carbs_input, bolus_type, soft_deleted) "
        "VALUES ('g1', '2026-01-01 08:00:00', '2026-01-01T08:00:00Z', 4.5, 40, 'suggested', 0)")
    store.db.commit()
    s = analysis.ha_snapshot(store, now_local().date())
    assert s["ultimo_bolo"] == {"ts_local": "2026-01-01 08:00:00", "unita_U": 4.5}


def test_ha_snapshot_senza_boli_ultimo_bolo_e_none(store):
    s = analysis.ha_snapshot(store, now_local().date())
    assert s["ultimo_bolo"] is None


def test_ha_snapshot_collegamento_ok_quando_sync_stato_ok(store):
    store.set("sync_stato", "ok")
    s = analysis.ha_snapshot(store, now_local().date())
    assert s["collegamento"]["ok"] is True


def test_ha_snapshot_tir_7g_esclude_giorni_poco_coperti(store):
    oggi = now_local().date()
    # un solo giorno con tanta copertura, TIR 100%
    giorno_buono = oggi - timedelta(days=1)
    rows = [(f"{giorno_buono.isoformat()} {h:02d}:{m:02d}:00", 120)
            for h in range(24) for m in range(0, 60, 5)]
    _insert_glucose(store, rows)
    s = analysis.ha_snapshot(store, oggi)
    assert s["tir_7g_pct"] == 100.0


def test_ha_snapshot_nutrienti_oggi(store):
    from datetime import date
    from camaps_diary import diary
    oggi = date(2026, 10, 16)
    assert analysis.ha_snapshot(store, oggi)["nutrienti_oggi"]["kcal"] == 0.0  # nessun pasto
    pasta = diary.create_food(store, {"nome": "Pasta", "carbo_100g": 75, "kcal_100g": 350,
                                      "proteine_100g": 12, "grassi_100g": 1.5})
    diary.save_meal(store, {"ora": "2026-10-16T13:00", "tipo": "pranzo",
                            "voci": [{"alimento_id": pasta["id"], "grammi": 100}]})
    n = analysis.ha_snapshot(store, oggi)["nutrienti_oggi"]
    assert n["kcal"] == 350.0 and n["proteine"] == 12.0 and n["parziale"] is False
    assert n["zuccheri"] is None  # sconosciuto, non zero
