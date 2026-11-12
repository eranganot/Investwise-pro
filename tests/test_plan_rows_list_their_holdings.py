"""PR 166 (V4) -- each plan row on Plan > My mix lists the holdings that make it up.

Owner, 2026-11-11: "I want that each plan (sleeves, core) will show what are
they made of -- the holdings that build this plan." Decided: expand on tap;
one line per holding, held vs target (% of the whole book) and the amount; at
most ONE tag, only when it applies -- not in your plan / shared with another
plan / a sleeve on its off-signal leg ("instead of SOXL").

The rows come from the same split PR 165 (D148) made: a sleeve's line is its
share of a ticker, the core's line is the rest, and a held name in no plan is
the core's (it is inside `core_held_pct` already) -- listed and marked rather
than hidden inside a total. Not financial advice.
"""
import os
import re
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from tests.test_a_ticker_the_core_shares_is_split import CORE, SEMIS, _pos  # noqa: E402
from tests.test_frontier_goal import _reset_plan  # noqa: E402

INDEX = Path(__file__).resolve().parents[1] / "app" / "static_app" / "index.html"


@pytest.fixture
def signals_off(monkeypatch):
    """Both sleeves on their off leg, as on production 2026-11-11."""
    from app.services import sleeve_service as sv

    async def _live(session, user):
        return {SEMIS: {"target": {"SMH": 1.0}, "as_of": "2026-11-10"}}
    monkeypatch.setattr(sv, "live_sleeve_targets", _live)


def _book(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("VTI", 3600.0), _pos("QQQ", 2250.0), _pos("VXUS", 900.0), _pos("SMH", 2350.0),
        _pos("XLE", 900.0)]})
    c.put("/api/v1/plan", json={"objective": "Grow"})
    assert c.post(f"/api/v1/strategies/{SEMIS}/apply?sleeve_pct=10").status_code == 200
    assert c.post("/api/v1/plan/core", json={"strategy_id": CORE}).json()["ok"] is True
    return c.get("/api/v1/plan/sleeves").json()


async def test_each_sleeve_lists_its_share_of_each_holding(own_session, signals_off):
    try:
        with TestClient(m.app) as c:
            j = _book(c)
        semis = next(s for s in j["sleeves"] if s["strategy_id"] == SEMIS)
        [h] = semis["holdings"]
        assert h["ticker"] == "SMH" and h["target_pct"] == pytest.approx(0.10)
        # The sleeve row and its holding lines agree -- the line is its share, not all of SMH.
        assert h["held_ils"] == pytest.approx(semis["held_ils"], abs=0.02)
        assert h["shared_with"] == ["core"]
        # SMH is the strategy's off-signal leg; SOXL is what it holds when on.
        assert h["parked"] is True and h["instead_of"] == ["SOXL"]
    finally:
        await _reset_plan(own_session)


async def test_the_core_lists_its_basket_and_what_no_plan_holds(own_session, signals_off):
    try:
        with TestClient(m.app) as c:
            j = _book(c)
        core = {h["ticker"]: h for h in j["core_holdings"]}
        assert {"VTI", "QQQ", "VXUS", "SMH"} <= set(core), core
        assert core["XLE"]["in_plan"] is False and core["XLE"]["target_pct"] == 0.0
        assert core["SMH"]["shared"] is True and core["VTI"]["shared"] is False
        # Every shekel accounted for once: sleeve lines + core lines = the book's holdings.
        semis = next(s for s in j["sleeves"] if s["strategy_id"] == SEMIS)
        total = sum(h["held_ils"] for h in j["core_holdings"]) + sum(h["held_ils"] for h in semis["holdings"])
        assert total == pytest.approx(3600 + 2250 + 900 + 2350 + 900, abs=0.1)
        # And the core's lines add up to the core row (cash aside: this book holds none).
        assert sum(h["held_pct"] for h in j["core_holdings"]) == pytest.approx(j["core_held_pct"], abs=0.002)
    finally:
        await _reset_plan(own_session)


def test_the_panel_expands_on_tap_and_tags_only_what_is_unusual():
    src = INDEX.read_text(encoding="utf-8")
    i = src.index("function mixPanel(){")
    body = src[i:src.index("\n}\n", i)]
    assert "MIXOPEN" in src and "function mixToggle(" in src
    assert 'onclick="mixToggle(' in body and "aria-expanded" in body
    for tag in ("not in your plan", "instead of", "shared with"):
        assert tag in body, tag
    # One tag per line: the tag is chosen by an if / else-if chain, never concatenated.
    chain = re.search(r"let tag=\"\";([\s\S]*?)const off=", body).group(1)
    assert chain.count("tag=") == 4 and chain.count("else if") == 3, chain
    # The Remove button still works inside a tappable row.
    assert "event.stopPropagation();removeSleeve(" in body
