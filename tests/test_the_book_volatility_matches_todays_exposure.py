"""The plan's volatility described the strategies. The book is not the strategies.

PR 55 established that `/plan.portfolio_volatility_pct` (44.62) and the realised
`/portfolio/risk.annualized_volatility_pct` (67.48) disagreed by 23 points, and
that the disagreement was a **contradiction**: the plan's figure is a
NAV-weighted average of component volatilities, which is an upper bound on the
volatility of any portfolio built from them, and the realised figure was above
it.

**This is why, and it is measured rather than inferred.** The backtests already
store `time_in_market_pct`, and it accounts for the whole gap:

    sleeve                    w%     vol%   in-mkt%   implied in-market vol%
    btm_trend_soxl (SOXL)   35.09   83.95     73.4            97.99
    btm_swing_dip  (TQQQ)   35.22   34.24     15.6            86.69
    bal_6040 core           26.77   11.59    100.0            11.59
    cash                     2.92    0.00      ---             0.00

    blend of LONG-RUN  vols = 44.62%   <- what /plan reported
    blend of IN-MARKET vols = 68.02%   <- realised measured 67.48%

Half a point. `btm_swing_dip` holds TQQQ but sits in cash **84.4% of the time**,
so its 34.24% is a ten-year average made mostly of flat days. The book is
holding TQQQ right now, so that sleeve is contributing something near 87%
volatility on a third of the book, and has been all along.

THE SCALING, stated so it can be argued with. If a strategy is in the market a
fraction `f` of the time and flat otherwise, the flat days contribute no
variance, so the variance measured over the whole window is `f * sigma_in^2` and

    sigma_in = sigma_measured / sqrt(f)

It assumes out-of-market days are genuinely flat (true when the sleeve sits in
cash) and that in-market days are homogeneous. The reconciliation above is the
evidence that it holds here; it is an estimate, not an identity.

**Both numbers are true and they answer different questions.** What the book is
doing *now*, with its sleeves in the market, is ~68%. What it will average over
a year, with `btm_swing_dip` flat most of it, is nearer 44.6%. Eran's decision,
2026-09-04: the headline is the one about today's exposure, because that is what
every other risk figure on the screen is about -- VaR is a statement about the
book you hold this morning. The long-run figure stays, beside it, labelled.

Not financial advice.
"""
import math
import os

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

REALISED_PCT = 67.48       # /portfolio/risk on the same book, same moment


def test_the_long_run_blend_is_unchanged():
    """The number that was there is still there, and still 44.62.

    A change that moves the old figure as a side effect would make the two
    incomparable, which is the opposite of the point.
    """
    from app.services.measured_assumptions import blend

    out = blend(LIVE)
    assert out["volatility_pct"] == 44.62, out
    assert out["volatility_method"] == "weighted_average"
    assert out["volatility_is_upper_bound"] is True


def test_the_conditional_blend_reconciles_with_what_the_book_actually_did():
    """68.02 against a measured 67.48. That is the whole argument.

    If this ever drifts far from the realised figure, the scaling assumption
    has stopped holding for this book and the headline number is wrong again --
    which is worth failing over rather than discovering on a screen.
    """
    from app.services.measured_assumptions import blend

    out = blend(LIVE)
    cond = out["volatility_conditional_pct"]
    assert abs(cond - 68.02) < 0.05, (
        f"the conditional blend is {cond}, not the 68.02 that reconciles with "
        f"the live book")
    assert abs(cond - REALISED_PCT) < 2.0, (
        f"the conditional blend {cond} is {abs(cond - REALISED_PCT):.2f} points "
        f"from the realised {REALISED_PCT}. The sigma/sqrt(f) scaling assumes "
        f"out-of-market days are flat; a gap this size means it has stopped "
        f"describing this book.")

    # And it is still an upper bound, so the contradiction test stays valid.
    assert cond >= REALISED_PCT, (cond, REALISED_PCT)


def test_a_sleeve_that_is_always_in_the_market_is_untouched():
    """Conditioning must do nothing where there is nothing to condition on.

    The core holds its basket every day, so its 11.59 is already an in-market
    number and scaling it would invent risk that is not there.
    """
    from app.services.measured_assumptions import blend

    always = [{"weight_pct": 100.0, "return_pct": 6.44, "volatility_pct": 11.59,
               "exposure_pct": 100.0, "volatility_source": "measured"}]
    out = blend(always)
    assert out["volatility_pct"] == 11.59
    assert out["volatility_conditional_pct"] == 11.59


def test_no_exposure_recorded_means_no_conditioning():
    """An absent exposure is not an exposure of zero.

    A component with no `exposure_pct` -- the cash line, or a sleeve whose
    backtest predates the field -- keeps its measured number rather than being
    scaled by a guess.
    """
    from app.services.measured_assumptions import blend

    out = blend([{"weight_pct": 100.0, "volatility_pct": 30.0,
                  "volatility_source": "measured"}])
    assert out["volatility_pct"] == 30.0
    assert out["volatility_conditional_pct"] == 30.0


def test_a_vanishing_exposure_is_floored_and_says_so():
    """sigma / sqrt(f) explodes as f approaches zero.

    A sleeve in the market 1% of the time would imply ten times its measured
    volatility, which is arithmetic rather than evidence: at that exposure the
    measured figure rests on a handful of days. The floor caps the scaling and
    the payload records that it bound, because a silent clamp is a lie the size
    of the difference.
    """
    from app.services.measured_assumptions import EXPOSURE_FLOOR_PCT, blend

    tiny = [{"weight_pct": 100.0, "volatility_pct": 30.0, "exposure_pct": 1.0,
             "volatility_source": "measured"}]
    out = blend(tiny)

    capped = 30.0 / math.sqrt(EXPOSURE_FLOOR_PCT / 100.0)
    assert abs(out["volatility_conditional_pct"] - capped) < 0.01, out
    assert out.get("volatility_conditional_floored") is True, out


def test_the_two_numbers_are_named_so_neither_can_be_mistaken_for_the_other():
    """Both are true; they answer different questions.

    ~68% is what the book is doing today, with its sleeves in the market. ~44.6%
    is what it will average over a year, with btm_swing_dip flat 84% of it. A
    payload that carries one unlabelled number is how the app came to compare
    the second against a realised measurement of the first.
    """
    from app.services.measured_assumptions import blend

    out = blend(LIVE)
    assert out["volatility_method"] == "weighted_average"
    assert "volatility_pct" in out and "volatility_conditional_pct" in out
    assert out["volatility_conditional_method"] == "weighted_average_in_market", out
