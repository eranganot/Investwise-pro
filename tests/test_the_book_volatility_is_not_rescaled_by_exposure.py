"""PR 55.1 is withdrawn. These pin why, so it cannot come back by accident.

WHAT SHIPPED AND WAS WRONG. PR 55 established that `/plan.portfolio_volatility_pct`
(44.62) and the realised `/portfolio/risk.annualized_volatility_pct` (67.48)
disagree by 23 points, and that the disagreement breaches a bound: the plan's
figure is a NAV-weighted average of component volatilities, and
``sqrt(w'Sw) <= sum(w.sigma)`` with equality only at perfect correlation, so no
portfolio built from those components can be more volatile than the average.
PR 55.1 then asserted the cause. Its argument: a sleeve in the market a
fraction ``f`` of the time and **flat otherwise** contributes no variance on the
flat days, so the variance measured across the whole window is ``f * sigma_in^2``
and the in-market volatility is ``sigma / sqrt(f)``. Applied to the book that
gave 68.02 against a realised 67.48, half a point apart, and the payload said so.

WHY IT IS WRONG, measured rather than argued. The scaling needs the
out-of-market days to be flat. They are not. `_weights_or_off` in
`strategy_backtest` holds the sleeve's ``base`` when the rule is off -- QQQ under
btm_swing_dip, SMH under btm_trend_soxl -- and falls back to cash only for a
strategy that declares ``risk_off``. Neither live sleeve does. Measured on the
shipped engine:

    sleeve            in market   true in-market vol   sigma/sqrt(f) said   error
    btm_swing_dip        15.6%          67.97%               85.96%       +17.99
    btm_trend_soxl       74.0%          93.95%               97.05%        +3.10

    out-of-market days on btm_swing_dip: 22.46% volatility, not zero.

The blend of the *formula's* numbers reconciled with the realised 67.48 (67.43).
The blend of the *true* in-market numbers is 60.01 -- below the realised figure,
so the corrected version does not reconcile either. Two errors of opposite sign
cancelled, and the agreement to half a point was the coincidence that made the
wrong model look confirmed. That is the specific way this defect was dangerous:
it produced its own evidence.

The residual gap has a second candidate nothing has separated out. The plan's
components are measured over each strategy's full backtest window, roughly ten
years; the realised figure covers 251 trading days. Two numbers over different
windows need not obey a bound derived for one.

SO THE APP ASSERTS NEITHER. `/plan` reports the blend, `/portfolio/risk` reports
what the book did, and the note names both candidates. Full record: STATUS.md B1.

Not financial advice.
"""
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

# The live components, with the exposure each backtest stores.
LIVE = [
    {"weight_pct": 35.09, "return_pct": 41.95, "volatility_pct": 83.95,
     "exposure_pct": 73.4, "return_source": "measured", "volatility_source": "measured"},
    {"weight_pct": 35.22, "return_pct": 16.04, "volatility_pct": 34.24,
     "exposure_pct": 15.6, "return_source": "measured", "volatility_source": "measured"},
    {"weight_pct": 26.7698, "return_pct": 6.44, "volatility_pct": 11.59,
     "exposure_pct": 100.0, "return_source": "measured", "volatility_source": "measured"},
    {"weight_pct": 2.9171, "return_pct": 3.0, "volatility_pct": 0.0,
     "return_source": "static", "volatility_source": "static"},
]


def test_the_blend_is_the_measured_average_and_says_which_window():
    """44.62, unchanged, and labelled with the window it came from.

    The label is the part that is new. The number was always the strategies'
    full-window average; nothing on the screen said so, which is how it came to
    be compared against a 251-day measurement as though they were rivals.
    """
    from app.services.measured_assumptions import blend

    out = blend(LIVE)
    assert out["volatility_pct"] == 44.62, out
    assert out["volatility_method"] == "weighted_average"
    assert out["volatility_is_upper_bound"] is True
    assert out["volatility_window"] == "strategy backtests, full measured window"


def test_no_conditional_volatility_is_published_anywhere():
    """The withdrawn keys are gone from the payload, not merely unused.

    A key left behind is a key something reads next quarter. `/plan` used to
    put the conditional figure in the headline and the blend beside it under
    `volatility_strategy_longrun`; both are removed with the number itself.
    """
    from app.services.measured_assumptions import blend

    out = blend(LIVE)
    leaked = [k for k in out if "conditional" in k]
    assert leaked == [], f"withdrawn PR 55.1 keys are back on the payload: {leaked}"


def test_the_exposure_floor_constant_is_gone():
    """It only existed to clamp sigma/sqrt(f). No scaling, no floor.

    Importable, it invites a caller to rebuild the formula around it.
    """
    import app.services.measured_assumptions as ma

    assert not hasattr(ma, "EXPOSURE_FLOOR_PCT")
    assert not hasattr(ma, "_in_market_volatility")


def test_exposure_is_reported_but_scales_nothing():
    """`exposure_pct` survives on components. It must stay inert.

    Keeping it is deliberate -- it is a real, useful fact about a sleeve, and
    the analysis that retired it is worth being able to redo. But two books
    identical except for exposure must report the same volatility, or the
    scaling has come back under another name.
    """
    from app.services.measured_assumptions import blend

    thin = [{"weight_pct": 100.0, "volatility_pct": 34.24, "exposure_pct": 15.6,
             "volatility_source": "measured"}]
    full = [{"weight_pct": 100.0, "volatility_pct": 34.24, "exposure_pct": 100.0,
             "volatility_source": "measured"}]
    absent = [{"weight_pct": 100.0, "volatility_pct": 34.24,
               "volatility_source": "measured"}]

    assert blend(thin)["volatility_pct"] == 34.24
    assert blend(full)["volatility_pct"] == 34.24
    assert blend(absent)["volatility_pct"] == 34.24


