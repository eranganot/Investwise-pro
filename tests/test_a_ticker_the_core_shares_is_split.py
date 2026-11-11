"""PR 165 (D148) -- a ticker a sleeve and the core basket both want is split
between them by target, on the Plan tab and in the sleeve-drift card.

MEASURED ON PRODUCTION, 2026-11-11 (`/plan/sleeves`, `/rules`, trade ledger):
SMH is the Trend-Filtered Semiconductors sleeve's 10% and 15% of a 54.24%
Diversified Global Growth core (8.14%). The book held 19.1% of it -- 1 point
over the combined 18.1% -- and the Plan tab read "sleeve 19.0% / 10.0%, core
46.2% / 54.2%": every shekel of SMH credited to the sleeve (`shared_with: {}`).

The same blind spot drove a card. "SMH has drifted to 19% of a 10% sleeve"
(`rec_71675a`) was accepted 11-10 14:30 and trimmed 1,543 of SMH the core
wanted; the SMH band, whose 18.1% includes the core, bought it back at 17:11 by
selling 1,612 of VXUS -- which is what put VXUS under its band and started the
VXUS/VTI loop PR 164 fixed.

PR 131 (D95) already split a ticker between two SLEEVES. The core was never a
claimant. Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from tests.test_frontier_goal import _reset_plan  # noqa: E402

SEMIS = "btm_trend_soxl"
CORE = "grow_diversified"          # VTI 40 / QQQ 25 / VXUS 20 / SMH 15


def _pos(ticker, ils, cls="Equities"):
    px = ils / 100.0
    return {"ticker": ticker, "market": "TASE", "asset_class": cls, "depth": 3,
            "spot_price": px, "listing_price": px, "quantity": 100.0, "cost_basis": px / 1.05,
            "expected_return_pct": 8, "volatility_pct": 20}


@pytest.fixture
def semis_on_smh(monkeypatch):
    """The sleeve's live signal points at SMH, as it does on production."""
    from app.services import sleeve_service as sv

    async def _live(session, user):
        return {SEMIS: {"target": {"SMH": 1.0}, "as_of": "2026-11-10"}}
    monkeypatch.setattr(sv, "live_sleeve_targets", _live)


async def _setup(c, own_session, smh_ils, *, core=True):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("VTI", 3600.0), _pos("QQQ", 2250.0), _pos("VXUS", 1800.0), _pos("SMH", smh_ils)]})
    c.put("/api/v1/plan", json={"objective": "Grow"})
    assert c.post(f"/api/v1/strategies/{SEMIS}/apply?sleeve_pct=10").status_code == 200
    if core:
        assert c.post("/api/v1/plan/core", json={"strategy_id": CORE}).json()["ok"] is True
    from app.services import sleeve_service as sv
    from app.services.feed_service import ensure_superadmin
    async with own_session() as s:
        core_t = await sv.core_ticker_targets(s, await ensure_superadmin(s))
    return c.get("/api/v1/plan/sleeves").json(), core_t


async def test_the_sleeve_holds_only_its_share_of_a_ticker_the_core_wants(own_session, semis_on_smh):
    try:
        with TestClient(m.app) as c:
            j, core_t = await _setup(c, own_session, 2350.0)
        assert core_t.get("SMH", 0.0) > 0.05, core_t        # the core wants SMH
        semis = next(s for s in j["sleeves"] if s["strategy_id"] == SEMIS)
        share = 0.10 / (0.10 + core_t["SMH"])
        # On main the sleeve held all 2,350 (23.5% of a 10% sleeve).
        assert semis["held_ils"] == pytest.approx(2350.0 * share, rel=1e-3), semis
        assert semis["shared_with"] == {"SMH": ["core"]}
        assert j["allocated_held_pct"] == pytest.approx(semis["held_pct"], abs=2e-4)
    finally:
        await _reset_plan(own_session)


async def test_sleeve_and_core_shares_add_up_to_the_holding(own_session, semis_on_smh):
    from app.services import sleeve_service as sv
    from app.services.feed_service import ensure_superadmin
    try:
        with TestClient(m.app) as c:
            await _setup(c, own_session, 2350.0)
        async with own_session() as s:
            occ = await sv.sleeve_occupancy(s, await ensure_superadmin(s))
        semis = next(x for x in occ["sleeves"] if x["strategy_id"] == SEMIS)
        assert semis["held_ils"] + occ["core_shared_ils"]["SMH"] == pytest.approx(2350.0, abs=0.05)
        # The core's actual share now carries its SMH.
        assert occ["core_held_pct"] == pytest.approx(1.0 - semis["held_pct"], abs=2e-4)
    finally:
        await _reset_plan(own_session)


async def test_without_a_core_basket_the_sleeve_keeps_the_whole_ticker(own_session, semis_on_smh):
    """No core claim known -> the PR 131 behaviour, unchanged."""
    try:
        with TestClient(m.app) as c:
            j, core_t = await _setup(c, own_session, 2350.0, core=False)
        semis = next(s for s in j["sleeves"] if s["strategy_id"] == SEMIS)
        if core_t.get("SMH"):
            pytest.fail(f"the objective's default core claimed SMH: {core_t}")
        assert semis["held_ils"] == pytest.approx(2350.0, rel=1e-3)
        assert semis["shared_with"] == {}
    finally:
        await _reset_plan(own_session)


def _smh_drift_cards(c):
    r = c.get("/api/v1/recommendations").json()
    cards = list(r.get("recommendations") or []) + list(r.get("more_recommendations") or [])
    cards += list(r.get("headsups") or []) + list(r.get("hidden_cards") or [])
    return [x for x in cards if str(x.get("title") or "").startswith("SMH has drifted to")]


async def test_no_drift_card_for_a_shared_ticker_on_its_combined_plan(own_session, semis_on_smh):
    """SMH at its sleeve + core target. On main: "SMH has drifted to 24% of a 10%
    sleeve", whose Accept trims the core's SMH."""
    try:
        with TestClient(m.app) as c:
            j, core_t = await _setup(c, own_session, 2350.0)
            nav = 3600.0 + 2250.0 + 1800.0 + 2350.0
            gap = abs(2350.0 / nav - (0.10 + core_t["SMH"])) * 100.0
            assert gap < 3.0, (gap, core_t)                 # precondition: on plan
            assert _smh_drift_cards(c) == []
    finally:
        await _reset_plan(own_session)


async def test_a_shared_ticker_far_over_its_combined_plan_says_both_parts(own_session, semis_on_smh):
    """Still a card when SMH is genuinely over -- measured against sleeve + core,
    and the card says so."""
    try:
        with TestClient(m.app) as c:
            await _setup(c, own_session, 9000.0)
            cards = _smh_drift_cards(c)
            assert cards, "SMH ~54% against ~24% must still be a drift card"
            t = cards[0]["title"]
            assert "sleeve +" in t and "core)" in t, t
    finally:
        await _reset_plan(own_session)
