"""Trading rules must drive real action, and leave a record of it.

Reported: "if the app hit the trading rules I would expect to see a difference in
my holdings but I can't see anything" and "I see the trading rule triggered but I
can't see the actions that were taken".

Cause: `triggered_rule_recs` emitted cards with no `apply` key at all, so every
firing rendered as "Guidance - you act on this yourself" and Accept could never
execute; and a firing left only `triggered`/`last_triggered_at` on the rule, so
nothing recorded what happened next.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.services.recommendations import _ACTIONABLE_KINDS  # noqa: E402
from app.services.rules_service import execution_plan  # noqa: E402


class _Rule:
    def __init__(self, rule_type, level=10.0, ticker="META"):
        self.rule_type, self.level, self.ticker = rule_type, level, ticker


POS = {"price": 556.71, "cost": 600.0, "qty": 12.0, "weight_pct": 40.0}


def test_stops_and_take_profit_become_a_full_exit():
    """A stop-loss is a full exit by definition - that's what the order type is."""
    for rt in ("stop_loss", "trailing_stop", "take_profit"):
        plan = execution_plan(_Rule(rt), POS)
        assert plan is not None, rt
        assert plan["kind"] == "sell_position"
        assert plan["shares"] == 12.0
        assert plan["ticker"] == "META"


def test_max_weight_trims_back_to_the_cap_exactly():
    """35% cap on a 40% position -> sell the 5/40 overshoot, no invented size."""
    plan = execution_plan(_Rule("max_weight", level=35.0), POS)
    assert plan["kind"] == "trim"
    assert plan["shares"] == round(12.0 * (40.0 - 35.0) / 40.0, 6)


def test_advisory_rule_types_do_not_guess_a_trade():
    """Price alerts carry no trade; buy-the-dip needs a funding decision."""
    for rt in ("price_above", "price_below", "buy_dip"):
        assert execution_plan(_Rule(rt), POS) is None


def test_no_plan_when_the_position_is_gone():
    assert execution_plan(_Rule("stop_loss"), {**POS, "qty": 0.0}) is None


def test_max_weight_below_cap_is_not_actionable():
    assert execution_plan(_Rule("max_weight", level=50.0), POS) is None


def test_sell_position_is_registered_as_executable():
    """Without this the card would still render as advice-only."""
    assert "sell_position" in _ACTIONABLE_KINDS
    assert "trim" in _ACTIONABLE_KINDS
