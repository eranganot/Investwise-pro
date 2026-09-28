"""PR 75: research moves money between names, never past a cap or the floor."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import redeploy_research as rr  # noqa: E402
from tests._redeploy_fixtures import S, run  # noqa: E402


def test_a_swap_is_clipped_to_the_new_names_cap_and_the_rest_stays_cash():
    legs = [{"ticker": "QQQ", "amount_ils": 1000.0, "asset_class": "Equities", "reason": "gap"}]
    legs, unplaced, _o = run(legs, {"QQQ": S(40), "CRM": S(90)}, room=600.0)
    assert legs[0]["ticker"] == "CRM" and legs[0]["amount_ils"] == 600.0
    assert unplaced[0]["amount_ils"] == 400.0 and "under its cap" in unplaced[0]["reason"]


def test_no_room_at_all_keeps_the_top_up():
    legs = [{"ticker": "QQQ", "amount_ils": 1000.0, "asset_class": "Equities", "reason": "gap"}]
    legs, _u, _o = run(legs, {"QQQ": S(40), "CRM": S(90)}, room=100.0)
    assert legs[0]["ticker"] == "QQQ"


def test_the_simulated_card_keeps_the_floor_and_cannot_be_accepted(monkeypatch):
    monkeypatch.setattr(rr, "live_scores", lambda *a, **k: {
        "RDQ": S(40), "RDCRM": S(90)})
    monkeypatch.setattr(rr, "live_trend", lambda tk: {"state": "mixed"})
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "RDQ", "market": "NYSE", "asset_class": "Equities", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 3, "cost_basis": 100}]})
        c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High",
                                    "horizon_years": 6, "cash_target_ils": 150,
                                    "cash_redeploy_at_ils": 1500})
        real = c.get("/api/v1/recommendations").json()
        sim = c.get("/api/v1/recommendations?simulate_cash=6000").json()
        cash_rows = [p for p in c.get("/api/v1/portfolio").json().get("positions", [])
                     if str(p.get("ticker", "")).upper() == "CASH"]
    assert sim["simulated"] is True and sim["recommendations"], sim
    card = sim["recommendations"][0]
    assert card["apply"]["kind"] == "none" and card["apply"]["simulated"] is True
    assert card["meta"]["cash_after_ils"] >= 150, "never below the hard floor"
    assert any("Not financial advice" in h for h in card["how"])
    assert not any(r.get("id") == card["id"] for r in real["recommendations"]), (
        "the preview is not a card on Today")
    assert cash_rows == [], "the preview did not write any cash into the book"
    rec = next(r for r in card["meta"]["research"]["records"] if r["asset_class"] == "Equities")
    assert rec["decision"] == "swapped" and rec["chosen"] == "RDCRM", rec
