"""PR 34 -- nine cards were hidden, and two of them were the ones he needed.

Read live from production on 2026-09-02, while Eran was asking why the app had
nothing to say about a book that was off target:

    /api/v1/mix              rebalance_required: true
                             BUY  Fixed Income  2,243.96
                             SELL Equities      1,626.87
    /api/v1/recommendations  4 cards, all screener verdicts
                             dismissed_count: 4   completed_count: 5

The rebalance pair was inside those nine. A card's id is
`sha1(category|ticker)` -- no amount, no date -- so the card you file today and
the card the same condition generates next week are THE SAME id, and one click
mutes both for a fortnight however far the number travels in between.
`_DONE_TTL_DAYS` was already cut from 90 to 14 for this reason, and the comment
that did it says so: *"making the id carry its own content is the actual fix and
is a separate change."* This is that change, and it does not touch the id.

WHY NOT A CONTENT-BEARING ID. The id is what Restore, the audit log and the push
dedupe all key on, and quantising an amount into it churns on every boundary --
1,499 and 1,501 become different cards, and a card that re-fires on a 0.1% move
is as useless as one that never re-fires. So the MARK remembers the situation
instead, and the situation is compared. No boundary, no new identity, and the
comparison is legible in the reason string the card carries back.

The second half is smaller and was misdiagnosed at first. The "N suggestions are
hidden" line already existed and already rendered next to live cards -- so the
count was never missing. What was missing is WHICH: it said "4 other suggestions
are ignored" while the two rebalance cards sat inside that 4 and nothing on the
screen named them.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402


def moved_on(entry, card):
    """Imported lazily ON PURPOSE.

    A module-level import of a function this PR introduces makes the whole file
    fail to COLLECT against the previous commit -- so "these tests fail without
    the fix" would be true only in the sense that they never ran. The API tests
    below are the ones that prove behaviour, and they must be able to execute
    against the old code and fail there for the right reason.
    """
    from app.services.recommendations import moved_on as _impl
    return _impl(entry, card)


def _mark(amount=None, severity=None, at="2026-08-31T08:58:00+00:00"):
    e = {"at": at}
    if amount is not None:
        e["amount"] = amount
    if severity is not None:
        e["severity"] = severity
    return e


def _card(amount=None, severity="MEDIUM"):
    return {"id": "rec_abc123", "severity": severity, "est_amount": amount}


# --------------------------------------------------------------------------- #
# the number moved
# --------------------------------------------------------------------------- #
def test_the_rebalance_card_comes_back_when_its_ask_changes():
    """The live case. 1,627 of equities is not the same instruction as 260."""
    why = moved_on(_mark(amount=1626.87), _card(amount=260.0))
    assert why, "a 6x change in the ask left the card muted"
    assert "1,627" in why and "260" in why, f"the reason must name both: {why}"


def test_ordinary_drift_does_not_un_hide_what_you_just_filed():
    """A card that re-fires on noise is as useless as one that never does."""
    assert moved_on(_mark(amount=2000.0), _card(amount=2100.0)) is None   # +5%
    assert moved_on(_mark(amount=2000.0), _card(amount=1800.0)) is None   # -10%
    assert moved_on(_mark(amount=2000.0), _card(amount=2400.0)) is None   # +20%
    assert moved_on(_mark(amount=2000.0), _card(amount=2600.0)) is not None  # +30%


def test_a_small_ask_is_measured_against_a_floor_not_a_ratio():
    """40 ILS becoming 55 is a rounding difference, not a new situation.

    Without the floor a card asking for pocket change re-fires on every scan,
    which trains the user to ignore the whole list.
    """
    assert moved_on(_mark(amount=40.0), _card(amount=55.0)) is None
    assert moved_on(_mark(amount=40.0), _card(amount=300.0)) is not None


def test_the_direction_does_not_matter_only_the_size_of_the_move():
    assert moved_on(_mark(amount=1000.0), _card(amount=2000.0)) is not None
    assert moved_on(_mark(amount=2000.0), _card(amount=1000.0)) is not None


# --------------------------------------------------------------------------- #
# the severity moved
# --------------------------------------------------------------------------- #
def test_an_escalation_un_hides_the_card_whatever_the_amount():
    """"I dealt with that" was said about a MEDIUM. HIGH is new information."""
    why = moved_on(_mark(amount=2000.0, severity="MEDIUM"), _card(amount=2000.0, severity="HIGH"))
    assert why and "MEDIUM" in why and "HIGH" in why, why


def test_a_de_escalation_does_not():
    """Getting less urgent is not a reason to interrupt someone again."""
    assert moved_on(_mark(amount=2000.0, severity="HIGH"),
                    _card(amount=2000.0, severity="MEDIUM")) is None
    assert moved_on(_mark(amount=2000.0, severity="HIGH"),
                    _card(amount=2000.0, severity="HIGH")) is None


# --------------------------------------------------------------------------- #
# nothing silently changes meaning
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("entry", [
    None,
    "2026-08-31T08:58:00+00:00",          # the shape marks used to be stored in
    {"at": "2026-08-31T08:58:00+00:00"},  # a mark made before this PR
])
def test_a_mark_with_no_situation_behaves_exactly_as_it_did_before(entry):
    """No migration, and no mute quietly changing meaning under the user.

    An old mark cannot answer "is this the same card", so it keeps the only
    behaviour it ever had: muted until the TTL runs out.
    """
    assert moved_on(entry, _card(amount=99999.0, severity="CRITICAL")) is None


def test_a_card_with_no_amount_of_its_own_is_left_alone():
    """Most cards carry no est_amount. They must not become undismissable."""
    assert moved_on(_mark(amount=2000.0), _card(amount=None)) is None


def test_a_junk_amount_cannot_make_a_card_undismissable():
    """The amount arrives from the client, so it must fail SAFE.

    Safe here means "the card comes back", never "the card stays hidden" --
    the asymmetry that makes trusting the client bounded rather than load-bearing.
    """
    assert moved_on({"at": "x", "amount": "not a number"}, _card(amount=2000.0)) is None
    assert moved_on({"at": "x", "amount": None}, _card(amount=2000.0)) is None


# --------------------------------------------------------------------------- #
# end to end
# --------------------------------------------------------------------------- #
def test_the_store_records_the_situation_and_reads_both_shapes():
    """`_load_map` must normalise the two legacy shapes without losing anything."""
    import asyncio
    import json

    from app.models.tables import KVSetting
    from app.services import recommendations as R

    class _FakeSession:
        def __init__(self, value):
            self.row = KVSetting(key="k", value=value) if value is not None else None
            self.added = []

        async def get(self, _model, _key):
            return self.row

        def add(self, obj):
            self.added.append(obj)
            self.row = obj

        async def commit(self):
            return None

    async def _go():
        # legacy list of ids
        m = await R._load_map(_FakeSession(json.dumps(["rec_a", "rec_b"])), "k")
        assert set(m) == {"rec_a", "rec_b"}
        assert all(isinstance(v, dict) and v.get("at") for v in m.values())

        # legacy {id: iso}
        m = await R._load_map(_FakeSession(json.dumps({"rec_a": "2026-08-31T08:58:00+00:00"})), "k")
        assert m["rec_a"] == {"at": "2026-08-31T08:58:00+00:00"}

        # current shape survives a round trip through _mark
        sess = _FakeSession(json.dumps({}))
        await R._mark(sess, "k", "rec_a", 14, {"amount": 1626.87, "severity": "HIGH"})
        stored = json.loads(sess.row.value)["rec_a"]
        assert stored["amount"] == pytest.approx(1626.87)
        assert stored["severity"] == "HIGH"
        assert stored["at"]

    asyncio.run(_go())


# --------------------------------------------------------------------------- #
# through the API -- what the screen actually receives
# --------------------------------------------------------------------------- #
def _clean(c):
    """Seed the book and clear every mute this suite's other tests left behind.

    The whole run shares one sqlite file, so a dismissal made by an earlier test
    -- in this module or another -- is still in the KV store when this one
    starts. Without this the assertions here pass or fail on test ORDER, which
    is the same class of defect as a test that skips: a green that proves
    nothing about the code.
    """
    c.post("/api/v1/intake/portfolio", json=BOOK)
    c.post("/api/v1/recommendations/restore")
    c.post("/api/v1/recommendations/restore-completed")


BOOK = {"entity_name": "Personal", "positions": [
    {"ticker": "AAA", "market": "TASE", "asset_class": "Equities", "depth": 3,
     "spot_price": 100, "listing_price": 100, "quantity": 400, "cost_basis": 100,
     "expected_return_pct": 7, "volatility_pct": 14},
    {"ticker": "BBB", "market": "TASE", "asset_class": "Equities", "depth": 2,
     "spot_price": 90, "listing_price": 90, "quantity": 10, "cost_basis": 100,
     "expected_return_pct": 5, "volatility_pct": 10},
]}


def test_a_hidden_card_is_named_on_the_payload_not_merely_counted():
    """THE second half. The count was always there; the names were not.

    "4 other suggestions are ignored" was rendered next to four live cards while
    the two rebalance cards sat inside that 4. The user's question was "why is
    there no card about my mix", and a count cannot answer it.
    """
    import logging

    from fastapi.testclient import TestClient

    import app.main as m

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _clean(c)
            first = c.get("/api/v1/recommendations").json()
            assert "hidden_cards" in first, "the payload cannot name what it hides"
            assert first["hidden_cards"] == [], "nothing is hidden yet"
            recs = first["recommendations"]
            assert recs, "expected at least one card to hide"
            # A card that is in TWO consecutive builds. The buy-idea ranking
            # rests on live provider history, so which commodity ranks first
            # can change between two calls with nothing dismissed (measured
            # 2026-09-28: WEAT then DBC) -- and a victim that vanishes on its
            # own proves nothing about the mute.
            again = {x["id"] for x in c.get("/api/v1/recommendations").json()["recommendations"]}
            victim = next(x for x in recs if x["id"] in again)

            r = c.post(f"/api/v1/recommendations/{victim['id']}/dismiss",
                       json={"amount": victim.get("est_amount"),
                             "severity": victim.get("severity")})
            assert r.status_code == 200, r.text

            after = c.get("/api/v1/recommendations").json()
            assert all(x["id"] != victim["id"] for x in after["recommendations"]), (
                "the card was not hidden at all")
            named = {h["id"]: h for h in after["hidden_cards"]}
            assert victim["id"] in named, (
                f"a hidden card is not named on the payload: {after['hidden_cards']}")
            entry = named[victim["id"]]
            assert entry["title"] == victim["title"]
            assert entry["how"] == "ignored"
            assert entry["hidden_at"], "a hidden card must say when it was hidden"
            assert after["dismissed_count"] >= 1
    finally:
        logging.disable(logging.NOTSET)


def test_dismissing_without_a_body_still_works():
    """Every existing caller posts no body. None of them may break.

    `push_service` and the older client both call this route bare, and a route
    that started requiring a body would silently stop muting anything.
    """
    import logging

    from fastapi.testclient import TestClient

    import app.main as m

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _clean(c)
            recs = c.get("/api/v1/recommendations").json()["recommendations"]
            assert recs
            r = c.post(f"/api/v1/recommendations/{recs[0]['id']}/dismiss")
            assert r.status_code == 200, r.text
            after = c.get("/api/v1/recommendations").json()
            assert all(x["id"] != recs[0]["id"] for x in after["recommendations"])
    finally:
        logging.disable(logging.NOTSET)


def test_a_card_whose_number_moved_comes_back_through_the_whole_path():
    """Route -> store -> filter -> payload, end to end.

    The mark is written with an amount a tenth of what the card is actually
    asking for. That is the live situation in miniature: the mute was made about
    one number and the card is now about a different one. It must be visible,
    and it must say why it is back.

    Against the previous commit this card stays hidden -- the mark carried no
    number, so nothing could notice the number had moved.
    """
    import logging

    from fastapi.testclient import TestClient

    import app.main as m

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _clean(c)
            recs = c.get("/api/v1/recommendations").json()["recommendations"]
            victim = next((r for r in recs if r.get("est_amount")), None)
            if victim is None:
                pytest.skip("this book produced no card carrying an amount")

            r = c.post(f"/api/v1/recommendations/{victim['id']}/dismiss",
                       json={"amount": float(victim["est_amount"]) / 10.0,
                             "severity": victim.get("severity")})
            assert r.status_code == 200, r.text

            after = c.get("/api/v1/recommendations").json()
            back = next((x for x in after["recommendations"] if x["id"] == victim["id"]), None)
            assert back is not None, (
                f"'{victim['title']}' asked for {victim['est_amount']} and was muted "
                f"against {float(victim['est_amount']) / 10:.0f} -- it stayed hidden")
            assert back.get("refired_because"), (
                "the card came back with no explanation, which reads as the app "
                "having forgotten you dealt with it")
            assert "ILS" in back["refired_because"]
            # ...and it is not double-counted as hidden while it is on screen
            assert all(h["id"] != victim["id"] for h in after["hidden_cards"])
    finally:
        logging.disable(logging.NOTSET)
