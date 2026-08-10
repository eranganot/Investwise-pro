"""P0.3 / P0.4 — the two things Today was silent about.

Today is "what to do now". A holding whose price has frozen, and a protective
rule that has been suggested but never armed, are both exactly that — and
neither could reach the screen.

Cards are matched by title, not by id: ids are content hashes (``_rid``), so
asserting on one would be asserting on a hash.
"""
import asyncio

from fastapi.testclient import TestClient

from app.main import app

BOOK = {"entity_name": "Personal", "positions": [
    {"ticker": "MSFT", "market": "NASDAQ", "depth": 2, "spot_price": 100, "listing_price": 108,
     "quantity": 50, "cost_basis": 80, "expected_return_pct": 9, "volatility_pct": 18},
    {"ticker": "COW", "market": "NYSE", "depth": 2, "spot_price": 40, "listing_price": 40,
     "quantity": 30, "cost_basis": 40, "expected_return_pct": 4, "volatility_pct": 12}]}


def _cards(client) -> dict:
    return client.get("/api/v1/recommendations").json()


def _matching(body: dict, needle: str) -> list[dict]:
    return [r for r in body.get("recommendations", []) if needle in r.get("title", "")]


def _mark_cow_stale() -> None:
    """Stamp price_stale the way the refresh job does — without a price provider."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings
    from app.models.tables import Position

    async def _go():
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                row = (await s.execute(
                    select(Position).where(Position.ticker == "COW"))).scalars().first()
                assert row is not None, "COW should exist before marking it stale"
                row.meta = {**(row.meta or {}), "price_stale": True,
                            "price_freshness": "stale",
                            "price_as_of": "2025-06-11T20:00:00+00:00",
                            "price_stale_days": 300}
                await s.commit()
        finally:
            await eng.dispose()

    asyncio.run(_go())


def test_suggested_rules_reach_today_as_one_card():
    """They existed only behind GET /rules/suggestions, which feeds a panel on a
    settings-shaped page — findable only by someone already looking for it."""
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=BOOK)
        body = _cards(c)
        found = _matching(body, "ready to arm")
        assert found, f"no suggestions card; degraded={body.get('degraded')}"
        assert len(found) == 1, "one card for the set, never one card per rule"
        card = found[0]
        assert card["apply"]["kind"] == "create_rules"
        assert card["apply"]["rules"], "the card must carry armable specs"
        for spec in card["apply"]["rules"]:
            assert spec["ticker"] and spec["rule_type"]
            assert spec["level"] is not None


def test_arming_from_today_creates_real_rules_and_the_card_stops_coming_back():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=BOOK)
        card = _matching(_cards(c), "ready to arm")[0]
        wanted = len(card["apply"]["rules"])

        r = c.post(f"/api/v1/recommendations/{card['id']}/accept")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["applied"] == "create_rules"
        assert len(body["rules_created"]) == wanted

        # They are real, armed rules — not a card that said it did something.
        rules = c.get("/api/v1/rules").json()
        armed = rules if isinstance(rules, list) else rules.get("rules", [])
        assert len(armed) >= wanted

        # And it does not nag about work already done: even after un-completing
        # every card, the suggester omits rule types that are already armed.
        c.post("/api/v1/recommendations/restore-completed")
        assert not _matching(_cards(c), "ready to arm")


def test_a_frozen_price_surfaces_as_guidance_not_a_write_off():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=BOOK)
        _mark_cow_stale()

        body = _cards(c)
        found = _matching(body, "has not traded since")
        assert found, f"no stale-price card; degraded={body.get('degraded')}"
        card = found[0]
        assert "COW" in card["title"] and "2025-06-11" in card["title"]
        # Guidance, never an automatic write-off: delisted, merged and renamed
        # are indistinguishable from here, and none of them is the app's call.
        assert (card.get("apply") or {}).get("kind") in (None, "none")
        assert card["why"] and card["impact"] and card["how"]


def test_nav_says_which_part_of_itself_it_does_not_trust():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=BOOK)
        _mark_cow_stale()
        pf = c.get("/api/v1/portfolio").json()

        assert [x["ticker"] for x in pf["stale_positions"]] == ["COW"]
        assert pf["stale_value_ils"] > 0
        # Still counted — writing a holding down to zero is not the app's call.
        assert pf["nav_ils"] > pf["stale_value_ils"]
        cow = next(p for p in pf["positions"] if p["ticker"] == "COW")
        assert cow["price_stale"] is True
        assert cow["price_as_of"].startswith("2025-06-11")
