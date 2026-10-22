"""2026-10-22 -- "it doesn't matter if I click Accept or Ignore, this card always
comes back to Today."

The second half (the first is `test_an_ignore_is_not_overruled_by_an_old_done`).
Read live from production: the TQQQ sleeve card offered "Add about 2,261 of TQQQ
to get back to your 28% sleeve" and every Accept was refused. A dry run of the
button's own plan returned

    ok: false
    error: funding this sleeve would move the book away from your plan
    reason: ... off target by 1.2 -> 9.6 points in total. It would have to sell
            Commodities (9.72% held, 10% target), Equities (88.98% held,
            89.29% target), which your plan says you hold too little of.

Eight Accepts in the audit log, no trade in the ledger, the card back each time.
`sleeve_coldstart` already priced the button's plan before offering Accept; the
under-target drift card did not, so it promised a trade the executor was always
going to refuse. Now it asks first, and a top-up that would be refused is a
Heads-up that says why, not a card with a button that does nothing (PR 102).
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402

SOXL = "btm_trend_soxl"

REFUSAL = {
    "ok": False, "mode": "fund",
    "error": "funding this sleeve would move the book away from your plan",
    "reason": ("Funding this would move the book further from your plan (off target "
               "by 1.2 -> 9.6 points in total). It would have to sell Commodities "
               "(9.72% held, 10% target), which your plan says you hold too little of."),
}
PREVIEW_OK = {"ok": True, "mode": "fund", "dry_run": True, "buys": [], "funding": {},
              "amount_ils": 4000.0}


def _pos(ticker, qty, price=100.0):
    return {"ticker": ticker, "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": 90.0, "expected_return_pct": 8, "volatility_pct": 20}


def _under_target_sleeve(c):
    """A SOXL sleeve that HOLDS some of itself (so it is drift, not cold start)
    and sits well under the size chosen."""
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("V", 100.0), _pos("SCHD", 100.0), _pos("SOXL", 10.0)]})
    c.post("/api/v1/recommendations/restore")
    c.post("/api/v1/recommendations/restore-completed")
    c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=30")


def _today(body):
    """Cards on Today proper. NOT `tests._cards.all_cards`, which also returns the
    cards behind heads-ups and would hide exactly the difference under test."""
    return (body.get("recommendations") or []) + (body.get("more_recommendations") or [])


def _drift(cards):
    return [r for r in cards if "has drifted to" in (r.get("title") or "")
            and (r.get("meta") or {}).get("ticker") == "SOXL"]


def _patch_preview(monkeypatch, result):
    import app.services.strategy_service as ss
    real = ss.load_basket

    async def fake(session, user, strategy_id, *a, **kw):
        if kw.get("dry_run") and kw.get("mode") == "fund":
            if isinstance(result, Exception):
                raise result
            return dict(result)
        return await real(session, user, strategy_id, *a, **kw)

    monkeypatch.setattr(ss, "load_basket", fake)


def test_a_top_up_the_funder_would_refuse_is_a_headsup_not_a_card(monkeypatch):
    _patch_preview(monkeypatch, REFUSAL)
    with TestClient(m.app) as c:
        _under_target_sleeve(c)
        r = c.get("/api/v1/recommendations").json()

    on_today = _drift(_today(r))
    assert not on_today, (
        f"a card still offers Accept on a top-up the funder refuses: "
        f"{[(x['title'], x.get('apply')) for x in on_today]}")

    heads = [h for h in (r.get("headsups") or [])
             if "has drifted to" in h["title"] and h.get("ticker") == "SOXL"]
    assert heads, "the refused top-up vanished instead of saying why"
    h = heads[0]
    assert "held back" in h["line"], f"the one line does not say it is held back: {h['line']!r}"
    assert not h.get("apply"), "a heads-up carries an act option it cannot honour"
    card = h["card"]
    assert (card.get("apply") or {}).get("kind") == "none"
    assert "off target by 1.2 -> 9.6" in card["why"], "the reason for the refusal is not on the card"
    assert card["meta"].get("held_back")
    # The number is kept, so the mute logic can still read it, but it is not a
    # trade anyone is being asked to make.
    assert card.get("est_amount")


def test_a_top_up_the_funder_would_do_is_still_a_card_with_an_accept(monkeypatch):
    _patch_preview(monkeypatch, PREVIEW_OK)
    with TestClient(m.app) as c:
        _under_target_sleeve(c)
        r = c.get("/api/v1/recommendations").json()

    cards = _drift(_today(r))
    assert cards, "a fundable top-up lost its card"
    assert cards[0]["apply"]["kind"] == "fund_sleeve"
    assert "held_back" not in (cards[0].get("meta") or {})


def test_a_failed_preview_never_costs_today_the_card(monkeypatch):
    _patch_preview(monkeypatch, RuntimeError("provider timed out"))
    with TestClient(m.app) as c:
        _under_target_sleeve(c)
        r = c.get("/api/v1/recommendations").json()

    cards = _drift(_today(r))
    assert cards, "a preview that raised removed the card"
    assert cards[0]["apply"]["kind"] == "fund_sleeve"
    assert "sleeve_drift" not in (r.get("degraded") or [])


def test_an_overweight_sleeve_is_not_previewed_as_a_top_up(monkeypatch):
    """Only the BUY direction goes through the funder. A trim is a trim."""
    calls = []
    import app.services.strategy_service as ss
    real = ss.load_basket

    async def spy(session, user, strategy_id, *a, **kw):
        if kw.get("dry_run") and kw.get("mode") == "fund":
            calls.append(strategy_id)
        return await real(session, user, strategy_id, *a, **kw)

    monkeypatch.setattr(ss, "load_basket", spy)
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("V", 10.0), _pos("SOXL", 300.0)]})
        c.post("/api/v1/recommendations/restore")
        c.post("/api/v1/recommendations/restore-completed")
        c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=10")
        r = c.get("/api/v1/recommendations").json()

    over = [x for x in _today(r) if "has drifted to" in (x.get("title") or "")
            and (x.get("meta") or {}).get("ticker") == "SOXL"]
    if not over:
        pytest.skip("this book produced no overweight drift card")
    assert over[0]["apply"]["kind"] in ("trim", "overweight_choice")
    assert not calls, f"an overweight card went through the funder preview: {calls}"
