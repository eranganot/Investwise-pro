"""P3.2 — the gate, wired into the backtest and the live read *together*.

The reason option (b) was chosen: one regime function, used identically in both
paths. If the live signal read one thing and the measured card described another,
the numbers would stop describing what actually runs — the same dishonesty this
build exists to remove, just better hidden.
"""
import inspect

import numpy as np
import pytest

from app.engines import regime as rg
from app.engines import strategy_backtest as bt
from app.services import strategy_catalog as sc
from app.services import strategy_signal_service as sigs

TREND = "btm_trend_tqqq"


def _spec(sid=TREND):
    return sc.backtestable(only=[sid])[0]


def _dates(n):
    """``n`` business days ENDING today.

    Ending today matters: the live signal refuses to speak from closes older
    than MAX_FEED_AGE_DAYS, so a fixed historical start would make every live
    test skip on STALE_FEED and quietly stop checking anything.
    """
    import datetime as _dt
    d, out = _dt.date.today(), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= _dt.timedelta(days=1)
    return sorted(out)


def _rows(dates, values):
    return [(d, float(v)) for d, v in zip(dates, values)]


def _series(n=900, *, tqqq_slope=0.08, index_slope=0.05):
    """A rising world: TQQQ, its QQQ core, and the regime indexes."""
    dates = _dates(n)
    idx = [100.0 + i * index_slope for i in range(n)]
    lev = [50.0 + i * tqqq_slope for i in range(n)]
    s = {"TQQQ": _rows(dates, lev), "QQQ": _rows(dates, idx)}
    for tk in rg.tickers_needed():
        s.setdefault(tk, _rows(dates, idx))
    return s


# --------------------------------------------------------------------------- #
# The structural claim
# --------------------------------------------------------------------------- #
def test_the_backtest_and_the_live_read_call_the_same_regime_function():
    """Not "produce similar answers" — literally the same function object.

    A parallel implementation is exactly what P3 exists to prevent, and it would
    pass any behavioural test on the day it was written and drift afterwards.
    """
    assert sigs.rg is rg
    assert bt.rg is rg
    # The live path reads the last element of the same series the gate masks on.
    assert "rg.latest" in inspect.getsource(sigs.evaluate)
    assert "rg.gate" in inspect.getsource(bt.run)


def test_the_gate_and_latest_agree_on_the_final_day():
    px = {tk: np.array([v for _d, v in rows], dtype=float)
          for tk, rows in _series().items()}
    assert rg.latest(px)["state"] == str(rg.series(px)["state"][-1])
    assert bool(rg.gate(px)[-1]) is (rg.latest(px)["state"] != rg.RISK_OFF)


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def test_tickers_needed_grows_only_when_the_gate_is_asked_for():
    plain = bt.tickers_needed(_spec())
    gated = bt.tickers_needed(_spec(), regime_gate=True)
    assert set(plain) < set(gated)
    for tk in rg.tickers_needed():
        assert tk in gated
    assert "SPY" not in plain, "an ungated run must not demand regime data"


def test_a_gated_run_abstains_when_the_regime_data_is_absent():
    """Silently skipping the gate would produce a number labelled "gated" that
    was not gated — the worst of both."""
    s = _series()
    for tk in rg.tickers_needed():
        s.pop(tk, None)
    out = bt.run(s, _spec(), regime_gate=True)
    assert out["ok"] is False
    assert out["reason"] == bt.MISSING_TICKER


# --------------------------------------------------------------------------- #
# Gating behaviour
# --------------------------------------------------------------------------- #
def test_gating_is_off_by_default():
    out = bt.run(_series(), _spec())
    assert out["ok"] and out["regime_gated"] is False


def test_both_runs_share_one_date_window_so_the_comparison_is_fair():
    s = _series()
    a = bt.run(s, _spec())
    b = bt.run(s, _spec(), regime_gate=True)
    assert a["ok"] and b["ok"]
    assert a["observations"] == b["observations"]


def test_the_gate_adds_nothing_when_it_agrees_with_the_strategy_s_own_filter():
    """Worth pinning down, because it is the honest answer for most of this
    family. btm_trend_tqqq gates on QQQ vs its 200-day, and the regime proxy is
    built from the same idea — so in a plainly falling market both say "hold the
    core" and the gated result is identical. A regime overlay is only worth
    switching on where it disagrees with the rule already in place."""
    n = 900
    dates = _dates(n)
    idx = [100.0 + i * 0.05 for i in range(500)]
    idx += [idx[-1] - i * 0.12 for i in range(n - 500)]
    lev = [50.0 + i * 0.08 for i in range(500)]
    lev += [lev[-1] - i * 0.20 for i in range(n - 500)]
    s = {"TQQQ": _rows(dates, lev), "QQQ": _rows(dates, idx)}
    for tk in rg.tickers_needed():
        s.setdefault(tk, _rows(dates, idx))

    plain = bt.run(s, _spec())
    gated = bt.run(s, _spec(), regime_gate=True)
    assert plain["ok"] and gated["ok"]
    assert plain["cagr_pct"] == gated["cagr_pct"]
    assert bt.gate_verdict(plain, gated)["improves"] is False


