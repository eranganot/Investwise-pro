"""Two forward numbers, both built on a basis nothing on the screen states.

Measured on production 2026-09-04, commit `6044124`, one book, one moment.

**1. The goal projection extrapolates the trailing year.**

    POST /api/v1/portfolio/risk
      annualized_return_pct   107.08
      goal: {"target": 102973.0, "years": 5.0,
             "prob_reach": 0.9604,
             "median_terminal": 1407623.15,
             "p90_terminal": 9601433.93}

`index.html:2890` renders that as **"Reach 102,973 in 5y - 96%"** on a
ILS 21,208 book. The drift is `port_rets.mean() * 252` -- the last 251 trading
days annualised -- pushed five years through a GBM. A 3x-leveraged NASDAQ and
semiconductor book after a good year is the worst possible input for that, and
the number it produces is the most confident thing on the screen.

The plan holds a stated forward return with provenance and a caveat already
attached: `expected_return_pct` 22.18, `return_basis` "measured_full", basis
"mixed", 97.1% of the book measured, and a caveat that reads *"A measured CAGR
is what happened over one window, not what will happen."* Nothing used it here.

**2. The plan's volatility is not a portfolio volatility.**

`measured_assumptions.blend` computes `sum(w*sigma) / sum(w)` -- a NAV-weighted
AVERAGE of component volatilities. Reconciled exactly against the live
components:

    35.09% x 83.95  +  35.22% x 34.24  +  26.77% x 11.59  +  2.92% x 0.00
      = 44.62      <- `/plan.portfolio_volatility_pct`

Expected return is linear, so blending returns that way is correct and
`expected_return_pct` 22.18 checks out the same way. **Volatility is not**:
portfolio volatility is `sqrt(w'Sw)`, and a weighted average of component
volatilities equals it only when every pair is perfectly correlated. Otherwise
the weighted average is strictly larger. It is an UPPER BOUND, not an estimate.

**3. Which makes the disagreement a contradiction, not a difference.**

    /plan.portfolio_volatility_pct        44.62   (upper bound)
    /portfolio/risk.annualized_volatility 67.48   (realised, real covariance)

**The realised volatility exceeds the upper bound.** Under those component
volatilities the book *cannot* be more volatile than 44.62; it measured 67.48.
So at least one component understates what the book is actually doing -- which
is what you would expect when a sleeve's ten-year backtest includes the periods
it sat in cash, and the book is holding the leveraged names right now. Two
honest measurements of two different questions, and nothing said which question
was being answered.

`book_assumptions`'s own docstring already reached for half of this: *"a
per-ticker table has no covariance term -- so it overstates the core's
volatility by construction."* It is true of the whole blend, not only the core.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import numpy as np  # noqa: E402

# The live components, exactly as `/plan.assumptions` reports them.
LIVE_COMPONENTS = [
    {"weight_pct": 35.09, "return_pct": 41.95, "volatility_pct": 83.95,
     "return_source": "measured", "volatility_source": "measured"},
    {"weight_pct": 35.22, "return_pct": 16.04, "volatility_pct": 34.24,
     "return_source": "measured", "volatility_source": "measured"},
    {"weight_pct": 26.7698, "return_pct": 6.44, "volatility_pct": 11.59,
     "return_source": "measured", "volatility_source": "measured"},
    {"weight_pct": 2.9171, "return_pct": 3.0, "volatility_pct": 0.0,
     "return_source": "static", "volatility_source": "static"},
]

TRAILING_DRIFT_PCT = 107.08      # what the last 251 days annualise to
STATED_DRIFT_PCT = 22.18         # what the plan actually assumes


def _paths(n_assets=2, days=520, seed=3, drift=0.0015, vol=0.02):
    """Correlated-ish random walks with a strong upward drift.

    The point is a history whose TRAILING return is large, so a projection
    driven by it is visibly different from one driven by a stated assumption.
    """
    rng = np.random.default_rng(seed)
    out = {}
    common = rng.standard_normal(days)
    for i in range(n_assets):
        shocks = drift + vol * (0.7 * common + 0.7 * rng.standard_normal(days))
        out[f"T{i}"] = list(100.0 * np.cumprod(1.0 + shocks))
    return out


def test_the_blend_is_a_weighted_average_and_says_so():
    """Reconciled against the live payload, then labelled.

    Both halves are checked because only one of them is wrong: expectation is
    linear, so blending returns this way is correct. Volatility is not linear
    in the weights, and calling the result `volatility_pct` with no method
    beside it is how it came to be read as the book's volatility.
    """
    from app.services.measured_assumptions import blend

    out = blend(LIVE_COMPONENTS)
    assert out["expected_return_pct"] == 22.18, out
    assert out["volatility_pct"] == 44.62, out

    assert out.get("volatility_method") == "weighted_average", (
        f"the payload does not say how the volatility was computed, so a "
        f"reader cannot tell it from a portfolio volatility: {out}")


def test_the_blended_volatility_is_an_upper_bound_and_the_payload_says_which_way():
    """sqrt(w'Sw) <= sum(w*sigma), with equality only at perfect correlation.

    Stated as a direction rather than a caveat, because the direction is what
    makes the live disagreement a contradiction: realised 67.48 against an
    upper bound of 44.62 cannot both describe the same components.
    """
    from app.services.measured_assumptions import blend

    out = blend(LIVE_COMPONENTS)
    assert out.get("volatility_is_upper_bound") is True, (
        f"nothing records that this number cannot be exceeded by a portfolio "
        f"built from these components: {out}")

    # The property itself, on a two-asset book, so the claim is checked and not
    # merely asserted about.
    w = np.array([0.5, 0.5])
    sig = np.array([0.30, 0.10])
    for rho in (0.0, 0.5, 0.999):
        cov = np.array([[sig[0] ** 2, rho * sig[0] * sig[1]],
                        [rho * sig[0] * sig[1], sig[1] ** 2]])
        true_vol = float(np.sqrt(w @ cov @ w))
        weighted_avg = float(w @ sig)
        assert true_vol <= weighted_avg + 1e-12, (rho, true_vol, weighted_avg)


def test_the_goal_projection_uses_the_stated_assumption_not_the_trailing_year():
    """A five-year probability may not be built from what just happened.

    Same history, two different stated assumptions: the projection has to move
    with the assumption. If it does not, it is still being driven by the trailing
    mean and the argument is decorative.
    """
    from app.engines.portfolio_risk import analyze

    hist = _paths()
    common = dict(tickers=list(hist), weights=[1.0, 1.0], history_by_ticker=hist,
                  nav=21_207.78, target=102_973.0, years=5.0)

    low = analyze(**common, drift_pct=5.0)
    high = analyze(**common, drift_pct=40.0)

    assert low["ok"] and high["ok"]
    assert low["goal"]["prob_reach"] < high["goal"]["prob_reach"], (
        f"the projection did not move with the stated drift, so it is still "
        f"being driven by the trailing return: {low['goal']} vs {high['goal']}")

    # And the trailing figure is still reported -- labelled as what it is,
    # because hiding it would trade one unstated basis for another.
    assert "trailing_return_pct" in low, low
    assert low["annualized_return_pct"] == low["trailing_return_pct"], low


def test_the_stated_rate_is_compounded_and_the_drag_is_not_applied_twice():
    """`drift_pct` is a CAGR, so the median compounds at exactly that rate.

    This is the convention decision, and it changes the answer by more than the
    fix does, so it is asserted rather than left in a comment.
    `measured_assumptions` builds each component's return from
    `metrics["cagr_pct"]` and its caveat calls it "a measured CAGR". A CAGR is
    already geometric -- the -0.5*sigma^2 drag is inside it -- so subtracting
    the drag again would be wrong, and on the live book spectacularly so: sigma
    is 67.5%, `0.5*sigma^2` is 22.8%, and it would have cancelled the whole
    22.18% assumption and put the median five-year outcome below today's NAV
    for arithmetic reasons rather than for anything measured.
    """
    from app.engines.portfolio_risk import analyze

    hist = _paths()
    nav, years, g = 21_207.78, 5.0, STATED_DRIFT_PCT / 100.0
    out = analyze(tickers=list(hist), weights=[1.0, 1.0], history_by_ticker=hist,
                  nav=nav, target=102_973.0, years=years, drift_pct=STATED_DRIFT_PCT)

    assert out["goal"]["drift_convention"] == "cagr", out["goal"]
    want = nav * np.exp(g * years)
    got = out["goal"]["median_terminal"]
    assert abs(got / want - 1.0) < 0.05, (
        f"the median terminal is {got:,.0f}; compounding {STATED_DRIFT_PCT}% for "
        f"{years:g} years gives {want:,.0f}. A gap this size means the "
        f"volatility drag is being applied to a rate that already includes it.")


def test_no_stated_assumption_means_no_probability():
    """A probability with no stated basis is worse than no probability.

    The old behaviour was to fall back to the trailing mean, which is exactly
    the thing being removed; falling back to it when the assumption is missing
    would keep the defect and hide it better.
    """
    from app.engines.portfolio_risk import analyze

    hist = _paths()
    out = analyze(tickers=list(hist), weights=[1.0, 1.0], history_by_ticker=hist,
                  nav=21_207.78, target=102_973.0, years=5.0, drift_pct=None)

    assert out["ok"], out
    assert out.get("goal") is None, (
        f"a goal probability was produced with no stated forward return: "
        f"{out.get('goal')}")
    assert out.get("goal_omitted_reason"), (
        "the payload drops the projection without saying why, which reads as a "
        "missing feature rather than a deliberate refusal")


def test_the_risk_report_carries_the_assumption_it_contradicts():
    """The reconciliation belongs where both numbers already exist.

    `/plan` has no price history and adding it would put N provider fetches on
    a hot endpoint. The risk endpoint already reads the plan and already
    computes the covariance, so it is the one place the two can be compared
    without paying for anything twice.
    """
    from app.engines.portfolio_risk import analyze

    hist = _paths()
    out = analyze(tickers=list(hist), weights=[1.0, 1.0], history_by_ticker=hist,
                  nav=21_207.78, target=102_973.0, years=5.0,
                  drift_pct=STATED_DRIFT_PCT, assumed_volatility_pct=44.62)

    assert out["assumed_volatility_pct"] == 44.62, out
    realised = out["annualized_volatility_pct"]

    if realised > 44.62:
        assert out.get("volatility_exceeds_assumption") is True, (
            f"realised {realised} is above the assumption's upper bound of "
            f"44.62 and the payload does not flag it: {out}")
        assert out.get("volatility_note"), out
    else:
        assert out.get("volatility_exceeds_assumption") is False, out
