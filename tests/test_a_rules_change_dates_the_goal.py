"""PR 104a.1: a goal verdict is about the rules it measured.

Eran pressed Accept on "Retune your protection": five stops were paused and the
guard moved to 15% -- and the Plan tab kept showing the verdict solved on the
old rules (0.7%, "cost your goal 28 pts", the same card), so it read as
"nothing happened". The verdict now records its rules; a change dates it, and
the retune's Accept re-solves at once.
"""
from fastapi.testclient import TestClient

from app.main import app
from app.services import frontier_service as fs
from tests.test_frontier_goal import _cards, _goal_verdict, _reset_plan, _seed
from tests.test_frontier_resize import _store_verdict


def test_rules_changed_since():
    assert fs.rules_changed_since({"rules_fp": "abc"}, "def") is True
    assert fs.rules_changed_since({"rules_fp": "abc"}, "abc") is False
    # a verdict from before the fingerprint is judged as before
    assert fs.rules_changed_since({}, "def") is False


def test_a_sleeve_is_named_by_what_it_holds():
    assert fs.sleeve_label("btm_trend_soxl") == "SOXL"
    assert fs.sleeve_label("not_a_strategy") == "not_a_strategy"


async def test_goal_cards_stand_down_when_the_rules_moved_and_return_when_they_match(own_session):
    from app.services.feed_service import ensure_superadmin
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            # Building Today retires rules on tickers no longer held (left by
            # earlier tests), which changes the fingerprint: settle that first,
            # so the one this test records is the one the build will see.
            _cards(c)
            async with own_session() as s:
                fp = await fs.rules_fingerprint(s, await ensure_superadmin(s))
            await _store_verdict(own_session, _goal_verdict(cur, rules_fp="not-" + fp))
            assert [x for x in _cards(c) if x.get("dimension") == "goal"] == []
            await _store_verdict(own_session, _goal_verdict(cur, rules_fp=fp))
            assert [x for x in _cards(c) if x.get("dimension") == "goal"]
    finally:
        await _reset_plan(own_session)
