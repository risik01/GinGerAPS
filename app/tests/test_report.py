"""Test di Fase 3: metriche di periodo, giorni notevoli, tabella pasti e PDF."""

from datetime import date, timedelta

from camaps_diary import diary, report
from camaps_diary.timeutil import fmt_local, parse_local


def _glucose_rows(ts_local_start, minuti_valori):
    base = parse_local(ts_local_start)
    return [(fmt_local(base + timedelta(minutes=m)), v) for m, v in minuti_valori]


def _insert_glucose(store, rows):
    store.db.executemany(
        "INSERT INTO glucose(ts_local, ts_utc, mgdl) VALUES (?, ?, ?)",
        [(ts, ts.replace(" ", "T") + "Z", v) for ts, v in rows])
    store.db.commit()


def _fill_day_cgm(store, day_iso, mgdl=120, every_min=5):
    """Una giornata di letture regolari, utile per far risultare >70% di copertura."""
    rows = [(f"{day_iso} {h:02d}:{m:02d}:00", mgdl) for h in range(24) for m in range(0, 60, every_min)]
    _insert_glucose(store, rows)


# -- period_metrics (funzione pura, senza DB) --------------------------------

def test_period_metrics_media_pesata_per_numero_di_letture():
    giorni = [
        {"giorno": "2026-01-01", "letture_cgm": 100, "copertura_cgm_pct": 80,
         "media_mgdl": 100, "dev_std_mgdl": 10, "tir_pct": 90, "sotto_pct": 5,
         "molto_sotto_pct": 0, "sopra_pct": 5, "molto_sopra_pct": 0,
         "totale_U": 20, "boli_U": 10, "basale_U": 10,
         "carbo_mangiati_g": 150, "carbo_camaps_g": 140, "passi": 5000},
        {"giorno": "2026-01-02", "letture_cgm": 200, "copertura_cgm_pct": 95,
         "media_mgdl": 160, "dev_std_mgdl": 20, "tir_pct": 60, "sotto_pct": 0,
         "molto_sotto_pct": 0, "sopra_pct": 40, "molto_sopra_pct": 10,
         "totale_U": 30, "boli_U": 15, "basale_U": 15,
         "carbo_mangiati_g": 200, "carbo_camaps_g": 190, "passi": 7000},
    ]
    m = report.period_metrics(giorni)
    assert m["giorni_totali"] == 2
    assert m["giorni_con_dati"] == 2
    # media pesata: (100*100 + 200*160) / 300 = 140
    assert m["media_mgdl"] == 140
    assert m["gmi_pct"] == round(3.31 + 0.02392 * 140, 1)
    # varianza aggregata esatta: vedi _pooled_mean_sd
    sumsq = 100 * (10**2 + 100**2) + 200 * (20**2 + 160**2)
    var = sumsq / 300 - 140**2
    assert m["cv_pct"] == round(100 * var**0.5 / 140, 1)
    # tir pesato: (100*90 + 200*60) / 300 = 70
    assert m["tir_pct"] == 70.0
    assert m["insulina_U"] == 25.0  # media semplice dei due giorni (20+30)/2
    assert m["carbo_mangiati_g"] == 175.0
    assert m["passi"] == 6000.0


def test_period_metrics_esclude_giorni_sotto_70_percento_di_copertura():
    giorni = [
        {"giorno": "2026-01-01", "letture_cgm": 300, "copertura_cgm_pct": 95,
         "media_mgdl": 120, "dev_std_mgdl": 15, "tir_pct": 80, "sotto_pct": 0,
         "molto_sotto_pct": 0, "sopra_pct": 20, "molto_sopra_pct": 0,
         "totale_U": 20, "boli_U": 10, "basale_U": 10,
         "carbo_mangiati_g": None, "carbo_camaps_g": 100, "passi": None},
        {"giorno": "2026-01-02", "letture_cgm": 10, "copertura_cgm_pct": 20,
         "media_mgdl": 300, "dev_std_mgdl": 5, "tir_pct": 0, "sotto_pct": 0,
         "molto_sotto_pct": 0, "sopra_pct": 100, "molto_sopra_pct": 100,
         "totale_U": 999, "boli_U": 999, "basale_U": 0,
         "carbo_mangiati_g": None, "carbo_camaps_g": 9999, "passi": None},
    ]
    m = report.period_metrics(giorni)
    assert m["giorni_con_dati"] == 1
    # il giorno con poca copertura non deve influenzare le medie
    assert m["media_mgdl"] == 120
    assert m["tir_pct"] == 80.0
    assert m["insulina_U"] == 20.0
    assert m["carbo_mangiati_g"] is None  # nessun giorno con carboidrati dichiarati


