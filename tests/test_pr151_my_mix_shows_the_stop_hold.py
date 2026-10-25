"""PR 151 -- My mix shows the core's target the cards use, and a stop-held sleeve as "on hold".

Owner, 2026-10-25 (screenshot of My mix after funding DBMF): the core row read
"49.2% / 26.2%" in warning colour and the sleeves "23.0 pts to fund". Since PR 149
the cards manage the core to 49.2% (`book_target.core_pct`): SOXL's 23% stop-held
share goes to the core (D127). My mix computed its own target in the page, as
100 - every sleeve target - cash, which knows nothing of the hold.

`/plan/sleeves` now carries `core_target_pct` (the book target's) and, per sleeve,
`on_stop_hold`; the page uses both.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402

SOXL = "btm_trend_soxl"


def _pos(ticker, qty, price=100.0, cls="Equities"):
    return {"ticker": ticker, "market": "NASDAQ", "asset_class": cls, "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": 90.0, "expected_return_pct": 8, "volatility_pct": 20}


def _stopped_book(c):
    from tests._cards import all_cards
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("VTI", 60.0), _pos("BND", 60.0, cls="Fixed Income"), _pos("SOXL", 20.0)]})
    c.post("/api/v1/recommendations/restore")
    c.post("/api/v1/recommendations/restore-completed")
    assert c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=20").status_code == 200
    assert c.post("/api/v1/rules", json={"ticker": "SOXL", "rule_type": "stop_loss",
                                         "mode": "price", "level": 110.0}).status_code == 200
    c.post("/api/v1/rules/evaluate")
    stop = [x for x in all_cards(c.get("/api/v1/recommendations").json())
            if (x.get("apply") or {}).get("kind") == "sell_position"
            and (x.get("apply") or {}).get("ticker") == "SOXL"]
    assert stop, "the fired stop must be a card"
    assert c.post(f"/api/v1/recommendations/{stop[0]['id']}/accept").status_code == 200


def test_the_sleeves_view_carries_the_core_target_the_cards_use():
    with TestClient(m.app) as c:
        _stopped_book(c)
        v = c.get("/api/v1/plan/sleeves").json()
        bt = c.get("/api/v1/recommendations").json()["book_target"]
        assert v["core_target_pct"] == pytest.approx(bt["core_pct"], abs=1e-4)
        # the planned remainder is still there, and the hold adds the sleeve's 20 points
        assert v["core_target_pct"] * 100 == pytest.approx(v["core_pct"] + 20.0, abs=0.5), v
        soxl = next(s for s in v["sleeves"] if s["strategy_id"] == SOXL)
        assert soxl["on_stop_hold"] == ["SOXL"]


def test_without_a_hold_the_core_target_is_the_planned_remainder():
    """NOT CHANGED: no hold, no difference, nothing marked on hold."""
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("VTI", 60.0), _pos("BND", 60.0, cls="Fixed Income")]})
        assert c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=20").status_code == 200
        v = c.get("/api/v1/plan/sleeves").json()
        assert v["core_target_pct"] * 100 + v["commodities_pct"] * 100 + v["cash_reserve_pct"] * 100 \
            == pytest.approx(v["core_pct"], abs=0.5), v
        assert all(s["on_stop_hold"] == [] for s in v["sleeves"])


def test_my_mix_reads_the_server_core_target_and_skips_held_sleeves():
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "static_app", "index.html"),
               encoding="utf-8").read()
    body = src[src.index("function mixPanel"):]
    body = body[:body.index("\nfunction ", 10)]
    assert "S.core_target_pct" in body
    assert "on_stop_hold" in body
    assert "sl.filter(x=>!x.hold)" in body, "a stop-held sleeve is not money to fund"
