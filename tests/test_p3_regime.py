"""P3.1 — the regime proxy.

The whole point of option (b): ONE price-derived regime function, used
identically by the backtest and by the live read. If those two ever diverge, the
measured numbers on a card stop describing what actually runs — which is the
dishonesty this build exists to remove, just better hidden.
"""
import numpy as np

from app.engines import regime as rg


def _rising(n=800, start=100.0, step=0.05):
    return np.array([start + i * step for i in range(n)], dtype=float)


def _falling(n=800, start=200.0, step=0.05):
    return np.array([start - i * step for i in range(n)], dtype=float)


def _all(arr):
    return {tk: arr.copy() for tk in rg.tickers_needed()}


def test_it_is_pure_and_deterministic():
    """No I/O, no clock: the same array must give the same answer, every time."""
    px = _all(_rising())
    a = rg.series(px)
    b = rg.series({k: v.copy() for k, v in px.items()})
    assert np.array_equal(a["state"].astype(str), b["state"].astype(str))
    assert np.allclose(a["score"], b["score"], equal_nan=True)


def test_a_steady_advance_is_risk_on_and_a_steady_decline_is_risk_off():
    up = rg.series(_all(_rising()))["state"].astype(str)
    down = rg.series(_all(_falling()))["state"].astype(str)
    assert up[-1] == rg.RISK_ON
    assert down[-1] == rg.RISK_OFF


def test_the_warm_up_window_is_neutral_never_risk_on():
    """Before the 200-day exists there is no trend to speak of. An unknown
    regime reading as a green light is the failure mode that matters."""
    st = rg.series(_all(_rising()))["state"].astype(str)
    assert set(st[: rg.MA_DAYS - 1]) == {rg.NEUTRAL}


def test_volatility_is_judged_against_its_own_history_not_a_fixed_number():
    """20% vol means something different for the Nasdaq than for the S&P, and
    something different in 2017 than in 2022."""
    calm = _rising(n=900)
    noisy = calm.copy()
    rng = np.random.default_rng(7)
    noisy[600:] += rng.normal(0, 6.0, size=noisy.size - 600)

    calm_s = rg.series(_all(calm))
    noisy_s = rg.series(_all(noisy))
    assert noisy_s["vol_pct"][-1] > calm_s["vol_pct"][-1]
    assert noisy_s["vol_percentile"][-1] > calm_s["vol_percentile"][-1]


def test_breadth_counts_only_the_indexes_actually_supplied():
    """A missing member shrinks the denominator; it must not count as 'below',
    which would fake a weak market out of an absent feed."""
    up = _rising()
    both = rg.series({rg.PRIMARY: up.copy(), "QQQ": up.copy(), "IWM": up.copy()})
    one = rg.series({rg.PRIMARY: up.copy()})
    assert both["breadth"][-1] == 1.0
    assert one["breadth"][-1] == 1.0          # 1 of 1, not 1 of 3


def test_narrow_breadth_pulls_the_score_down():
    up, down = _rising(), _falling()
    broad = rg.series({rg.PRIMARY: up.copy(), "QQQ": up.copy(), "IWM": up.copy()})
    narrow = rg.series({rg.PRIMARY: up.copy(), "QQQ": down.copy(), "IWM": down.copy()})
    assert narrow["breadth"][-1] < broad["breadth"][-1]
    assert narrow["score"][-1] < broad["score"][-1]


def test_latest_is_the_last_element_of_the_same_computation():
    """Not a parallel implementation that could drift from the gated backtest."""
    px = _all(_rising())
    s = rg.series(px)
    live = rg.latest(px)
    assert live["ok"] is True
    assert live["state"] == str(s["state"][-1])
    assert live["score"] == round(float(s["score"][-1]), 3)
    assert live["components"]["trend_up"] is bool(s["trend_up"][-1])


def test_latest_abstains_rather_than_guessing_when_there_is_no_series():
    out = rg.latest({})
    assert out["ok"] is False and out["reason"] == "NO_SERIES"
    assert "state" not in out          # no default, no guess


def test_the_gate_blocks_only_risk_off_and_aligns_to_the_input():
    px = _all(_falling())
    g = rg.gate(px)
    s = rg.series(px)
    assert g.shape == s["state"].shape
    assert not g[-1]                                  # risk_off blocks
    on = rg.gate(_all(_rising()))
    assert on[-1]                                     # risk_on permits
    # Neutral is permission, not a block: the gate exists to sit out the bad
    # stretches, not to demand a perfect one.
    assert rg.gate(_all(_rising()))[rg.MA_DAYS - 5]


def test_tickers_needed_is_what_the_caller_must_fetch():
    """This module never fetches. If the list is wrong the caller silently
    supplies less than the regime needs, and breadth quietly narrows."""
    need = rg.tickers_needed()
    assert rg.PRIMARY in need
    for tk in rg.BREADTH_SET:
        assert tk in need