def test_period_metrics_senza_giorni_qualificanti_restituisce_none():
    m = report.period_metrics([{"giorno": "2026-01-01", "letture_cgm": 0, "copertura_cgm_pct": 0,
                                "media_mgdl": None, "dev_std_mgdl": None, "tir_pct": None,
                                "sotto_pct": None, "molto_sotto_pct": None, "sopra_pct": None,
                                "molto_sopra_pct": None, "totale_U": 0, "boli_U": 0, "basale_U": 0,
                                "carbo_mangiati_g": None, "carbo_camaps_g": None, "passi": None}])
    assert m["media_mgdl"] is None
    assert m["gmi_pct"] is None
    assert m["cv_pct"] is None
    assert m["tir_pct"] is None


# -- giorni notevoli (richiede il database) -----------------------------------

def test_notable_days_rileva_ipo_sotto_54(store):
    _fill_day_cgm(store, "2026-02-01", mgdl=120)
    _insert_glucose(store, [("2026-02-01 03:02:00", 50)])
    giorni = [{"giorno": "2026-02-01", "letture_cgm": 289, "copertura_cgm_pct": 100,
               "molto_sotto_pct": 0.3}]
    notevoli = report.notable_days(store, giorni)
    assert any(n["tipo"] == "ipo" and n["giorno"] == "2026-02-01" for n in notevoli)


def test_notable_days_rileva_iper_sostenuto_oltre_2_ore(store):
    # 150 minuti consecutivi sopra 250 mg/dL
    rows = [(f"2026-02-02 10:{m:02d}:00", 280) for m in range(0, 60, 5)]
    rows += [(f"2026-02-02 11:{m:02d}:00", 280) for m in range(0, 60, 5)]
    rows += [(f"2026-02-02 12:{m:02d}:00", 280) for m in range(0, 30, 5)]
    _insert_glucose(store, rows)
    giorni = [{"giorno": "2026-02-02", "letture_cgm": len(rows), "copertura_cgm_pct": 50,
               "molto_sotto_pct": 0}]
    notevoli = report.notable_days(store, giorni)
    assert any(n["tipo"] == "iper" for n in notevoli)


def test_notable_days_breve_picco_sopra_250_non_e_notevole(store):
    rows = [(f"2026-02-03 10:{m:02d}:00", 280) for m in range(0, 20, 5)]  # solo 15 min
    _insert_glucose(store, rows)
    giorni = [{"giorno": "2026-02-03", "letture_cgm": len(rows), "copertura_cgm_pct": 50,
               "molto_sotto_pct": 0}]
    notevoli = report.notable_days(store, giorni)
    assert not any(n["tipo"] == "iper" for n in notevoli)


def test_notable_days_segnala_giorno_senza_dati(store):
    giorni = [{"giorno": "2026-02-04", "letture_cgm": 0, "copertura_cgm_pct": 0, "molto_sotto_pct": None}]
    notevoli = report.notable_days(store, giorni)
    assert notevoli == [{"giorno": "2026-02-04", "tipo": "senza_dati",
                         "dettaglio": "Nessun dato dal sensore", "note": []}]


# -- tabella pasti e dettaglio giornaliero --------------------------------------

def _pasti_del_periodo(store, day_from, day_to):
    blocchi = report.day_blocks(store, day_from, day_to, [])
    return [p for b in blocchi for p in b["pasti"]]


