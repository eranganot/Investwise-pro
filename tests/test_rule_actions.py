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


def test_stops_become_a_full_exit():
    """A stop-loss is a full exit by definition - that's what the order type is.

    A trailing stop likewise: the gain it was protecting is evaporating. These
    two stay full exits. `take_profit` no longer does -- see below.
    """
    for rt in ("stop_loss", "trailing_stop"):
        plan = execution_plan(_Rule(rt), POS)
        assert plan is not None, rt
        assert plan["kind"] == "sell_position"
        assert plan["shares"] == 12.0
        assert plan["ticker"] == "META"


def test_take_profit_takes_some_off_the_table_rather_than_liquidating():
    """It returned `sell_position` -- a FULL exit -- while the note the app
    writes on its own take-profit rules read "Takes some off the table if it
    climbs ~10% more". Live on QQQ at 791.02 and USO at 168.17 on 2026-09-14.
    A card may not claim a change it does not produce."""
    plan = execution_plan(_Rule("take_profit"), POS)
    assert plan["kind"] == "trim", "a take-profit trims; it does not liquidate"
    assert plan["shares"] == round(12.0 * 0.25, 6), "the house slice is a quarter"
    assert plan["ladder"] is True
    assert plan["ticker"] == "META"


def test_a_rule_may_size_its_own_slice():
    r = _Rule("take_profit")
    r.trim_fraction = 0.5
    assert execution_plan(r, POS)["shares"] == round(12.0 * 0.5, 6)


def test_a_nonsense_slice_falls_back_to_the_house_size():
    """Null is the default; zero would be a rule that sells nothing, and
    anything above 1 is a liquidation wearing a trim's label."""
    for bad in (0.0, -0.2, 1.5, "banana", None):
        r = _Rule("take_profit")
        r.trim_fraction = bad
        assert execution_plan(r, POS)["shares"] == round(12.0 * 0.25, 6), bad


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
