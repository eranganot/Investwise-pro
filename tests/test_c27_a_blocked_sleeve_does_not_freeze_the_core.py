"""C27 (owner, 2026-10-23: "Currently I have two triggered rules but no recommendation
on how to act"; decided: the money blocked sleeves can't use goes into the core).

Live on 332deff: the SOXL stop hold (PR 139, and since PR 141 every 2x/3x fund)
blocks the SOXL sleeve and the TQQQ part of the vol-target sleeve. `book_target`
gives that share to the core (37% -> 58%), which raised the core's QQQ and SMH
targets and fired both rebalance bands. But the core card reserved every
off-basket holding (GOOGL, XOM, XLE, BND, BIL, UGL: ~3,510) for "your sleeves are
6,728 short" -- a shortfall made entirely of purchases the stop hold refuses. So
neither card could act.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from tests._cards import all_cards  # noqa: E402
from tests.test_a_stop_out_is_not_bought_back import _accept_the_stop, _pos  # noqa: E402
from tests.test_pr141_one_bet_one_hold import _two_geared_sleeves_and_a_fired_soxl_stop  # noqa: E402


def _core_card(c):
    cards = all_cards(c.get("/api/v1/recommendations").json())
    return next((x for x in cards if (x.get("apply") or {}).get("kind") == "core_to_basket"), None)


def test_money_kept_for_a_stop_held_sleeve_goes_to_the_core():
    with TestClient(m.app) as c:
        _accept_the_stop(c, _two_geared_sleeves_and_a_fired_soxl_stop(c))
        # an off-basket holding the core would sell, as GOOGL on the live book
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("GOOGL", 15.0)]})
        assert c.post("/api/v1/plan/core", json={"strategy_id": "grow_diversified"}).status_code == 200
        card = _core_card(c)
        assert card is not None, "the core is off its basket; there must be a core card"
        how = " ".join(card.get("how") or [])
        assert "GOOGL" in how
        assert "NOT sold here" not in how, f"still reserved for a sleeve the stop hold blocks: {how}"
        pl = card["meta"]["plan"]
        assert pl["sleeve_short_ils"] > 0 and pl["sleeve_short_blocked_ils"] == pl["sleeve_short_ils"], pl


def test_a_sleeve_the_app_can_buy_still_has_first_claim():
    """Not changed: with no hold, an unfunded sleeve keeps first claim (PR 60)."""
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("VTI", 60.0), _pos("BND", 40.0, cls="Fixed Income"), _pos("GOOGL", 15.0)]})
        c.post("/api/v1/recommendations/restore")
        c.post("/api/v1/recommendations/restore-completed")
        assert c.post("/api/v1/strategies/btm_qqq_hold/apply?sleeve_pct=30").status_code == 200
        assert c.post("/api/v1/plan/core", json={"strategy_id": "grow_diversified"}).status_code == 200
        card = _core_card(c)
        if card is not None:
            how = " ".join(card.get("how") or [])
            assert "GOOGL" not in how or "NOT sold here" in how, how
