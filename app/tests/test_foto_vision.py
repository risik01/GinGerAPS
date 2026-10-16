"""Test della pipeline di analisi foto: schema Pydantic, chiamata (con client finto),
retry, rifiuto, e salvataggio dello stato/contatore costi. Nessuna chiamata di rete vera."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import pytest

from camaps_diary import foto


def _fake_resp(parsed, stop_reason="end_turn", in_tok=1000, out_tok=200):
    block = SimpleNamespace(type="text", parsed_output=parsed)
    return SimpleNamespace(content=[block], stop_reason=stop_reason,
                           usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok))


def _fake_client(resp=None, side_effect=None):
    client = MagicMock()
    if side_effect is not None:
        client.messages.parse.side_effect = side_effect
    else:
        client.messages.parse.return_value = resp
    return client


def test_photo_analysis_schema_valida_un_pasto_tipico():
    analisi = foto.PhotoAnalysis(
        items=[foto.FoodItem(name_it="pasta al pomodoro", category="primo", portion_label="piatto medio",
                              grams_estimate=180, grams_min=150, grams_max=220, carbs_g=48.0, kcal=420, protein_g=14, fat_g=12, sugars_g=6, fiber_g=5,
                              confidence=0.8, reason_short="piatto pieno a livello del bordo")],
        reference_object="forchetta", plate_size_guess="medio", warnings=[],
    )
    assert analisi.items[0].name_it == "pasta al pomodoro"
    assert 0 <= analisi.items[0].confidence <= 1


def test_foto_vuota_o_senza_cibo_restituisce_items_vuoti_con_warning():
    parsed = foto.PhotoAnalysis(items=[], reference_object="nessuno", plate_size_guess="sconosciuto",
                                warnings=["La foto non mostra un piatto riconoscibile"])
    client = _fake_client(resp=_fake_resp(parsed))
    stima, usage = foto._call_claude("sk-test", "claude-sonnet-5", b"jpeg-fittizio", None, client=client)
    assert stima.items == []
    assert stima.warnings
    assert usage.input_tokens == 1000


def test_rifiuto_del_modello_genera_visionerror():
    client = _fake_client(resp=_fake_resp(None, stop_reason="refusal"))
    with pytest.raises(foto.VisionError, match="rifiutato"):
        foto._call_claude("sk-test", "claude-sonnet-5", b"jpeg", None, client=client)


def test_un_retry_su_errore_di_connessione_poi_successo():
    parsed = foto.PhotoAnalysis(items=[], reference_object="nessuno", plate_size_guess="sconosciuto", warnings=[])
    conn_err = anthropic.APIConnectionError(request=MagicMock())
    client = _fake_client(side_effect=[conn_err, _fake_resp(parsed)])
    stima, _ = foto._call_claude("sk-test", "claude-sonnet-5", b"jpeg", None, client=client)
    assert stima.items == []
    assert client.messages.parse.call_count == 2


def test_due_errori_di_connessione_di_seguito_falliscono_senza_secondo_retry():
    conn_err = anthropic.APIConnectionError(request=MagicMock())
    client = _fake_client(side_effect=[conn_err, conn_err])
    with pytest.raises(foto.VisionError):
        foto._call_claude("sk-test", "claude-sonnet-5", b"jpeg", None, client=client)
    assert client.messages.parse.call_count == 2


def test_chiave_assente_non_chiama_la_rete(store, tmp_path):
    photo = foto.save_photo(store, tmp_path, _meal_for_photo(store), _jpeg_fittizio())
    with pytest.raises(foto.VisionError, match="chiave"):
        foto.analyze(store, tmp_path, photo["id"], "", "claude-sonnet-5")


def _jpeg_fittizio() -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(200, 120, 50)).save(buf, format="JPEG")
    return buf.getvalue()


def test_contatore_costo_si_accumula_e_si_azzera_al_mese_nuovo(store):
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000)
    foto._registra_costo(store, "claude-sonnet-5", usage)
    dati = foto.costo_mese(store)
    assert dati["n"] == 1
    assert dati["costo_usd"] == pytest.approx(3.0 + 1.5, abs=0.01)  # 1M*$3 + 0.1M*$15

    foto._registra_costo(store, "claude-sonnet-5", usage)
    assert foto.costo_mese(store)["n"] == 2


def _meal_for_photo(store):
    from camaps_diary import diary
    pasto = diary.save_meal(store, {"ora": "2026-10-15T08:00", "tipo": "colazione", "voci": []})
    return pasto["id"]


class _FakeBlock:
    type = "text"

    def __init__(self, parsed):
        self.parsed_output = parsed


class _FakeClient:
    def __init__(self, parsed):
        self._parsed = parsed
        self.messages = self

    def parse(self, **kwargs):
        class R:
            content = [_FakeBlock(self._parsed)]
        return R()


def test_stima_nutrienti_corregge_zuccheri_e_calorie_incoerenti():
    stima = foto._StimaNutrienti(voci=[
        foto._VoceStima(indice=0, kcal=900, protein_g=10, fat_g=5, sugars_g=50, fiber_g=2)])
    out = foto.stima_nutrienti_voci("k", "m", [{"descrizione": "pane", "grammi": 90, "carbo_g": 40}],
                                    client=_FakeClient(stima))
    assert out[0]["zuccheri"] == 40.0          # mai piu' dei carboidrati
    assert out[0]["kcal"] == 4 * 40 + 4 * 10 + 9 * 5   # 900 incoerente coi macro: vale il calcolo


def test_completa_nutrienti_ia_riempie_solo_voci_libere_vuote_e_le_marca(store):
    from camaps_diary import diary
    pasto = diary.save_meal(store, {"ora": "2026-10-16T13:00", "tipo": "pranzo", "voci": [
        {"descrizione": "pane", "grammi": 90, "carbo_g": 40}]})
    chiamate = []

    def stimatore(voci):
        chiamate.append(voci)
        return [{"kcal": 250, "proteine": 8, "grassi": 2, "zuccheri": 3, "fibra": 4} for _ in voci]

    assert diary.completa_nutrienti_ia(store, stimatore, "2026-10-15") == 1
    v = diary.get_meal(store, pasto["id"])["voci"][0]
    assert v["nutrienti"]["kcal"] == 250 and v["nutrienti_ia"] is True and v["carbo_g"] == 40.0
    assert diary.completa_nutrienti_ia(store, stimatore, "2026-10-15") == 0  # nulla da rifare
    assert len(chiamate) == 1
