"""Test dei calcoli statistici: percentili (AGP) e riepilogo giornaliero (GMI, CV, TIR)."""

from datetime import date

import pytest

from camaps_diary import analysis, stats


def _insert_glucose(store, readings):
    """readings: lista di (ts_local 'AAAA-MM-GG HH:MM:SS', mg/dL)."""
    store.db.executemany(
        "INSERT INTO glucose(ts_local, ts_utc, mgdl) VALUES (?, ?, ?)",
        [(ts, ts.replace(" ", "T") + "Z", v) for ts, v in readings])
    store.db.commit()


def test_percentile_valori_noti():
    vals = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert analysis._percentile(vals, 0) == 10.0
    assert analysis._percentile(vals, 50) == 30.0
    assert analysis._percentile(vals, 100) == 50.0
    # interpolazione lineare: p25 di 5 valori -> indice 1.0 -> 20.0
    assert analysis._percentile(vals, 25) == 20.0


def test_percentile_valore_singolo():
    assert analysis._percentile([42.0], 10) == 42.0
    assert analysis._percentile([42.0], 90) == 42.0


def test_agp_profile_raggruppa_per_fascia_oraria(store):
    # Due giorni, stessa fascia 12:00-12:30: valori diversi per verificare che si aggreghino insieme
    _insert_glucose(store, [
        ("2026-10-01 12:00:00", 100), ("2026-10-01 12:10:00", 120), ("2026-10-01 12:20:00", 140),
        ("2026-10-02 12:05:00", 160), ("2026-10-02 12:15:00", 180),
    ])
    bins = analysis.agp_profile(store, date(2026, 10, 1), date(2026, 10, 2), bin_minutes=30)
    assert len(bins) == 48  # 24h / 30 min
    fascia_12 = next(b for b in bins if b["min"] == 720)  # 12:00 = 720 min
    assert fascia_12["n"] == 5
    assert fascia_12["p50"] == 140  # mediana di [100,120,140,160,180]

    # Una fascia senza letture resta a n=0, senza errori e senza percentili
    fascia_vuota = next(b for b in bins if b["min"] == 0)
    assert fascia_vuota["n"] == 0
    assert "p50" not in fascia_vuota


def test_daily_summary_formule_gmi_cv_tir(store):
    """Valori scelti a mano: media 150, cosi' GMI e CV si verificano a calcolatrice."""
    giorno = date(2026, 10, 15)
    valori = [100, 120, 140, 160, 180, 200]  # media 150
    letture = [(f"2026-10-15 {h:02d}:00:00", v) for h, v in zip(range(6), valori)]
    _insert_glucose(store, letture)

    righe = stats.daily_summary(store, giorno, giorno)
    assert len(righe) == 1
    r = righe[0]

    media = sum(valori) / len(valori)
    assert r["media_mgdl"] == round(media)
    assert r["gmi_pct"] == round(3.31 + 0.02392 * media, 1)

    import statistics
    sd = statistics.pstdev(valori)
    assert r["dev_std_mgdl"] == round(sd, 1)
    assert r["cv_pct"] == round(100 * sd / media, 1)

    # soglie di default 70-180: 100,120,140,160,180 in range (5/6), 200 sopra (1/6)
    assert r["tir_pct"] == round(100 * 5 / 6, 1)
    assert r["sopra_pct"] == round(100 * 1 / 6, 1)
    assert r["sotto_pct"] == 0.0


def test_daily_summary_giorno_senza_letture_non_esplode(store):
    righe = stats.daily_summary(store, date(2026, 10, 15), date(2026, 10, 15))
    assert len(righe) == 1
    assert righe[0]["letture_cgm"] == 0
    assert righe[0]["media_mgdl"] is None
    assert righe[0]["tir_pct"] is None