def test_the_plan_screen_publishes_the_blend_and_nothing_beside_it():
    """Where the withdrawn number actually reached a human.

    `_portfolio_stats` is read by eight screens. PR 55.1 made its `volatility`
    the conditional figure and added `volatility_strategy_longrun` alongside.
    Handed a payload that still carries a conditional key -- a stale cache, an
    old row, a future experiment -- the endpoint must ignore it rather than
    quietly promote it back to the headline.
    """
    from types import SimpleNamespace

    from app.api.routes.plan import _portfolio_stats

    rows = [SimpleNamespace(ticker="VTI", quantity=10, current_price=100.0,
                            market="US", meta={"asset_class": "Equities"})]
    out = _portfolio_stats(rows, {"volatility_pct": 44.62,
                                  "volatility_conditional_pct": 68.02,
                                  "expected_return_pct": 22.18, "basis": "mixed"})

    assert out["volatility"] == 44.62, out
    assert "volatility_strategy_longrun" not in out, out


def test_the_plan_payload_itself_carries_no_trace_of_the_withdrawn_number():
    """THE LAYER THE FIRST VERSION OF THIS TEST COULD NOT SEE.

    PR 55.1 added the figure at two sites under two names: `_portfolio_stats`
    produced `volatility_strategy_longrun`, and `_plan_dict` republished it as
    `portfolio_volatility_pct_strategy_longrun`. PR 56 removed the producer and
    the test above passed, because the test above reads the stats dict -- one
    layer below the rename. Production then answered with the key still on it,
    holding null.

    So this asserts on the payload the API actually returns. A key that only
    ever holds null is worse than an absent one: it reads as a measurement that
    came back empty. And the assertion is written against the whole payload
    rather than that one name, so the next retraction cannot leave a differently
    spelled survivor behind.
    """
    from types import SimpleNamespace

    from app.api.routes.plan import _plan_dict, _portfolio_stats

    rows = [SimpleNamespace(ticker="VTI", quantity=10, current_price=100.0,
                            market="US", meta={"asset_class": "Equities"})]
    stats = _portfolio_stats(rows, {"volatility_pct": 44.62,
                                    "volatility_conditional_pct": 68.02,
                                    "expected_return_pct": 22.18, "basis": "mixed"})
    plan = SimpleNamespace(id=1, objective="growth", risk_tolerance="aggressive",
                           currency="ILS", target_amount=100000.0,
                           target_date="2031-01-01", horizon_years=5,
                           target_roi_pct=30.0, target_roi_period="yearly",
                           target_yield_pct=None, target_yield_period=None,
                           preferred_depth=None, cash_target_pct=None)

    payload = _plan_dict(plan, stats)

    leaked = [k for k in payload if "longrun" in k or "conditional" in k]
    assert leaked == [], f"the withdrawn number is still named on /plan: {leaked}"
    assert payload["portfolio_volatility_pct"] == 44.62, payload


@pytest.mark.parametrize("sleeve_id", ["btm_swing_dip", "btm_trend_soxl"])
def test_a_sleeve_that_is_off_still_holds_something(sleeve_id):
    """THE FACT THAT KILLED THE FORMULA, pinned where it lives.

    sigma / sqrt(f) requires zero variance on the out-of-market days. Each live
    sleeve declares a `base` and no `risk_off`, so `_weights_or_off` hands it an
    equity basket on exactly those days. If a future strategy genuinely sits in
    cash when its gate closes it will declare `risk_off`, and this test will
    tell whoever adds it that the arithmetic changes for that one and not for
    these.
    """
    from app.services import strategy_catalog

    spec = strategy_catalog.get(sleeve_id)
    base = spec.get("base")
    assert base, f"{sleeve_id} has no base, so its off days would be cash"
    assert not spec.get("risk_off"), (
        f"{sleeve_id} declares risk_off; its off days may be flat after all")
    assert abs(sum(float(v) for v in base.values()) - 1.0) < 1e-9, base


def test_the_off_branch_returns_the_base_not_cash():
    """And the engine actually does it, rather than the spec merely saying so."""
    import numpy as np

    from app.engines.strategy_backtest import _weights_or_off

    on = np.array([True, False, True])
    out = _weights_or_off(on, {"TQQQ": 1.0}, None, {"QQQ": 1.0})
    assert out == [{"TQQQ": 1.0}, {"QQQ": 1.0}, {"TQQQ": 1.0}], out
    assert out[1] != {}, "an empty off-weight would be the flat book the formula assumed"


def test_the_note_names_both_candidates_and_asserts_neither():
    """What the screen is allowed to claim about the 23 points.

    It may say the bound is breached -- that is arithmetic. It may not say why,
    because the first attempt at why shipped, reconciled to half a point, and
    was wrong. The word 'contradiction' is specifically what came out: it framed
    an unexplained gap as a proven defect in one component.
    """
    from app.engines.portfolio_risk import analyze

    hist = {"A": [100.0, 108.0, 96.0, 110.0, 95.0, 112.0, 94.0, 115.0]}
    out = analyze(tickers=["A"], weights=[1.0], history_by_ticker=hist,
                  benchmark_returns=None, nav=10000.0,
                  assumed_volatility_pct=1.0)

    assert out["volatility_exceeds_assumption"] is True
    note = out["volatility_note"]
    assert "contradiction" not in note.lower(), note
    assert "window" in note.lower(), note
    assert "different instrument" in note.lower(), note
