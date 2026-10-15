"""Test di Fase 6: sorgenti dati intercambiabili (Nightscout, xDrip+).

Nessuna chiamata di rete vera: le risposte HTTP sono finte ma riproducono la
forma reale documentata delle API (vedi docstring dei moduli). Non c'e' modo
di verificarle contro un server reale in questa installazione (si usa solo
Glooko): questi test proteggono solo la logica di analisi delle risposte.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from camaps_diary.datasource import DataSourceError
from camaps_diary.nightscout_source import NightscoutDataSource
from camaps_diary.xdrip_source import XDripDataSource


def _risposta(payload, status=200):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.side_effect = None if status < 400 else Exception("HTTP error")
    return resp


class TestNightscout:
    def test_fetch_glucose_analizza_le_voci_sgv(self):
        voci = [
            {"type": "sgv", "sgv": 120, "date": 1760000000000},
            {"type": "sgv", "sgv": 140, "date": 1760000300000},
            {"type": "mbg", "sgv": None, "date": 1760000600000},  # da ignorare
        ]
        src = NightscoutDataSource("http://ns.local", token="abc123")
        with patch("camaps_diary.nightscout_source.requests.get", return_value=_risposta(voci)) as m:
            righe = src.fetch_glucose(datetime(2026, 10, 1), datetime(2026, 10, 2))
        assert len(righe) == 2
        assert righe[0]["mgdl"] == 120
        assert "ts_local" in righe[0] and "ts_utc" in righe[0]
        # il token va sempre nei parametri della richiesta
        assert m.call_args.kwargs["params"]["token"] == "abc123"

    def test_fetch_boluses_ignora_trattamenti_senza_insulina(self):
        voci = [
            {"_id": "a1", "eventType": "Meal Bolus", "insulin": 4.5, "carbs": 40,
             "created_at": "2026-10-15T10:00:00.000Z"},
            {"_id": "a2", "eventType": "Note", "created_at": "2026-10-15T11:00:00.000Z"},  # niente insulina
        ]
        src = NightscoutDataSource("http://ns.local")
        with patch("camaps_diary.nightscout_source.requests.get", return_value=_risposta(voci)):
            righe = src.fetch_boluses(datetime(2026, 10, 15), datetime(2026, 10, 16))
        assert len(righe) == 1
        assert righe[0]["guid"] == "a1"
        assert righe[0]["insulin_delivered"] == 4.5
        assert righe[0]["carbs_input"] == 40

    def test_fetch_basals_legge_temp_basal(self):
        voci = [{"_id": "b1", "eventType": "Temp Basal", "rate": 0.8, "duration": 30,
                 "created_at": "2026-10-15T10:00:00.000Z"}]
        src = NightscoutDataSource("http://ns.local")
        with patch("camaps_diary.nightscout_source.requests.get", return_value=_risposta(voci)):
            righe = src.fetch_basals(datetime(2026, 10, 15), datetime(2026, 10, 16))
        assert righe[0]["rate"] == 0.8
        assert righe[0]["duration_s"] == 1800

    def test_status_ok(self):
        src = NightscoutDataSource("http://ns.local")
        with patch("camaps_diary.nightscout_source.requests.get", return_value=_risposta({"status": "ok"})):
            assert src.status() == {"ok": True, "errore": None}

    def test_status_errore_di_rete(self):
        import requests
        src = NightscoutDataSource("http://ns.local")
        with patch("camaps_diary.nightscout_source.requests.get", side_effect=requests.ConnectionError("boom")):
            esito = src.status()
        assert esito["ok"] is False
        assert "Nightscout non raggiungibile" in esito["errore"]


class TestXDrip:
    def test_fetch_glucose_filtra_per_intervallo_richiesto(self):
        voci = [
            {"sgv": 100, "date": 1791504000000},  # 2026-10-09 02:00 ora di Roma: dentro il periodo
            {"sgv": 110, "date": 1791590400000},  # un giorno dopo: fuori dal periodo richiesto
        ]
        src = XDripDataSource("http://192.168.1.50:17580")
        with patch("camaps_diary.xdrip_source.requests.get", return_value=_risposta(voci)):
            righe = src.fetch_glucose(datetime(2026, 10, 9), datetime(2026, 10, 9, 5))
        assert len(righe) == 1
        assert righe[0]["mgdl"] == 100

    def test_fetch_boluses_e_basals_sono_sempre_vuoti(self):
        src = XDripDataSource("http://192.168.1.50:17580")
        assert src.fetch_boluses(datetime(2026, 10, 1), datetime(2026, 10, 2)) == []
        assert src.fetch_basals(datetime(2026, 10, 1), datetime(2026, 10, 2)) == []

    def test_status_errore_risposta_non_json(self):
        src = XDripDataSource("http://192.168.1.50:17580")
        resp = MagicMock()
        resp.raise_for_status.side_effect = None
        resp.json.side_effect = ValueError("not json")
        with patch("camaps_diary.xdrip_source.requests.get", return_value=resp):
            esito = src.status()
        assert esito["ok"] is False


def test_sync_generic_scrive_nel_database(store):
    from camaps_diary.sync import sync_generic
    from camaps_diary.datasource import DataSource

    class FintaSorgente(DataSource):
        kind = "finta"

        def fetch_glucose(self, start, end):
            return [{"ts_local": "2026-10-15 10:00:00", "ts_utc": "2026-10-15T08:00:00Z", "mgdl": 130}]

        def fetch_boluses(self, start, end):
            return [{"guid": "x1", "ts_local": "2026-10-15 10:00:00", "ts_utc": "2026-10-15T08:00:00Z",
                     "insulin_delivered": 3.0, "carbs_input": 30, "bolus_type": "bolus", "soft_deleted": 0}]

        def status(self):
            return {"ok": True, "errore": None}

    report = sync_generic(FintaSorgente(), store, backfill_days=1)
    assert report == {"glicemie": 1, "boli": 1, "basali": 0}
    assert store.db.execute("SELECT COUNT(*) FROM glucose").fetchone()[0] == 1
    assert store.db.execute("SELECT COUNT(*) FROM bolus").fetchone()[0] == 1
