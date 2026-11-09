"""C34 -- every Today build stamps its cards; the summary counts cards no push named.

Production kept no record of which cards appeared, so "a card that lived under
the 30-minute push check was missed" could not be counted. These pin the writer
(first/last seen, count, retention), the summary (pushed vs never pushed,
short-lived) and the wiring (a real Today request writes; a simulated one does
not; the summary needs a login like every portfolio endpoint).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models.tables import CardSighting, NotifiedEvent
from app.services import card_sightings as cs
from app.services.push_service import _ledger_key, card_signature

SUBJ = "c34@example.com"
T0 = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)


def _card(cid, kind="buy_funded"):
    return {"id": cid, "title": f"card {cid}", "apply": {"kind": kind}}


@pytest.mark.asyncio
async def test_first_and_last_seen_and_the_count(own_session):
    async with own_session() as s:
        assert await cs.record(s, SUBJ, [_card("rec_a")], now=T0) == 1
        await cs.record(s, SUBJ, [_card("rec_a"), _card("rec_b")], now=T0 + timedelta(minutes=10))
        await s.commit()
        rows = {r.card_id: r for r in (await s.execute(
            select(CardSighting).where(CardSighting.subject == SUBJ))).scalars()}
    assert set(rows) == {"rec_a", "rec_b"}
    a = rows["rec_a"]
    assert a.seen_count == 2 and a.kind == "buy_funded"
    assert cs._aware(a.first_seen) == T0 and cs._aware(a.last_seen) == T0 + timedelta(minutes=10)


@pytest.mark.asyncio
async def test_old_rows_are_dropped_on_write(own_session):
    async with own_session() as s:
        await cs.record(s, SUBJ, [_card("rec_old")], now=T0)
        await cs.record(s, SUBJ, [_card("rec_new")], now=T0 + timedelta(days=cs.RETAIN_DAYS + 1))
        await s.commit()
        ids = set((await s.execute(select(CardSighting.card_id).where(
            CardSighting.subject == SUBJ))).scalars())
    assert ids == {"rec_new"}


@pytest.mark.asyncio
async def test_the_summary_separates_pushed_from_never_pushed(own_session):
    """rec_p was pushed; rec_s lived 5 minutes and no push named it -- the C34 case;
    rec_l lived 2 hours, not pushed (counted as never pushed, not short-lived)."""
    async with own_session() as s:
        await cs.record(s, SUBJ, [_card("rec_p"), _card("rec_s"), _card("rec_l")], now=T0)
        await cs.record(s, SUBJ, [_card("rec_p"), _card("rec_s")], now=T0 + timedelta(minutes=5))
        await cs.record(s, SUBJ, [_card("rec_l")], now=T0 + timedelta(hours=2))
        s.add(NotifiedEvent(subject=SUBJ, signature=_ledger_key(card_signature({"id": "rec_p"}))))
        await s.commit()
        out = await cs.summary(s, SUBJ, days=14, now=T0 + timedelta(hours=3))
    by = {c["card_id"]: c for c in out["cards"]}
    assert by["rec_p"]["pushed"] is True
    assert by["rec_s"]["pushed"] is False and by["rec_s"]["lived_min"] == 5.0
    assert out["never_pushed"] == 2
    assert out["never_pushed_short_lived"] == 1


@pytest.mark.asyncio
async def test_a_failed_write_does_not_raise(own_session):
    async with own_session() as s:
        assert await cs.record(s, "", [_card("rec_x")]) == 0
        assert await cs.record(s, SUBJ, [{"title": "no id"}]) == 0


BOOK = [{"ticker": "VTI", "market": "NYSE", "asset_class": "Equities", "depth": 2,
         "spot_price": 380.0, "listing_price": 380.0, "quantity": 40, "cost_basis": 300.0,
         "expected_return_pct": 8, "volatility_pct": 18}]


def test_a_today_request_stamps_its_cards_and_a_simulation_does_not():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": BOOK})
        c.put("/api/v1/plan", json={"objective": "Grow"})
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 9000.0})
        sim = c.get("/api/v1/recommendations?simulate_cash=5000")
        assert sim.status_code == 200
        before = c.get("/api/v1/cards/sightings").json()
        today = c.get("/api/v1/recommendations").json()
        after = c.get("/api/v1/cards/sightings").json()
    ids = {r["id"] for r in today["recommendations"] if r.get("id")}
    assert before["count"] == 0, "a simulated build must not stamp sightings"
    assert ids, "the fixture must put at least one card on Today"
    assert {x["card_id"] for x in after["cards"]} == ids