def test_day_blocks_elenca_i_pasti_con_alimenti_e_carboidrati(store):
    pasta = diary.create_food(store, {"nome": "Pasta", "carbo_100g": 75})
    _fill_day_cgm(store, "2026-03-01", mgdl=130)
    diary.save_meal(store, {"ora": "2026-03-01T13:00", "tipo": "pranzo",
                            "voci": [{"alimento_id": pasta["id"], "grammi": 100}]})
    righe = _pasti_del_periodo(store, date(2026, 3, 1), date(2026, 3, 1))
    assert len(righe) == 1
    r = righe[0]
    assert r["giorno"] == "2026-03-01"
    assert r["ora"] == "13:00"
    assert r["tipo"] == "Pranzo"
    assert "Pasta" in r["alimenti"]
    assert r["carbo_dichiarati_g"] == 75.0
    assert r["in_attesa"] is False


def test_day_blocks_periodo_senza_pasti_e_vuoto(store):
    assert _pasti_del_periodo(store, date(2026, 3, 10), date(2026, 3, 12)) == []


def test_pasto_senza_dati_camaps_ancora_arrivati_non_viene_saltato(store):
    """Bug segnalato dall'utente: la merenda inserita nel pomeriggio non
    compariva nel report perche' i dati CamAPS di quell'ora non erano ancora
    arrivati da Glooko e il pasto veniva scartato del tutto, invece di essere
    mostrato con 'in attesa' per le sole colonne che dipendono da CamAPS."""
    pane = diary.create_food(store, {"nome": "Pane e marmellata", "carbo_100g": 50})
    # nessuna lettura di glicemia nel database: last_glucose_local() e' None,
    # quindi analyse_meal segna sempre in_attesa=True per qualunque pasto
    diary.save_meal(store, {"ora": "2026-03-01T17:00", "tipo": "merenda",
                            "voci": [{"alimento_id": pane["id"], "grammi": 40}]})
    righe = _pasti_del_periodo(store, date(2026, 3, 1), date(2026, 3, 1))
    assert len(righe) == 1
    r = righe[0]
    assert r["tipo"] == "Merenda"
    assert r["in_attesa"] is True
    assert r["carbo_dichiarati_g"] == 20.0  # i grammi dichiarati si vedono comunque
    assert r["carbo_camaps_g"] is None
    assert r["bolo_U"] is None


def test_day_blocks_include_giorni_senza_pasti_nel_dettaglio(store):
    _fill_day_cgm(store, "2026-03-01", mgdl=120)
    blocchi = report.day_blocks(store, date(2026, 3, 1), date(2026, 3, 2), [])
    assert len(blocchi) == 2
    assert blocchi[0]["pasti"] == []
    assert len(blocchi[0]["glicemie"]) > 0


# -- rendering HTML e PDF -------------------------------------------------------

