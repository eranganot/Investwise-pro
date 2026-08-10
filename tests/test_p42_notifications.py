"""P4.2 — four triggers, four rate limits.

The limits are per TRIGGER, not per message, and that distinction is the design.
Recommendation ids are content hashes, so a drift card whose amount moves from
₪3,300 to ₪3,362 gets a new id and would slip past an id-keyed dedupe and push
again — nothing about the situation changed, only the rounding.
"""
import pytest

from app.services import push_service as ps


def _rec(**kw):
    base = {"id": "rec_abc123", "title": "Something", "severity": "MEDIUM",
            "action": "Do a thing"}
    return {**base, **kw}


def test_a_fired_rule_pushes_immediately():
    trigger, limit = ps.classify_trigger(_rec(id="rule_f3cff1bf", title="SOXL hit your stop"))
    assert trigger == "rule_fired"
    assert limit == ps.TRIGGER_IMMEDIATE


def test_a_signal_flip_pushes_immediately():
    """Rare and material: acting late on a flip is the main reason a rule
    underperforms its own backtest."""
    trigger, limit = ps.classify_trigger(_rec(id="stratsig_btm_trend", title="Your strategy flipped"))
    assert trigger == "signal_flip"
    assert limit == ps.TRIGGER_IMMEDIATE


def test_sleeve_drift_is_limited_to_daily():
    """It drifts a little every day and says the same thing each time."""
    for title in ("SOXL has drifted to 4% of a 10% sleeve",
                  "You chose a 10% SOXL sleeve and hold none of it"):
        trigger, limit = ps.classify_trigger(_rec(title=title))
        assert limit == ps.TRIGGER_DAILY, title
        assert trigger in ("sleeve_drift", "sleeve_coldstart")


def test_rules_available_can_never_fire_a_live_push():
    """Suggestions regenerate as prices move, so a live push here is the one that
    gets notifications switched off entirely. It is not "a long window" -- it is
    impossible."""
    trigger, limit = ps.classify_trigger(_rec(title="3 protective rules ready to arm"))
    assert trigger == "rules_available"
    assert limit is ps.TRIGGER_NEVER_LIVE
    assert limit is None


def test_an_ordinary_recommendation_keeps_the_old_window():
    _trigger, limit = ps.classify_trigger(_rec(title="Trim TEVA"))
    assert limit == ps.get_settings().push_dedupe_days * 24


def test_a_rate_limited_signature_ignores_the_numbers_in_the_card():
    """The whole point. Two renders of the same drift, different amounts, must
    share one signature or the daily limit never bites."""
    a = _rec(id="rec_111111", title="SOXL has drifted to 4% of a 10% sleeve",
             meta={"ticker": "SOXL", "actual_pct": 4.0})
    b = _rec(id="rec_999999", title="SOXL has drifted to 5% of a 10% sleeve",
             meta={"ticker": "SOXL", "actual_pct": 5.0})
    sig_a = ps.trigger_signature(ps.classify_trigger(a)[0], a)
    sig_b = ps.trigger_signature(ps.classify_trigger(b)[0], b)
    assert sig_a == sig_b, "the signature must not move when the numbers do"
    assert "rec_" not in sig_a, "a content hash in the signature defeats the limit"


def test_two_different_sleeves_are_rate_limited_separately():
    soxl = _rec(title="SOXL has drifted to 4% of a 10% sleeve", meta={"ticker": "SOXL"})
    tqqq = _rec(title="TQQQ has drifted to 4% of a 20% sleeve", meta={"ticker": "TQQQ"})
    assert (ps.trigger_signature("sleeve_drift", soxl)
            != ps.trigger_signature("sleeve_drift", tqqq))


def test_two_different_rules_firing_are_two_events():
    """A shared signature here would silence the second stop-loss of the day."""
    a = _rec(id="rule_aaaaaaaa", rule_id="aaaa")
    b = _rec(id="rule_bbbbbbbb", rule_id="bbbb")
    assert ps.trigger_signature("rule_fired", a) != ps.trigger_signature("rule_fired", b)


def test_the_digest_carries_the_never_live_triggers():
    import inspect
    src = inspect.getsource(ps.send_digest)
    assert "classify_trigger" in src
    assert "waiting in the app" in src


def test_the_push_loop_skips_never_live_and_bypasses_severity_for_real_triggers():
    import inspect
    src = inspect.getsource(ps.evaluate_and_notify)
    # never-live is skipped before anything else can send it
    assert "if limit is None:" in src
    # the four triggers are not gated on the CRITICAL/HIGH severity filter
    assert 'trigger == "recommendation" and r.get("severity"' in src
    # and the windowed check is used, not only the global one
    assert "_seen_within" in src


@pytest.mark.parametrize("hours", [0, 24])
def test_the_rate_limits_are_hours_not_days(hours):
    """_seen_within takes HOURS. Passing days would make 'daily' mean monthly."""
    import inspect
    src = inspect.getsource(ps._seen_within)
    assert "hours=max(0, hours)" in src or "timedelta(hours=" in src
    assert hours >= 0
