"""W2 -- the regime gate's threshold was never a measurement.

`strategy_backtest.MAX_CAGR_GIVEUP_PCT = 1.0` has always carried the comment
that it is *"decided, not discovered"* -- "a filter that costs more than a point
a year is buying that shelter too dearly" is a preference about money, and 1.0
is where somebody put it.

On this catalog it is also decisive. `btm_vol_target_tqqq` measured
(reproduced live 2026-08-25, matching `STATUS.md:2911`):

    ungated   29.80%/yr at -63.51%
    gated     28.09%/yr at -55.19%
    deltas    -1.71%/yr for -8.32 points of drawdown

It fails ONLY because 1.71 > 1.0. `STATUS.md:2931` already put the question:
*"on a 3x leveraged sleeve, 8 points of drawdown for under 2%/yr is arguably a
trade worth taking... whether vol_target should be switched on is Eran's call."*

So W2 is not a measurement, it is a threshold handed back to its owner:

  * the limit is a **setting**, and the verdict reports which one produced it;
  * a gate that failed on COST alone reports **the limit at which it passes** --
    "it failed" and "it would pass at 1.71" are the same measurement and only
    the second is a decision a reader can make;
  * the opt-in is per strategy and **still off by default**, because the rule in
    `backtest_service` is unchanged: the gate ships off until a human has
    compared the two numbers. What changed is that there is now a way to say so
    that is not a code edit.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.engines import strategy_backtest as bt  # noqa: E402


def _pair(*, d_cagr, d_dd, base_cagr=30.0, base_dd=60.0):
    ungated = {"ok": True, "cagr_pct": base_cagr, "max_drawdown_pct": base_dd,
               "observations": 2513}
    gated = {"ok": True, "cagr_pct": base_cagr + d_cagr,
             "max_drawdown_pct": base_dd + d_dd, "observations": 2513}
    return ungated, gated


# ===========================================================================
# the limit is an argument now
# ===========================================================================

def test_the_default_limit_is_unchanged_when_nobody_passes_one():
    """No existing caller may change behaviour."""
    u, g = _pair(d_cagr=-1.71, d_dd=-8.32)
    v = bt.gate_verdict(u, g)
    assert v["cagr_giveup_limit_pct"] == bt.MAX_CAGR_GIVEUP_PCT
    assert v["improves"] is False


def test_the_live_vol_target_numbers_reproduce():
    """Measured 2026-08-25 against Yahoo, matching STATUS.md:2911."""
    u, g = _pair(d_cagr=-1.71, d_dd=-8.32, base_cagr=29.80, base_dd=63.51)
    v = bt.gate_verdict(u, g)
    assert v["cagr_delta_pct"] == -1.71
    assert v["max_drawdown_delta_pct"] == -8.32
    assert v["improves"] is False, "it fails only on cost"


def test_raising_the_limit_flips_the_same_measurement():
    """Nothing about the strategy changed. The verdict is a preference applied
    to a measurement, and W2 is about making that visible."""
    u, g = _pair(d_cagr=-1.71, d_dd=-8.32)
    assert bt.gate_verdict(u, g, max_giveup_pct=1.0)["improves"] is False
    assert bt.gate_verdict(u, g, max_giveup_pct=2.0)["improves"] is True


def test_the_verdict_says_whether_the_limit_is_still_the_shipped_one():
    """"The user chose this limit" is exactly the claim a reader needs to check.
    `is None` would have read False even when nobody had changed anything,
    because `measure()` always passes the setting."""
    u, g = _pair(d_cagr=-1.71, d_dd=-8.32)
    assert bt.gate_verdict(u, g, max_giveup_pct=bt.MAX_CAGR_GIVEUP_PCT)["limit_is_default"] is True
    assert bt.gate_verdict(u, g, max_giveup_pct=2.0)["limit_is_default"] is False


# ===========================================================================
# "it failed" and "it would pass at 1.71" are the same measurement
# ===========================================================================

def test_a_gate_that_lost_on_cost_reports_where_it_would_pass():
    u, g = _pair(d_cagr=-1.71, d_dd=-8.32)
    v = bt.gate_verdict(u, g)
    assert v["would_improve_at_limit_pct"] == 1.71
    assert "1.71" in v["flip_note"]
    # And it names what the reader would be buying with that limit.
    assert "8.3 points of drawdown" in v["flip_note"]


def test_a_gate_that_already_passes_offers_no_flip_point():
    u, g = _pair(d_cagr=-0.4, d_dd=-8.0)
    v = bt.gate_verdict(u, g)
    assert v["improves"] is True
    assert v["would_improve_at_limit_pct"] is None


def test_a_gate_that_failed_on_DRAWDOWN_is_never_offered_a_higher_limit():
    """No limit can rescue a gate that did not reduce drawdown, and offering one
    would invite raising the limit until something passes."""
    u, g = _pair(d_cagr=-0.1, d_dd=+2.0)
    v = bt.gate_verdict(u, g)
    assert v["improves"] is False
    assert v["would_improve_at_limit_pct"] is None
    assert v["flip_note"] is None
    assert "not reduced" in v["why"]


@pytest.mark.parametrize("d_cagr", [-1.01, -1.71, -3.0, -8.0])
def test_the_flip_point_is_always_the_cost_itself(d_cagr):
    u, g = _pair(d_cagr=d_cagr, d_dd=-8.0)
    v = bt.gate_verdict(u, g)
    assert v["would_improve_at_limit_pct"] == round(abs(d_cagr), 2)
    # And applying it flips the verdict, which is what makes the number useful.
    assert bt.gate_verdict(u, g, max_giveup_pct=abs(d_cagr))["improves"] is True


def test_an_incomparable_pair_still_refuses_to_guess():
    v = bt.gate_verdict({"ok": False}, {"ok": True, "cagr_pct": 1, "max_drawdown_pct": 1})
    assert v["comparable"] is False


# ===========================================================================
# the opt-in, and the rule it does not break
# ===========================================================================

def test_the_gate_still_ships_off_by_default():
    """`backtest_service`'s rule is unchanged: the gate ships off everywhere
    until a human has compared the two numbers."""
    from app.core.config import Settings

    assert Settings().regime_gate_enabled_for == ""
    assert Settings().regime_gate_max_cagr_giveup_pct == bt.MAX_CAGR_GIVEUP_PCT


def test_the_engine_version_moved_with_the_meaning_of_a_row():
    """An opted-in row's headline numbers are the GATED run. A gated CAGR
    sitting in the same field as an ungated one, with nothing to tell them
    apart, is worse than not having the gate - so the version bumped and every
    row records which run produced it."""
    from app.services import backtest_service as bs

    assert bs.ENGINE_VERSION == "a5"
    import inspect
    src = inspect.getsource(bs.measure)
    assert "regime_gate_applied" in src


def test_an_opted_in_strategy_that_could_not_be_gated_says_so(monkeypatch):
    """Falling back to ungated numbers SILENTLY would put an ungated CAGR under
    a row that claims to be gated."""
    from app.core.config import get_settings
    from app.services import backtest_service as bs

    monkeypatch.setenv("REGIME_GATE_ENABLED_FOR", "x1")
    get_settings.cache_clear()
    try:
        calls = {"n": 0}

        def _run(series, spec, **kw):
            calls["n"] += 1
            if kw.get("regime_gate"):
                return {"ok": False, "reason": "INSUFFICIENT_HISTORY", "detail": "x"}
            return {"ok": True, "cagr_pct": 10.0, "max_drawdown_pct": -20.0,
                    "observations": 2513, "start": "a", "end": "b"}

        monkeypatch.setattr(bs.bt, "run", _run)
        monkeypatch.setattr(bs, "_fetch", lambda t: ({"A": [("d", 1.0)] * 5}, []))
        monkeypatch.setattr(bs.bt, "tickers_needed", lambda s, **k: ["A"])
        monkeypatch.setattr(bs.bt, "out_of_sample", lambda *a, **k: {})
        out = bs.measure({"id": "x1", "overlay": {"kind": "buy_hold"}})
        reg = out["robustness"]["regime"]
        assert reg["enabled"] is True
        assert reg["headline_is_gated"] is False
        assert "UNGATED" in reg["opt_in_not_honoured"]
        assert out["regime_gate_applied"] is False
    finally:
        get_settings.cache_clear()


def test_the_ungated_numbers_are_kept_beside_the_gated_ones():
    """Whichever run is the headline, the other must remain visible - the
    comparison is the whole product of W2."""
    import inspect

    from app.services import backtest_service as bs

    src = inspect.getsource(bs.measure)
    assert '"ungated"' in src


def test_the_comparison_is_rendered_somewhere():
    """It was computed nightly and read by NOTHING. A measurement nobody can see
    cannot be the human comparison the ship-off rule is waiting for."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    html = (root / "app" / "static_app" / "index.html").read_text(encoding="utf-8")
    assert "robustness||{}).regime" in html
    assert "would_improve_at_limit_pct" in html
