"""WHS Engine tests (Section 4.3) - weighted composite + ratings."""
import pytest

from app.engines.whs_engine import WhsEngine

A = pytest.approx


def test_all_max_is_100():
    out = WhsEngine().compute(risk=100, tax=100, alloc=100, liq=100, thematic=100)
    assert out["score"] == A(100)
    assert out["rating"] == "Strong"


def test_known_weighted_value():
    # 0.25*70 + 0.25*80 + 0.20*60 + 0.15*90 + 0.15*50 = 70.5
    out = WhsEngine().compute(risk=70, tax=80, alloc=60, liq=90, thematic=50)
    assert out["score"] == A(70.5)
    assert out["rating"] == "Healthy"


def test_rating_bands():
    eng = WhsEngine()
    assert eng.compute(risk=30, tax=30, alloc=30, liq=30, thematic=30)["rating"] == "At risk"
    assert eng.compute(risk=50, tax=50, alloc=50, liq=50, thematic=50)["rating"] == "Needs attention"


def test_rejects_out_of_range():
    with pytest.raises(ValueError):
        WhsEngine().compute(risk=120, tax=50, alloc=50, liq=50, thematic=50)
