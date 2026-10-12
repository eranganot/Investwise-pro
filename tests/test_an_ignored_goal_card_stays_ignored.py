"""PR 122 (owner, 2026-10-12): "this recommendation card keep coming back to me
after I click on the ignore button (it should disappear for one week)".

The goal cards' ids carried the stored solve's row id (`rec_goal_target_{row.id}`).
Every re-solve -- nightly, a settings save, a refresh -- writes a new row, so the
same card came back under a new id and the mute (keyed by id) no longer matched.
A card's identity is what it asks, not which solve computed it.
"""
from fastapi.testclient import TestClient

from app.main import app

from tests.test_frontier_goal import _cards, _goal_verdict, _reset_plan, _seed
from tests.test_frontier_resize import _store_verdict


def _target_card(c):
    return next((x for x in _cards(c) if (x.get("meta") or {}).get("lever") == "target"), None)


async def test_an_ignored_target_card_stays_ignored_after_a_re_solve(own_session):
    try:
        with TestClient(app) as c:
            c.post("/api/v1/recommendations/restore")
            cur = _seed(c, 10)
            await _store_verdict(own_session, _goal_verdict(cur))
            card = _target_card(c)
            assert card is not None, [x["title"] for x in _cards(c)]
            r = c.post(f"/api/v1/recommendations/{card['id']}/dismiss")
            assert r.status_code == 200, r.text
            assert _target_card(c) is None                         # gone now
            await _store_verdict(own_session, _goal_verdict(cur))   # the next solve: a new row
            again = _target_card(c)
            assert again is None, f"came back as {again['id']} after a re-solve"
            c.post("/api/v1/recommendations/restore")
    finally:
        await _reset_plan(own_session)


def test_no_card_id_carries_a_solve_row_id():
    src = open("app/services/frontier_service.py", encoding="utf-8").read()
    assert '{row.id}"' not in src, "a card id keyed to the solve row churns on every re-solve"
