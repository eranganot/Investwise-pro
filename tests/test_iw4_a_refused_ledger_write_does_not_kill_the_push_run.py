"""IW-4 / IW-B1 (2026-11-05): one refused ledger write must not take the rest of
the push run with it.

On Postgres a failed INSERT aborts the transaction. The card loop's `except`
logged it and carried on with that session, so the heads-up, risk and price
pushes after it failed, and the final commit raised: the marks of cards already
pushed were lost and they pushed again next run. SQLite does not abort, which is
why the SQLite suite never saw it; this test is meaningful on the Postgres job.

Runs `evaluate_and_notify` against the real database (ledger, KV, subscription
rows); only the card builder and the network sender are replaced.
"""
import types
import uuid

from sqlalchemy import select

from app.models.tables import NotifiedEvent, PushSubscription
from app.services import push_service as ps

SUBJECT = "iw4@example.test"


def _card(cid, title):
    return {"id": cid, "title": title, "severity": "MEDIUM", "action": "Open it"}


async def _run(monkeypatch, own_session, cards, poison=None):
    sent = []

    async def fake_build(session, user):
        return {"recommendations": cards,
                "headsups": [{"card": {"id": "hu-job-1", "title": "A job is failing",
                                        "meta": {"headsup_kind": "job_health"}}}]}

    async def send(session, subject, title, body, url=None, tag=None, category=None):
        sent.append(tag)
        return 1

    async def no_positions(*a, **k):
        return []

    import app.services.portfolio_analytics as pa
    import app.services.recommendations as R
    monkeypatch.setattr(R, "build_recommendations", fake_build)
    monkeypatch.setattr(ps, "send_to_subject", send)
    monkeypatch.setattr(pa, "load_positions", no_positions)
    monkeypatch.setattr(ps, "load_positions", no_positions, raising=False)
    if poison:
        real = ps._ledger_key
        # A value the column refuses (String(160)) for one signature only.
        monkeypatch.setattr(ps, "_ledger_key", lambda s: "x" * 400 if s == poison else real(s))

    async with own_session() as s:
        s.add(PushSubscription(subject=SUBJECT, endpoint=f"https://push.example/{uuid.uuid4()}",
                               p256dh="k", auth="a"))
        await ps._kv_set(s, ps._baseline_key(SUBJECT), "2026-11-05")
        await s.commit()
        result = await ps.evaluate_and_notify(s, types.SimpleNamespace(email=SUBJECT))
    async with own_session() as s:
        marks = set((await s.scalars(select(NotifiedEvent.signature)
                                     .where(NotifiedEvent.subject == SUBJECT))).all())
    return result, sent, marks


async def test_a_refused_mark_leaves_the_rest_of_the_run_working(monkeypatch, own_session):
    cards = [_card("a1", "First"), _card("b2", "Second"), _card("c3", "Third")]
    result, sent, marks = await _run(monkeypatch, own_session, cards, poison="rec:b2")
    # Every card and the heads-up went out, and the run committed.
    assert {"rec:a1", "rec:b2", "rec:c3", "trigger:app_health:hu-job-1"} <= set(sent), sent
    assert result["sent"] >= 4
    # The marks that could be written were kept (they would push again otherwise);
    # only the refused one is missing.
    assert {"rec:a1", "rec:c3", "trigger:app_health:hu-job-1"} <= marks, marks
    assert "rec:b2" not in marks


async def test_an_overlong_card_id_is_marked_and_not_pushed_twice(monkeypatch, own_session):
    long_id = "drift_" + "x" * 300
    cards = [_card(long_id, "Long id")]
    _r, sent1, marks = await _run(monkeypatch, own_session, cards)
    assert f"rec:{long_id}" in sent1
    assert any(len(m) <= 160 and m.startswith("rec:drift_") for m in marks), marks
    _r, sent2, _m = await _run(monkeypatch, own_session, cards)
    assert f"rec:{long_id}" not in sent2, "the long-id card pushed again"


def test_ledger_key_is_bounded_stable_and_distinct():
    a, b = "rec:" + "y" * 500, "rec:" + "y" * 499 + "z"
    assert len(ps._ledger_key(a)) <= 160
    assert ps._ledger_key(a) == ps._ledger_key(a)
    assert ps._ledger_key(a) != ps._ledger_key(b)
    assert ps._ledger_key("rec:short") == "rec:short"
    assert "\x00" not in ps._ledger_key("rec:a\x00b")
