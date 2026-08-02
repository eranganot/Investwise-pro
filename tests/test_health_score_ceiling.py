"""Regression: the wealth health score had invisible ceilings.

Reported: "how come I can't be more than 80?" -- and the number was right.
Three hard caps, none of them shown to the user:

  * `thematic` was passed as the constant 60.0 at 15% weight, so a flawless
    portfolio could only reach 94, and 15% of the score was unexplainable;
  * tax efficiency started from an arbitrary base of 85, so that component
    could never reach 100 no matter what the user did;
  * risk was `100 - vol% x 2`, which only reaches 100 at zero volatility --
    the app marked you down simply for being invested, contradicting every
    recommendation it made.

The screenshot's 78 was exactly .25(70)+.25(84)+.20(100)+.15(73)+.15(60).
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.engines.whs_engine import MEASURED_WEIGHTS, WhsEngine  # noqa: E402
from app.services.portfolio_analytics import (  # noqa: E402
    health_scores, risk_score_vs_budget,
)


def test_measured_weights_sum_to_one_and_exclude_thematic():
    assert abs(sum(MEASURED_WEIGHTS.values()) - 1.0) < 1e-9
    assert "thematic" not in MEASURED_WEIGHTS


def test_perfect_portfolio_can_actually_reach_100():
    """The old composite topped out at 94 because of the thematic constant."""
    out = WhsEngine().compute(risk=100, tax=100, alloc=100, liq=100)
    assert out["score"] == 100.0
    assert out["rating"] == "Strong"


def test_legacy_five_component_path_still_works():
    """The standalone /whs endpoint keeps its original weighting."""
    out = WhsEngine().compute(risk=100, tax=100, alloc=100, liq=100, thematic=60)
    assert out["score"] == 94.0
    assert "thematic" in out["components"]


def test_risk_is_scored_against_the_plan_budget_not_against_zero():
    # Exactly at a Medium investor's 15% volatility cap: on plan, not unhealthy.
    assert risk_score_vs_budget(15.0, 0.15) == 85.0
    # Half the budget scores better; zero volatility is the only 100.
    assert risk_score_vs_budget(7.5, 0.15) == 92.5
    assert risk_score_vs_budget(0.0, 0.15) == 100.0
    # A High-tolerance investor at 20% vol is inside their 25% budget and is no
    # longer punished for the volatility their objective requires. Under the old
    # formula this was 100 - 20*2 = 60.
    assert risk_score_vs_budget(20.0, 0.25) > 85.0
    # Overshooting the budget still costs, and 2x the cap bottoms out.
    assert risk_score_vs_budget(30.0, 0.15) == 0.0
    assert 0.0 < risk_score_vs_budget(20.0, 0.15) < 85.0


def test_clean_book_has_no_hidden_ceiling():
    snap = {"avg_volatility_pct": 0.0, "max_weight": 0.10, "liquidity_avg": 100.0,
            "unrealized_losses": 0.0, "nav": 100_000.0}
    sc = health_scores(snap, cap=0.25, vol_cap=0.15)
    assert sc["tax_efficiency_score"] == 100      # was capped at 85 by the base
    assert sc["wealth_health_score"] == 100
    assert sc["max_achievable"] == 100


def test_unharvested_losses_still_dent_tax_efficiency():
    snap = {"avg_volatility_pct": 0.0, "max_weight": 0.10, "liquidity_avg": 100.0,
            "unrealized_losses": 5_000.0, "nav": 100_000.0}
    sc = health_scores(snap, cap=0.25, vol_cap=0.15)
    assert sc["tax_efficiency_score"] == 90       # 100 - 0.05*200
    assert sc["wealth_health_score"] < 100
