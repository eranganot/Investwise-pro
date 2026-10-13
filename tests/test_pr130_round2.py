"""PR 130 -- round 2 (approved 2026-10-13), and the near-duplicate test within a tier (D94).

Round 1 taught one narrow thing: the only rule that beat holding QQQ timed the
Nasdaq itself, and its weakness is that "off" still meant owning the Nasdaq --
-85.5% over 2000-2016 for Trend Nasdaq. Round 2 tried where the money goes when
the trend breaks (cash) and how much leverage to hold (scaled by volatility).
Four passed; inside each measured tier only the stronger of two near-duplicates
ships (owner, 2026-10-13): R7 (Medium-high) and R6 (High), which supersedes MH3.

Not financial advice.
"""
import numpy as np
import pytest

from app.engines import strategy_backtest as bt
from app.services import strategy_candidates as sc
from app.services import strategy_catalog as cat

LEGS = {"cash": "BIL", "x1": "QQQ", "x2": "QLD", "x3": "TQQQ"}


@pytest.mark.parametrize("e,want", [
    (0.0, {"BIL": 1.0}), (0.4, {"QQQ": 0.4, "BIL": 0.6}), (1.0, {"QQQ": 1.0}),
    (1.5, {"QLD": 0.5, "QQQ": 0.5}), (2.0, {"QLD": 1.0}), (2.5, {"TQQQ": 0.5, "QLD": 0.5})])
def test_exposure_is_built_from_real_funds(e, want):
    got = bt.exposure_mix(e, LEGS)
    assert got == pytest.approx(want)
    lev = {"BIL": 0, "QQQ": 1, "QLD": 2, "TQQQ": 3}
    assert sum(w * lev[t] for t, w in got.items()) == pytest.approx(e)


def _px(n=400, calm=0.005, wild=0.03, seed=4):
    rng = np.random.default_rng(seed)
    vol = np.where(np.arange(n) < n // 2, calm, wild)
    r = rng.normal(0.0004, 1, n) * vol
    q = 100 * np.cumprod(1 + r)
    return {"QQQ": q, "QLD": 100 * np.cumprod(1 + 2 * r), "TQQQ": 100 * np.cumprod(1 + 3 * r),
            "BIL": np.linspace(100, 104, n)}


def test_volatility_management_cuts_exposure_when_markets_get_choppy():
    spec = {"weights": {}, "overlay": {"kind": "vol_managed", "legs": LEGS, "signal_ticker": "QQQ",
                                       "vol_days": 20, "target_vol_pct": 18, "max_exposure": 2.0}}
    tg = bt.targets_for(_px(), spec)
    lev = {"BIL": 0, "QQQ": 1, "QLD": 2, "TQQQ": 3}
    exp = [sum(w * lev[t] for t, w in t_.items()) for t_ in tg]
    assert exp[150] == pytest.approx(2.0)        # calm: at its 2x ceiling
    assert exp[-1] < 0.6                          # choppy: mostly cash


def test_the_trend_gate_overrides_the_brake():
    px = _px()
    px["QQQ"] = np.concatenate([np.linspace(100, 200, 250), np.linspace(200, 120, 150)])
    spec = {"weights": {"TQQQ": 1.0}, "base": {"QQQ": 1.0},
            "overlay": {"kind": "vol_target", "gate_ticker": "TQQQ", "vol_days": 20,
                        "target_vol_pct": 45, "max_weight": 1.0,
                        "trend_gate": {"ticker": "QQQ", "ma_days": 200, "confirm_days": 3,
                                       "off": {"BIL": 1.0}}}}
    tg = bt.targets_for(px, spec)
    assert tg[-1] == {"BIL": 1.0}
    assert "TQQQ" in tg[230]
    assert "BIL" in bt.tickers_needed(spec) and "QQQ" in bt.tickers_needed(spec)


def test_a_volatility_managed_rule_is_judged_against_its_ceiling():
    own = bt.own_hold_spec({"id": "x", "weights": {}, "overlay": {
        "kind": "vol_managed", "legs": LEGS, "max_exposure": 1.5, "target_vol_pct": 25}})
    assert own["weights"] == pytest.approx({"QLD": 0.5, "QQQ": 0.5})


def test_the_two_survivors_are_offered_and_mh3_gave_way():
    for sid in ("btm_tqqq_trend_cash", "btm_tqqq_trend_vol_brake"):
        e = cat.get(sid)
        assert e and not e.get("retired") and e.get("long_history"), sid
        assert cat.backtestable([sid])[0]["drift_band"] == 0.10
    gone = cat.retired("btm_qld_trend")
    assert gone and gone["decision"] == "D94" and gone["move_to"] == "btm_qqq_hold"


def test_the_off_state_is_cash_not_the_index():
    """The whole point of round 2."""
    assert cat.get("btm_tqqq_trend_cash")["base"] == {"BIL": 1.0}
    assert cat.get("btm_tqqq_trend_vol_brake")["overlay"]["trend_gate"]["off"] == {"BIL": 1.0}


def test_the_rest_of_round_two_is_kept_as_candidates():
    codes = {c["code"]: c for c in sc.CANDIDATES}
    for code in ("R1", "R2", "R3", "R4", "R5"):
        assert code in codes and sc.spec(codes[code]["id"])["long_history"] is True
    assert "near-duplicate" in codes["R4"]["result"] and "near-duplicate" in codes["R5"]["result"]
