"""2026-10-22 -- "it doesn't matter if I click Accept or Ignore, this card always
comes back to Today."

Read live from production. The TQQQ sleeve card (rec_2e52d5):

    10-20 04:01  Accept -> sleeve_fund Buy 1,423.23 ILS      (done mark: 1,423)
    10-21        Ignore x4 (13:55, 13:57, 13:57, 14:13)       (ignore marks)
    10-22 09:01  card on Today, banner: "asked for about 1,423 ILS when you
                 filed it and asks for about 2,261 ILS now"

The banner quotes the amount of an ACCEPT from two days earlier, not of the
Ignore he had just pressed. `_marks = {**ignored, **done}` made the done mark win
for the same id however old it was, `moved_on` fired on it, and the card was
un-muted out of both sets. Every Ignore lost to a stale Accept.

The newest action on a card is the user's current statement about it.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


def _resolve(dis, done):
    # Lazy, so the file still COLLECTS against the previous commit and the tests
    # fail there for the right reason instead of never running.
    from app.services.recommendations import resolve_marks
    return resolve_marks(dis, done)


def _moved_on(entry, card, done):
    from app.services.recommendations import moved_on
    return moved_on(entry, card, done=done)


DONE_1020 = {"at": "2026-10-20T04:01:10+00:00", "amount": 1423.0, "severity": "MEDIUM"}
IGNORE_1021 = {"at": "2026-10-21T14:13:30+00:00", "amount": 2261.0, "severity": "MEDIUM"}
CARD_NOW = {"id": "rec_2e52d5", "severity": "MEDIUM", "est_amount": 2260.92}


def test_a_newer_ignore_beats_an_older_done():
    marks, done_wins = _resolve({"rec_2e52d5": IGNORE_1021}, {"rec_2e52d5": DONE_1020})
    assert marks["rec_2e52d5"] == IGNORE_1021, "the older Accept overruled the newer Ignore"
    assert "rec_2e52d5" not in done_wins


def test_the_live_case_stays_hidden_after_the_ignore():
    """Same numbers as production: ignored at 2,261, card still asks 2,261."""
    marks, done_wins = _resolve({"rec_2e52d5": IGNORE_1021}, {"rec_2e52d5": DONE_1020})
    why = _moved_on(marks["rec_2e52d5"], CARD_NOW, done="rec_2e52d5" in done_wins)
    assert why is None, f"the card came back although nothing moved since the Ignore: {why}"


def test_the_old_behaviour_is_what_the_banner_showed():
    """Pins the defect: compared against the stale done mark, it fires."""
    why = _moved_on(DONE_1020, CARD_NOW, done=True)
    assert why and "1,423" in why and "2,261" in why


def test_a_newer_done_still_beats_an_older_ignore():
    newer_done = {"at": "2026-10-22T06:01:00+00:00", "amount": 2261.0, "severity": "MEDIUM"}
    old_ignore = {"at": "2026-10-15T10:00:00+00:00", "amount": 900.0, "severity": "MEDIUM"}
    marks, done_wins = _resolve({"x": old_ignore}, {"x": newer_done})
    assert marks["x"] == newer_done and "x" in done_wins


def test_a_tie_goes_to_done():
    at = "2026-10-22T06:01:00+00:00"
    marks, done_wins = _resolve({"x": {"at": at, "amount": 1.0}}, {"x": {"at": at, "amount": 2.0}})
    assert marks["x"]["amount"] == 2.0 and "x" in done_wins


def test_ids_in_only_one_map_are_untouched():
    marks, done_wins = _resolve({"a": {"at": "2026-10-01T00:00:00+00:00"}},
                                {"b": {"at": "2026-10-02T00:00:00+00:00"}})
    assert set(marks) == {"a", "b"}
    assert done_wins == {"b"}


def test_an_ignore_is_still_not_judged_by_the_books_contradiction():
    """PR 38's 'the book still shows the ask, so the done was a lie' is for DONE
    marks only. An Ignore a week old with the same ask must stay ignored."""
    old_ignore = {"at": "2026-10-10T10:00:00+00:00", "amount": 2261.0, "severity": "MEDIUM"}
    marks, done_wins = _resolve({"rec_2e52d5": old_ignore}, {})
    assert _moved_on(marks["rec_2e52d5"], CARD_NOW, done="rec_2e52d5" in done_wins) is None


# --------------------------------------------------------------------------- #
# through the API -- the live sequence: an old Accept, then a fresh Ignore
# --------------------------------------------------------------------------- #
def _write_done_mark(rid, amount, severity, at):
    """Plant a DONE mark exactly as an Accept left it, with a chosen timestamp."""
    import asyncio
    import json

    from app.core.database import AsyncSessionLocal
    from app.models.tables import KVSetting
    from app.services.recommendations import _done_key
    from app.services.feed_service import ensure_superadmin

    async def _go():
        async with AsyncSessionLocal() as s:
            user = await ensure_superadmin(s)
            row = await s.get(KVSetting, _done_key(user))
            m = json.loads(row.value) if row and row.value else {}
            m[rid] = {"at": at, "amount": amount, "severity": severity}
            if row:
                row.value = json.dumps(m)
            else:
                s.add(KVSetting(key=_done_key(user), value=json.dumps(m)))
            await s.commit()

    asyncio.run(_go())


def test_an_ignore_pressed_after_an_old_accept_keeps_the_card_hidden():
    """Route -> store -> filter -> payload.

    An Accept two days ago at a tenth of today's ask, then an Ignore NOW at
    today's ask. Nothing has moved since the Ignore, so the card must stay
    hidden. Against the previous commit the old done mark wins, `moved_on` fires
    on its stale amount, and the card is back with a banner quoting the Accept.
    """
    import logging
    from datetime import datetime, timedelta, timezone

    import pytest
    from fastapi.testclient import TestClient

    import app.main as m
    from tests.test_a_mute_does_not_outlive_its_number import _clean

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _clean(c)
            recs = c.get("/api/v1/recommendations").json()["recommendations"]
            # A card whose id SURVIVES a second build: some ids in this fixture
            # change between two builds, and a victim that vanishes for that
            # reason would make the assertion below pass without testing anything.
            again = {r["id"] for r in c.get("/api/v1/recommendations").json()["recommendations"]}
            victim = next((r for r in recs if r["id"] in again and r.get("est_amount")
                           and float(r["est_amount"]) >= 500
                           and (r.get("apply") or {}).get("kind") != "none"), None)
            if victim is None:
                pytest.skip("this book produced no stable card asking for real money")
            # Guard against a green that proves nothing: with the old done mark
            # alone (a tenth of today's ask) the card must be VISIBLE, because the
            # number moved. If it is not, the planted mark never reached the app.
            two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            _write_done_mark(victim["id"], float(victim["est_amount"]) / 10.0,
                             victim.get("severity"), two_days_ago)
            planted = c.get("/api/v1/recommendations").json()
            seen = next((x for x in planted["recommendations"] if x["id"] == victim["id"]), None)
            assert seen is not None and seen.get("refired_because"), (
                "the planted done mark did not reach the app, so this test would "
                "prove nothing")

            r = c.post(f"/api/v1/recommendations/{victim['id']}/dismiss",
                       json={"amount": float(victim["est_amount"]),
                             "severity": victim.get("severity")})
            assert r.status_code == 200, r.text

            after = c.get("/api/v1/recommendations").json()
            back = next((x for x in after["recommendations"] if x["id"] == victim["id"]), None)
            assert back is None, (
                f"'{victim['title']}' was ignored at {victim['est_amount']} and came "
                f"straight back: {back.get('refired_because') if back else ''}")
    finally:
        logging.disable(logging.NOTSET)
        with TestClient(m.app) as c:
            c.post("/api/v1/recommendations/restore")
            c.post("/api/v1/recommendations/restore-completed")
