"""Test di Fase 5: insight settimanali deterministici (niente IA, niente dosi)."""

from datetime import date, timedelta

from camaps_diary import diary, insights


def _insert_glucose(store, rows):
    store.db.executemany(
        "INSERT INTO glucose(ts_local, ts_utc, mgdl) VALUES (?, ?, ?)",
        [(ts, ts.replace(" ", "T") + "Z", v) for ts, v in rows])
    store.db.commit()


def _fill_day(store, day_iso, mgdl=120, picchi=None):
    # un minuto per lettura, come i dati reali di Glooko: serve per superare la
    # soglia di "finestra coperta" che analyse_meal usa per calcolare il picco.
    # picchi: {"HH:MM": valore} per alzare singoli minuti sopra il valore base.
    picchi = picchi or {}
    rows = []
    for h in range(24):
        for m in range(60):
            etichetta = f"{h:02d}:{m:02d}"
            rows.append((f"{day_iso} {etichetta}:00", picchi.get(etichetta, mgdl)))
    _insert_glucose(store, rows)


def test_ultima_domenica():
    assert insights.ultima_domenica(date(2026, 10, 15)) == date(2026, 10, 11)  # giovedi' -> domenica prima
    assert insights.ultima_domenica(date(2026, 10, 11)) == date(2026, 10, 11)  # domenica stessa
    assert insights.ultima_domenica(date(2026, 10, 12)) == date(2026, 10, 11)  # lunedi' -> domenica appena finita


def test_fascia_di():
    assert insights._fascia_di(8) == "mattina"
    assert insights._fascia_di(13) == "dopo pranzo"
    assert insights._fascia_di(17) == "pomeriggio"
    assert insights._fascia_di(20) == "dopo cena"
    assert insights._fascia_di(2) == "notte"
    assert insights._fascia_di(23) == "notte"


def test_weekly_insights_senza_dati_segnala_i_giorni_mancanti(store):
    r = insights.weekly_insights(store, date(2026, 10, 11))
    assert r["frasi"] == ["7 giorni senza dati dal sensore questa settimana."]


def test_weekly_insights_confronta_tir_con_la_settimana_precedente(store):
    # settimana corrente: 7 giorni buoni (TIR alto); precedente: TIR basso
    for i in range(7):
        _fill_day(store, (date(2026, 10, 5) + timedelta(days=i)).isoformat(), mgdl=120)  # dentro range
    for i in range(7):
        _fill_day(store, (date(2026, 9, 28) + timedelta(days=i)).isoformat(), mgdl=220)  # sopra range
    r = insights.weekly_insights(store, date(2026, 10, 11))
    assert r["metriche"]["tir_pct"] == 100.0
    assert r["metriche_precedenti"]["tir_pct"] == 0.0
    assert any("in aumento" in f and "100" in f for f in r["frasi"])


def test_weekly_insights_rileva_fascia_con_piu_iperglicemia(store):
    for i in range(7):
        giorno = (date(2026, 10, 5) + timedelta(days=i)).isoformat()
        _fill_day(store, giorno, mgdl=120, picchi={"13:03": 260})  # iper ripetuta dopo pranzo
    r = insights.weekly_insights(store, date(2026, 10, 11))
    assert any('dopo pranzo' in f and 'Iperglicemia' in f for f in r["frasi"])


def test_weekly_insights_rileva_pasto_con_salita_maggiore(store):
    pasta = diary.create_food(store, {"nome": "Pasta", "carbo_100g": 75})
    giorno = date(2026, 10, 7).isoformat()
    _fill_day(store, giorno, mgdl=100, picchi={"14:02": 260})  # picco a +60 minuti dal pasto
    diary.save_meal(store, {"ora": f"{giorno}T13:00", "tipo": "pranzo",
                            "voci": [{"alimento_id": pasta["id"], "grammi": 100}]})
    r = insights.weekly_insights(store, date(2026, 10, 11))
    assert any("Salita maggiore" in f and "13:00" in f for f in r["frasi"])


def test_weekly_insights_segnala_boli_senza_pasto(store):
    giorno = date(2026, 10, 7)
    _fill_day(store, giorno.isoformat(), mgdl=120)
    store.db.execute(
        "INSERT INTO bolus(guid, ts_local, ts_utc, insulin_delivered, carbs_input, bolus_type, soft_deleted) "
        "VALUES ('g1', ?, ?, 3.0, 30, 'suggested', 0)",
        (f"{giorno.isoformat()} 09:00:00", f"{giorno.isoformat()}T09:00:00Z"))
    store.db.commit()
    r = insights.weekly_insights(store, date(2026, 10, 11))
    assert any("senza un pasto collegato" in f for f in r["frasi"])


def test_weekly_insights_segnala_giorni_senza_dati(store):
    for i in range(5):
        _fill_day(store, (date(2026, 10, 5) + timedelta(days=i)).isoformat(), mgdl=120)
    r = insights.weekly_insights(store, date(2026, 10, 11))
    assert any("2 giorni senza dati" in f for f in r["frasi"])