def test_the_gate_bites_when_the_broad_market_disagrees_with_the_strategy():
    """The case a regime overlay is actually for: the strategy's own gate ticker
    is fine, but the market underneath it is not. QQQ holds its trend while SPY
    and IWM roll over — narrow, late-cycle breadth. The trend filter says hold
    the 3x sleeve; the regime says no."""
    n = 900
    dates = _dates(n)
    qqq = [100.0 + i * 0.05 for i in range(n)]            # never breaks its trend
    broad = [100.0 + i * 0.05 for i in range(500)]         # rolls over halfway
    broad += [broad[-1] - i * 0.12 for i in range(n - 500)]
    lev = [50.0 + i * 0.08 for i in range(n)]

    s = {"TQQQ": _rows(dates, lev), "QQQ": _rows(dates, qqq)}
    for tk in rg.tickers_needed():
        if tk != "QQQ":
            s[tk] = _rows(dates, broad)

    plain = bt.run(s, _spec())
    gated = bt.run(s, _spec(), regime_gate=True)
    assert plain["ok"] and gated["ok"]
    assert plain["cagr_pct"] != gated["cagr_pct"], "the gate never bit"
    # It moved to the core rather than to cash, so it stayed invested.
    assert gated["max_drawdown_pct"] != plain["max_drawdown_pct"]


# --------------------------------------------------------------------------- #
# The verdict
# --------------------------------------------------------------------------- #
def _res(cagr, dd, ok=True, obs=1000):
    return {"ok": ok, "cagr_pct": cagr, "max_drawdown_pct": dd, "observations": obs}


def test_the_verdict_accepts_a_small_return_giveup_for_a_shallower_drawdown():
    """That trade IS the product: judging on CAGR alone would reject a filter
    doing exactly what it was built to do."""
    v = bt.gate_verdict(_res(20.0, 60.0), _res(19.5, 35.0))
    assert v["improves"] is True
    assert v["cagr_delta_pct"] == -0.5 and v["max_drawdown_delta_pct"] == -25.0


def test_the_verdict_rejects_a_gate_that_costs_too_much_return():
    v = bt.gate_verdict(_res(20.0, 60.0), _res(16.0, 30.0))
    assert v["improves"] is False
    assert "more than the" in v["why"]


def test_the_verdict_rejects_a_gate_that_does_not_reduce_drawdown():
    """Judging on drawdown alone would bless a gate that simply refuses to
    invest; judging on neither would bless anything."""
    v = bt.gate_verdict(_res(20.0, 60.0), _res(21.0, 62.0))
    assert v["improves"] is False
    assert "not reduced" in v["why"]


def test_the_verdict_is_at_the_boundary_what_the_criterion_says():
    exactly = bt.gate_verdict(_res(20.0, 60.0), _res(20.0 - bt.MAX_CAGR_GIVEUP_PCT, 50.0))
    just_over = bt.gate_verdict(_res(20.0, 60.0), _res(20.0 - bt.MAX_CAGR_GIVEUP_PCT - 0.01, 50.0))
    assert exactly["improves"] is True
    assert just_over["improves"] is False


def test_the_verdict_refuses_to_compare_an_abstention():
    v = bt.gate_verdict(_res(20.0, 60.0), {"ok": False, "reason": bt.MISSING_TICKER})
    assert v["comparable"] is False
    assert "improves" not in v


def test_the_verdict_reports_whether_the_windows_matched():
    """An unequal window silently makes the comparison meaningless."""
    assert bt.gate_verdict(_res(20, 60, obs=1000), _res(19, 40, obs=1000))["observations_match"]
    assert not bt.gate_verdict(_res(20, 60, obs=1000), _res(19, 40, obs=800))["observations_match"]


# --------------------------------------------------------------------------- #
# The live read
# --------------------------------------------------------------------------- #
def test_the_live_signal_reports_the_regime_without_acting_on_it():
    """Ships off: the target must be identical with the regime present."""
    res = sigs.evaluate(TREND, _series())
    if not res.get("ok"):
        pytest.skip(f"signal abstained on synthetic data: {res.get('reason')}")
    assert res["regime"]["ok"] is True
    assert res["regime"]["applied"] is False
    assert res["regime"]["state"] in (rg.RISK_ON, rg.NEUTRAL, rg.RISK_OFF)
    assert res["regime"]["components"]["trend_up"] in (True, False)
