"""PR 152 -- score hypothetical sleeve sets side by side on the optimiser's goal solve.

Owner, 2026-10-25: "which sleeve is better for me? which one will increase my
chances to meet my goals?" -- Trend-Filtered Semiconductors (held, 23%) or
Trend-Filtered Leveraged Nasdaq. The nightly optimiser never measures that swap
(the two are one bet, so neither is a candidate while the other is held), and no
endpoint scored a book of your choosing. `POST /plan/optimizer/compare` does,
read-only, through `frontier_service.score_books`.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402


def _fake(calls):
    async def score_books(session, user, books, *, extra_sleeves=None, nominal_nav=None, full=False):
        calls.append({"books": books, "extra": extra_sleeves})
        return {"ok": True, "scores": [{"ok": True, "p_goal_pct": 9.0 + i, "p_halve_pct": 1.0,
                                        "amount_at_confidence": 29000 + i, "roi_at_confidence_pct": 7.0,
                                        "proposed": {"x": 1}} for i, _ in enumerate(books)]}
    return score_books


def test_it_scores_each_book_and_names_the_sleeves_to_add(monkeypatch):
    from app.services import frontier_service as fs
    calls = []
    monkeypatch.setattr(fs, "score_books", _fake(calls))
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "VTI", "market": "NASDAQ", "asset_class": "Equities", "depth": 3, "spot_price": 100.0,
             "listing_price": 100.0, "quantity": 50.0, "cost_basis": 90.0}]})
        assert c.post("/api/v1/strategies/btm_trend_soxl/apply?sleeve_pct=23").status_code == 200
        r = c.post("/api/v1/plan/optimizer/compare", json={"books": [
            {"btm_trend_soxl": 23, "div_dbmf": 40}, {"btm_trend_tqqq": 23, "div_dbmf": 40}]})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["ok"] and [b["p_goal_pct"] for b in out["books"]] == [9.0, 10.0]
        assert "proposed" not in out["books"][0]
        assert calls[0]["extra"] == ["btm_trend_tqqq", "div_dbmf"]
        # read-only: the sleeve set is unchanged
        held = [s["strategy_id"] for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]]
        assert held == ["btm_trend_soxl"]


def test_it_refuses_an_unknown_strategy_and_an_overfull_book(monkeypatch):
    from app.services import frontier_service as fs
    monkeypatch.setattr(fs, "score_books", _fake([]))
    with TestClient(m.app) as c:
        assert c.post("/api/v1/plan/optimizer/compare",
                      json={"books": [{"nope": 10}]}).status_code == 400
        assert c.post("/api/v1/plan/optimizer/compare",
                      json={"books": [{"btm_trend_soxl": 60, "div_dbmf": 50}]}).status_code == 400
