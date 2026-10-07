"""PR 104c.1 (Eran: "it takes very long time for the goals, holdings prices, and
cards recommendations to upload").

Measured on production: every endpoint 0.2-1 s, the card build 3.5 s of which the
war room was 2.8 s -- and Today fetched its six blocks one after another, so the
goal and the projection waited behind the cards.
"""
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

from app.services import recommendations as rec

HTML = (Path(__file__).resolve().parents[1] / "app" / "static_app" / "index.html").read_text(encoding="utf-8")


def test_today_starts_every_request_before_awaiting_any():
    body = HTML[HTML.index("async function loadToday(){"):HTML.index("// PR 70: cards past the first 12")]
    for ep in ("/api/v1/rules", "/api/v1/health-check", "/api/v1/recommendations", "/api/v1/plan",
               "/api/v1/plan/projection", "/api/v1/risk/alerts"):
        assert f'_J("{ep}")' in body, ep
    assert not re.search(r"await\(await fetch\(", body), "a block still fetches on its own, after the one before it"


def test_the_war_room_is_not_recomputed_for_the_same_book(monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "war_room_cache_ttl_sec", 600.0)   # production's
    calls = []

    def compute(grounded):
        async def _c(session, user, rows, narrate=False):
            calls.append(1)
            return {"grounded": grounded}
        return _c
    rec._WAR_ROOM_CACHE.clear()
    user = SimpleNamespace(email="cache@test")
    rows = [SimpleNamespace(ticker="AAA", quantity=1.0, current_price=10.0)]
    run = lambda c: asyncio.run(rec._war_room_payload_cached(None, user, rows, c))  # noqa: E731
    run(compute(False))
    run(compute(False))
    assert len(calls) == 2, "an ungrounded answer must not be reused"
    calls.clear()
    run(compute(True))
    run(compute(True))
    assert len(calls) == 1, "the same book within 10 minutes recomputed the war room"
    rows[0].current_price = 11.0                      # a new price is a new answer
    run(compute(True))
    assert len(calls) == 2
    rec._WAR_ROOM_CACHE.clear()