def _dati_minimi():
    return {
        "generato": parse_local("2026-03-15 10:00:00"),
        "day_from": date(2026, 3, 1), "day_to": date(2026, 3, 14), "kind": "14gg",
        "giorni": [{"giorno": "2026-03-01", "tir_pct": 80, "copertura_cgm_pct": 90}],
        "metriche": {"giorni_totali": 14, "giorni_con_dati": 10, "media_mgdl": 140,
                     "gmi_pct": 6.7, "cv_pct": 30.0, "tir_pct": 75.0, "sotto_pct": 3.0,
                     "molto_sotto_pct": 0.5, "sopra_pct": 22.0, "molto_sopra_pct": 4.0,
                     "insulina_U": 28.0, "bolo_U": 14.0, "basale_U": 14.0,
                     "carbo_mangiati_g": 180.0, "carbo_camaps_g": 170.0, "passi": 6000.0},
        "agp": [{"min": m, "n": 1, "p10": 90, "p25": 110, "p50": 130, "p75": 150, "p90": 180}
                for m in range(0, 1440, 30)],
        "notevoli": [{"giorno": "2026-03-05", "tipo": "ipo", "dettaglio": "test", "note": []}],
        "pasti": [{"giorno": "2026-03-01", "ora": "13:00", "tipo": "Pranzo", "alimenti": "Pasta",
                   "carbo_dichiarati_g": 75.0, "carbo_camaps_g": 70.0, "bolo_U": 5.0,
                   "picco_2h": 140, "in_attesa": False, "foto_id": None}],
        "giorni_dettaglio": [{"giorno": "2026-03-01", "riepilogo": {"tir_pct": 80, "media_mgdl": 140, "passi": 6000},
                              "glicemie": [[480, 110], [600, 160]],
                              "voci": [{"min": 600, "tipo": "bolo"}], "soglie": {"bassa": 70, "alta": 180},
                              "pasti": [{"ora": "13:00", "tipo": "Pranzo", "carbo_dichiarati_g": 75.0, "bolo_U": 5.0}]}],
        "passi": {"giorni": [{"giorno": "2026-03-01", "passi": 6000}], "totale_periodo": 6000,
                  "media_giorno": 6000, "per_settimana": [{"da": "2026-02-23", "a": "2026-03-01",
                  "totale": 6000, "giorni_con_dati": 1}],
                  "anno_in_corso": {"anno": 2026, "totale": 6000, "giorni_con_dati": 1}},
        "domande_visita": "",
        "consensus": report.CONSENSUS,
    }


def test_render_html_contiene_il_disclaimer_obbligatorio():
    html = report.render_html(_dati_minimi(), {"bassa": 70, "alta": 180})
    assert report.DISCLAIMER in html
    assert "Pasta" in html
    assert "6.7" in html  # gmi


def test_render_html_mostra_le_domande_per_la_visita_solo_se_presenti():
    dati = _dati_minimi()
    assert "Domande per la visita" not in report.render_html(dati, {"bassa": 70, "alta": 180})
    dati["domande_visita"] = "Chiedere del nuovo sensore"
    html = report.render_html(dati, {"bassa": 70, "alta": 180})
    assert "Domande per la visita" in html
    assert "Chiedere del nuovo sensore" in html


def test_render_pdf_bytes_produce_un_pdf_valido():
    pdf = report.render_pdf_bytes(_dati_minimi(), {"bassa": 70, "alta": 180})
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1000


def test_generate_salva_il_file_e_la_riga_nel_database(store, tmp_path):
    pasta = diary.create_food(store, {"nome": "Pasta", "carbo_100g": 75})
    _fill_day_cgm(store, "2026-04-01", mgdl=130)
    diary.save_meal(store, {"ora": "2026-04-01T13:00", "tipo": "pranzo",
                            "voci": [{"alimento_id": pasta["id"], "grammi": 100}]})
    store.set("domande_visita", "Controllare il set")
    reports_dir = tmp_path / "report"
    risultato = report.generate(store, reports_dir, date(2026, 4, 1), date(2026, 4, 1), "1gg",
                                {"bassa": 70, "alta": 180}, "Controllare il set")
    assert (reports_dir / risultato["filename"]).exists()
    assert (reports_dir / risultato["filename"]).read_bytes()[:4] == b"%PDF"
    riga = report.get_report(store, risultato["id"])
    assert riga["day_from"] == "2026-04-01"
    # le domande per la visita si svuotano dopo l'esportazione
    assert store.get("domande_visita") == ""


def test_list_reports_ordina_dal_piu_recente(store, tmp_path):
    reports_dir = tmp_path / "report"
    a = report.generate(store, reports_dir, date(2026, 4, 1), date(2026, 4, 1), "1gg", {"bassa": 70, "alta": 180})
    b = report.generate(store, reports_dir, date(2026, 4, 2), date(2026, 4, 2), "1gg", {"bassa": 70, "alta": 180})
    lista = report.list_reports(store)
    assert lista[0]["id"] == b["id"]
    assert lista[1]["id"] == a["id"]
