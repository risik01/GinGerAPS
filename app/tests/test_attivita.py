"""Test della sincronizzazione passi da Home Assistant: il bug segnalato
dall'utente era che il contatore non sembrava azzerarsi a mezzanotte, perche'
il valore vecchio di ieri veniva copiato come se fosse quello di oggi."""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from camaps_diary import attivita


def _risposta(stato, last_updated, http_status=200):
    resp = MagicMock()
    resp.status_code = http_status
    resp.ok = http_status < 400
    resp.json.return_value = {"state": stato, "last_updated": last_updated}
    return resp


def test_sync_today_memorizza_i_passi_se_il_sensore_e_stato_aggiornato_oggi(store):
    oggi = attivita.now_local("Europe/Rome").date()
    ora = f"{oggi.isoformat()}T08:15:09.123456+00:00"
    with patch("camaps_diary.attivita.requests.get", return_value=_risposta("2558", ora)):
        passi = attivita.sync_today(store, "token", "sensor.passi", "Europe/Rome")
    assert passi == 2558
    riga = store.db.execute("SELECT passi FROM activity_steps WHERE giorno = ?", (oggi.isoformat(),)).fetchone()
    assert riga["passi"] == 2558


def test_sync_today_ignora_un_valore_non_ancora_aggiornato_oggi(store):
    """Il bug reale: last_updated di ieri sera, letto questa mattina presto.
    Senza il controllo, il totale di ieri finiva copiato su oggi."""
    oggi = attivita.now_local("Europe/Rome").date()
    ieri_sera = f"{(oggi - timedelta(days=1)).isoformat()}T21:59:42.000000+00:00"
    with patch("camaps_diary.attivita.requests.get", return_value=_risposta("2558", ieri_sera)):
        passi = attivita.sync_today(store, "token", "sensor.passi", "Europe/Rome")
    assert passi is None
    riga = store.db.execute("SELECT passi FROM activity_steps WHERE giorno = ?", (oggi.isoformat(),)).fetchone()
    assert riga is None  # non deve aver scritto il valore vecchio su oggi


def test_sync_today_con_sensore_sconosciuto_restituisce_none(store):
    with patch("camaps_diary.attivita.requests.get", return_value=_risposta("unknown", None)):
        assert attivita.sync_today(store, "token", "sensor.passi", "Europe/Rome") is None


def test_sync_today_valore_non_numerico_solleva_errore(store):
    oggi = attivita.now_local("Europe/Rome").date().isoformat()
    with patch("camaps_diary.attivita.requests.get", return_value=_risposta("boh", f"{oggi}T08:00:00+00:00")):
        with pytest.raises(attivita.StepsError):
            attivita.sync_today(store, "token", "sensor.passi", "Europe/Rome")


def test_fetch_state_senza_token_solleva_errore(store):
    with pytest.raises(attivita.StepsError, match="SUPERVISOR_TOKEN"):
        attivita.sync_today(store, "", "sensor.passi", "Europe/Rome")


def test_fetch_state_entita_non_trovata(store):
    resp = MagicMock(status_code=404)
    with patch("camaps_diary.attivita.requests.get", return_value=resp):
        with pytest.raises(attivita.StepsError, match="non trovata"):
            attivita.sync_today(store, "token", "sensor.passi", "Europe/Rome")


def test_sync_today_mantiene_un_valore_aggiornato_anche_senza_last_updated(store):
    """Alcuni sensori potrebbero non avere last_updated: meglio accettare il
    valore piuttosto che bloccare per sempre la sincronizzazione."""
    oggi = attivita.now_local("Europe/Rome").date()
    with patch("camaps_diary.attivita.requests.get", return_value=_risposta("1000", None)):
        passi = attivita.sync_today(store, "token", "sensor.passi", "Europe/Rome")
    assert passi == 1000
