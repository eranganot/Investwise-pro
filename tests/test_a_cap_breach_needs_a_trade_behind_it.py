"""PR 66. Eran, 2026-09-06, on two cards he could not act on:

    "why do I still getting guidance cards? all these cards should be actionable"

He was right that something was wrong and wrong about what. Measured on the live
book the same minute:

    SOXL  38.002% against a 38% cap  ->  0.44 shekels to trim
    QQQ   37.004% against a 37% cap  ->  0.87 shekels to trim

Both fired, both latched, and both rendered a card whose own `how` read *"There's
no single obvious trade here, so the app won't guess one."* The Rules tab said it
outright: **"Triggered · 37.0% of book vs a 37% cap"**.

So the cards were guidance because there was nothing to trade. **The defect is
not the missing Accept button — it is that the cards existed at all.**

## The cause is structural, not a rounding accident

1. `_arm_sleeve_caps` arms a `max_weight` cap at **exactly** the sleeve's target.
   Both rules carry `_cap_note`'s wording, so both are sleeve-armed.
2. `_fund_sleeve` funds the sleeve to **exactly** that target.
3. `_evaluate` fired on `weight >= level` — **at equality**.

So every correctly funded sleeve tripped its own cap the moment it was funded.
**QQQ's card exists because PR 61 worked**: the sleeve was funded to 37% and the
cap armed at 37% fired on it.

Then `execution_plan` refused to name a trade — twice over, since `weight_pct` is
rounded to one decimal so `38.0 <= 38.0`, and its own `MIN_TRADE_ILS` guard would
have refused ₪0.44 anyway. And `clears_with_margin` wanted SOXL down at 37.05%
before it would re-arm: a nag with no exit.

## The fix uses a number the app already has

`MIN_TRADE_ILS`, not a new tolerance band. `execution_plan` has applied exactly
this test since the MSFT case — one layer too late, where it could only downgrade
the card instead of preventing it. Moving it into the firing test means a real
breach still fires **and is actionable**, which is what Eran actually asked for.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.services import rules_service as rs  # noqa: E402
from app.services.funding_service import MIN_TRADE_ILS  # noqa: E402


class _Rule:
    def __init__(self, ticker="SOXL", level=38.0, rule_type="max_weight", mode="pct"):
        self.ticker, self.level = ticker, level
        self.rule_type, self.mode = rule_type, mode
        self.peak_price = None


# The live book, 2026-09-06 20:0x UTC. NAV 21,859.69.
SOXL_VALUE, SOXL_W = 8307.07, 38.002
QQQ_VALUE, QQQ_W = 8088.90, 37.004


# --------------------------------------------------------------------------- #
# 1. the arithmetic, in shekels
# --------------------------------------------------------------------------- #
def test_the_two_breaches_were_worth_less_than_a_shekel():
    """44 and 87 agorot. This is the whole bug in two numbers."""
    assert rs.cap_trim_ils(38.0, SOXL_W, SOXL_VALUE) == pytest.approx(0.44, abs=0.05)
    assert rs.cap_trim_ils(37.0, QQQ_W, QQQ_VALUE) == pytest.approx(0.87, abs=0.05)
    assert MIN_TRADE_ILS == 250.0


def test_a_position_at_its_cap_has_nothing_to_trim():
    """Equality is where a correctly funded sleeve sits, by construction."""
    assert rs.cap_trim_ils(38.0, 38.0, SOXL_VALUE) == 0.0
    assert rs.cap_trim_ils(38.0, 37.5, SOXL_VALUE) == 0.0


# --------------------------------------------------------------------------- #
# 2. the firing test
# --------------------------------------------------------------------------- #
def test_the_cap_no_longer_fires_on_a_funded_sleeve():
    hit, _t, _a, _lvl = rs._evaluate(_Rule(level=38.0), 117.28, 90.0, SOXL_W, SOXL_VALUE)
    assert hit is False, "SOXL still fires at 38.002% against a 38% cap"

    hit, _t, _a, _lvl = rs._evaluate(_Rule("QQQ", 37.0), 718.96, 600.0, QQQ_W, QQQ_VALUE)
    assert hit is False, "QQQ still fires at 37.004% against a 37% cap"


def test_it_fired_at_exact_equality_which_is_where_a_funded_sleeve_lands():
    hit, _t, _a, _lvl = rs._evaluate(_Rule(level=38.0), 117.28, 90.0, 38.0, SOXL_VALUE)
    assert hit is False


def test_a_real_breach_still_fires():
    """SOXL at 45% against a 38% cap is 1,290 shekels of trim. That is a breach."""
    w, value = 45.0, 9800.0
    assert rs.cap_trim_ils(38.0, w, value) > MIN_TRADE_ILS
    hit, title, action, _lvl = rs._evaluate(_Rule(level=38.0), 117.28, 90.0, w, value)
    assert hit is True
    assert "45% of your portfolio" in title
    assert "38% cap" in action


def test_a_caller_that_does_not_know_the_value_keeps_the_old_behaviour():
    """Silencing a rule on missing data would be a worse failure than a nag."""
    hit, _t, _a, _lvl = rs._evaluate(_Rule(level=38.0), 117.28, 90.0, SOXL_W, 0.0)
    assert hit is True


# --------------------------------------------------------------------------- #
# 3. and it can clear
# --------------------------------------------------------------------------- #
def test_an_immaterial_breach_re_arms_instead_of_latching():
    """The band alone left no way out.

    `rearm_threshold(38.0)` is 37.05, so SOXL at 38.002% would have had to fall
    2.5% before the card went away — a nag the user cannot clear by doing
    anything, which is the exact phrase the re-arm fix used about the last one.
    """
    r = _Rule(level=38.0)
    assert rs.rearm_threshold(38.0) == pytest.approx(37.05, abs=0.01)
    assert rs.clears_with_margin(r, 117.28, SOXL_W, 0.0) is False, (
        "the fixture no longer reproduces the latch")
    assert rs.clears_with_margin(r, 117.28, SOXL_W, SOXL_VALUE) is True


def test_a_real_breach_does_not_re_arm_on_noise():
    """The band still does its job where the band is the right tool."""
    r = _Rule(level=38.0)
    assert rs.clears_with_margin(r, 117.28, 45.0, 9800.0) is False
    assert rs.clears_with_margin(r, 117.28, 37.0, 9800.0) is True


# --------------------------------------------------------------------------- #
# 4. one answer, not three
# --------------------------------------------------------------------------- #
def test_the_firing_test_and_the_execution_plan_agree():
    """They disagreed, and the disagreement is what reached the screen.

    `execution_plan` has applied the MIN_TRADE_ILS test since the MSFT case, but
    one layer too late: it could only strip the card's apply spec, never stop the
    card. A rule that fires must now have a trade behind it, so a fired
    `max_weight` card is actionable.
    """
    r = _Rule(level=38.0)
    small = {"qty": 70.8, "weight_pct": SOXL_W, "value_ils": SOXL_VALUE,
             "price": 117.28, "cost": 90.0}
    assert rs._evaluate(r, 117.28, 90.0, SOXL_W, SOXL_VALUE)[0] is False
    assert rs.execution_plan(r, small) is None       # agreed, and now unreachable

    big = {"qty": 83.6, "weight_pct": 45.0, "value_ils": 9800.0,
           "price": 117.28, "cost": 90.0}
    assert rs._evaluate(r, 117.28, 90.0, 45.0, 9800.0)[0] is True
    plan = rs.execution_plan(r, big)
    assert plan is not None and plan["kind"] == "trim", plan
    assert plan["ticker"] == "SOXL" and plan["shares"] > 0


# --------------------------------------------------------------------------- #
# 5. and when it DOES fire, it answers all three questions
#
# Eran, on the two cards: "1. it doesn't tell me what to do (how much to sell)
# 2. it doesn't tell me what do do instead (buy something else or keep it in
# cash) 3. it doesn't do the action for me -- it's only a guidance".
#
# (1) and (3) were already right for a MATERIAL breach and he never saw one --
# his was 44 agorot. (2) was missing, and (1) was in the wrong currency.
# --------------------------------------------------------------------------- #
def test_the_amount_is_in_shekels_net_of_tax_not_native_currency():
    """`shares * price` was the position's NATIVE currency, unlabelled.

    On SOXL at $117.28 a six-share trim read "~737" on a screen where every other
    number is shekels, while Accept credits about 2,700. Same defect as
    `_rebalance_to`'s "sell 4,324" against a 12,139 execution, one order of
    magnitude quieter.
    """
    import inspect

    src = inspect.getsource(rs.triggered_rule_recs)
    assert "_sale_value_ils" in src, (
        "the card values its own trim again instead of asking the function "
        "Accept runs")
    assert 'plan["shares"] * price' not in src, "the native-currency amount is back"


def test_the_card_says_where_the_money_lands():
    """Cash, at what percentage, and whether that is above or below the target."""
    above = rs._cash_landing(2900.0, 21859.69, 0.075)
    assert "13.3%" in above and "7.5% target" in above
    assert "cash to work" in above, above

    below = rs._cash_landing(1000.0, 21859.69, 0.075)
    assert "4.6%" in below and "dry powder" in below, below

    # No target set, and no book: neither invents a sentence it cannot support.
    assert "of the book" in rs._cash_landing(1000.0, 21859.69, 0.0)
    assert "until you decide" in rs._cash_landing(1000.0, 0.0, 0.075)


def test_a_material_cap_breach_is_actionable_not_guidance():
    """The `trim` kind is executable; the guidance fallback is for no-plan cases."""
    from app.services.recommendations import _ACTIONABLE_KINDS

    assert "trim" in _ACTIONABLE_KINDS
    r = _Rule(level=38.0)
    big = {"qty": 83.6, "weight_pct": 45.0, "value_ils": 9800.0,
           "price": 117.28, "cost": 90.0, "market": "NASDAQ", "meta": {}}
    plan = rs.execution_plan(r, big)
    assert plan is not None and plan["kind"] in _ACTIONABLE_KINDS
