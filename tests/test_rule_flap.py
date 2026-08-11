"""The MSFT notification that arrived every few hours with nothing to do.

Live: MSFT sat at 20.2% of the book against a 20% cap it had itself armed. The
push said "1 trading rule triggered: MSFT - tap to review" and there was nothing
to review, over and over.

Three faults, each of which alone would have been enough:

1. No hysteresis. The P4.2 fix cleared the latch the moment the condition went
   false, so a position resting on its own boundary went latch -> push -> clear
   -> re-latch -> push. The bug before it was a rule that never stopped
   nagging; the fix made a rule that nagged rhythmically. Both are one rule
   that will not shut up.
2. `evaluate_user` called `push_service.send_to_subject` directly, with no
   dedupe and no rate limit, while P4.2 built exactly that machinery for every
   other trigger. Two notification paths, one governed and one not.
3. It pushed a firing with no trade behind it. Backlog #7 already decided a
   0.2-point breach is a sub-minimum trim not worth proposing; the rule fired
   and pushed anyway.
"""
from types import SimpleNamespace as NS

import pytest

from app.services import push_service as ps
from app.services import rules_service as rs


def _cap(level=20.0):
    return NS(rule_type="max_weight", level=level, ticker="MSFT", triggered=True)


# --------------------------------------------------------------------------- #
# 1 - hysteresis
# --------------------------------------------------------------------------- #
def test_a_position_resting_on_its_cap_does_not_re_arm():
    """The live case. 20.2% breaches a 20% cap; 19.9% is under it but is the
    same position on the same day. Re-arming there is what produced a push
    every few hours."""
    assert not rs.clears_with_margin(_cap(), 510.88, 19.9)
    assert not rs.clears_with_margin(_cap(), 510.88, 19.6)


def test_a_real_move_back_inside_the_cap_does_re_arm():
    """The band must not become a latch. Once the weight has genuinely come
    down, the rule has to be able to fire again on the next real breach."""
    assert rs.clears_with_margin(_cap(), 510.88, 19.4)
    assert rs.clears_with_margin(_cap(), 510.88, 12.0)


def test_the_band_scales_with_the_level():
    """Half a point is right for a 20% cap and far too tight for a 500 price
    alert, so the band is 2.5% of the level with a half-point floor."""
    assert rs.rearm_threshold(20.0) == pytest.approx(19.5)     # floor bites
    assert rs.rearm_threshold(500.0) == pytest.approx(487.5)   # 2.5% bites
    assert rs.rearm_threshold(4.0) == pytest.approx(3.5)       # floor bites


def test_a_price_below_alert_re_arms_upwards():
    """The mirror image: a stop at 100 must not re-arm at 100.01."""
    stop = NS(rule_type="price_below", level=100.0, ticker="MSFT", triggered=True)
    assert not rs.clears_with_margin(stop, 100.5, 0)
    assert rs.clears_with_margin(stop, 103.0, 0)


def test_a_price_above_alert_re_arms_downwards():
    alert = NS(rule_type="price_above", level=100.0, ticker="MSFT", triggered=True)
    assert not rs.clears_with_margin(alert, 99.0, 0)
    assert rs.clears_with_margin(alert, 96.0, 0)


def test_one_shot_rule_types_are_unaffected():
    """Stops and take-profits do not re-arm at all; the band must not invent a
    path back for them."""
    for rt in ("trailing_stop", "stop_loss", "take_profit", "strategy_signal"):
        assert rs.clears_with_margin(NS(rule_type=rt, level=10.0), 1.0, 1.0)


# --------------------------------------------------------------------------- #
# 2 - one governed push path
# --------------------------------------------------------------------------- #
def test_the_rule_push_goes_through_the_p42_limiter():
    """Structural, because the failure mode is a SECOND implementation quietly
    appearing beside the governed one -- the same shape as the duplicated
    reprice loop that mispriced the cash row. What matters is not that a limit
    exists somewhere, but that this call site is the one that consults it."""
    import inspect
    src = inspect.getsource(rs.evaluate_user)
    push = src[src.index("if notify and newly:"):]
    assert "classify_trigger" in push
    assert "trigger_signature" in push
    assert "_seen_within" in push
    assert "_mark" in push
    assert 'tag=f"rule:' not in push, "the raw per-id tag bypassed the ledger"


def test_a_fired_rule_is_rate_limited_rather_than_unlimited():
    """TRIGGER_IMMEDIATE means no limit at all, which is how one cap breach
    reached the user every few hours. It is now a floor, not a free pass."""
    trigger, limit = ps.classify_trigger(
        {"id": "rule_f3cff1bf", "title": "MSFT is over your cap"})
    assert trigger == "rule_fired"
    assert limit == ps.TRIGGER_RULE_REPEAT
    assert 0 < limit <= 24, "urgent enough to matter, rare enough not to nag"


def test_two_different_rules_still_both_notify():
    """The floor is per rule, not per user: a stop hitting on one holding must
    not silence a cap breaching on another."""
    a = ps.trigger_signature("rule_fired", {"rule_id": "aaa"})
    b = ps.trigger_signature("rule_fired", {"rule_id": "bbb"})
    assert a != b


# --------------------------------------------------------------------------- #
# 3 - nothing pushed that has nothing to do
# --------------------------------------------------------------------------- #
def test_a_firing_with_no_executable_trade_is_not_pushed():
    """Backlog #7 already decided a 0.2-point breach is not worth proposing.
    The rule kept firing and pushing regardless, so the app declined to suggest
    the trade and woke the user about it in the same breath."""
    import inspect
    src = inspect.getsource(rs.evaluate_user)
    assert '"actionable": bool(_plan)' in src
    push = src[src.index("if notify and newly:"):]
    assert 'if not n.get("actionable", True):' in push
    assert "continue" in push


def test_the_msft_breach_is_exactly_the_unactionable_case():
    """Tie the suppression to the real numbers rather than to a claim about
    them: this is the position that was pushing."""
    pos = {"qty": 2.8243, "weight_pct": 20.2, "price": 510.88, "value_ils": 4329.0}
    assert rs.execution_plan(_cap(), pos) is None


def test_a_breach_worth_acting_on_is_still_pushed():
    """The counter-check. Suppressing the noise must not suppress the signal."""
    pos = {"qty": 60.0, "weight_pct": 40.0, "price": 100.0, "value_ils": 8000.0}
    assert rs.execution_plan(_cap(), pos) is not None


# --------------------------------------------------------------------------- #
# the coupling that caused it
# --------------------------------------------------------------------------- #
def test_rendering_a_card_does_not_re_arm_the_alert():
    """The card and the latch answer different questions, and one line wrote
    both. The card must vanish the instant the breach corrects -- the screen has
    to be honest about now -- but clearing the latch there re-armed the push, so
    every render of Today handed the notifier a fresh firing."""
    import inspect
    src = inspect.getsource(rs.triggered_rule_recs)
    block = src[src.index('if not hit and r.rule_type in ('):]
    block = block[:block.index("plan = execution_plan")]
    assert "clears_with_margin" in block, "the latch must clear only on a real move"
    # The card still goes immediately: the `continue` is outside the guard.
    guard = block.index("if clears_with_margin")
    assert block.index("continue") > guard
    assert block[guard:].index("r.triggered = False") < block[guard:].index("continue")
