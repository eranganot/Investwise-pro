"""PR 71 -- one target mix for the whole app, and it adds up to 100%.

OBSERVED 2026-09-22 (Eran's screenshot, after accepting "Consider USO"):
  * the USO card: "Moves Commodities from 0% to 5% against your 10% target";
  * the rebalance card, the next screen: "Sell Commodities (~1,036)".
The live book target was {Equities .8322, Fixed Income .1614, Cash .0064} --
no Commodities. `funding_service.targets_for` merged the Grow table into it,
giving Equities 83.2 + Fixed Income 16.1 + Commodities 10 + Cash 0.6 = 110%.
Buy cards and funding read the merge; /mix, the rebalance card and the health
score read the book target. Each card was honest against its own target.

Eran's decision: "Yes, 10% carved from core". These pin it.

Not financial advice.
"""
from __future__ import annotations

import pytest

from app.services.book_target import compose
from app.services.funding_service import targets_for


def carved_classes(*a, **k):
    # Local import, so every other test here still RUNS against the previous
    # code and fails there for the right reason instead of failing to collect.
    from app.services.book_target import carved_classes as _c
    return _c(*a, **k)

LIVE_SLEEVES = {"SOXL": 0.40, "QQQ": 0.19}
SIXTY_FORTY = {"Equities": 0.6, "Fixed Income": 0.4}


def test_the_live_book_target_now_holds_ten_percent_commodities_from_the_core():
    bt = compose(sleeve_targets=LIVE_SLEEVES, core_mix=SIXTY_FORTY,
                 objective="Grow", cash_pct=0.0064)
    assert bt["target"]["Commodities"] == pytest.approx(0.10)
    assert bt["commodities_pct"] == pytest.approx(0.10)
    # The core shrinks by exactly the carve-out and stays 60/40 inside itself.
    assert bt["core_pct"] == pytest.approx(0.41 - 0.0064 - 0.10, abs=1e-4)
    assert bt["core"]["Equities"] / bt["core_pct"] == pytest.approx(0.6, abs=1e-3)
    # The sleeves are untouched.
    assert bt["sleeves"] == {"Equities": 0.59}
    assert sum(bt["target"].values()) == pytest.approx(1.0, abs=0.005)


@pytest.mark.parametrize("objective", ["Grow", "Balanced", "Preserve", "Income"])
def test_every_objective_yields_one_target_that_sums_to_one(objective):
    bt = compose(sleeve_targets=LIVE_SLEEVES, core_mix=SIXTY_FORTY,
                 objective=objective, cash_pct=0.0)["target"]
    t = targets_for(objective, bt)
    assert sum(t.values()) == pytest.approx(1.0, abs=0.005), t


def test_the_buy_card_and_the_rebalance_card_read_the_same_commodity_target():
    """The whole defect in one assertion: what funding buys toward is what the
    mix screen measures against."""
    bt = compose(sleeve_targets=LIVE_SLEEVES, core_mix=SIXTY_FORTY,
                 objective="Grow", cash_pct=0.0064)["target"]
    assert targets_for("Grow", bt).get("Commodities") == bt.get("Commodities")


def test_a_core_with_its_own_commodities_is_not_carved():
    """All-Weather already holds commodities; nothing is taken from it twice."""
    aw = {"Equities": 0.35, "Fixed Income": 0.40, "Commodities": 0.25}
    bt = compose(sleeve_targets=LIVE_SLEEVES, core_mix=aw, objective="Grow")
    assert bt["commodities_pct"] == 0.0
    assert bt["target"]["Commodities"] == pytest.approx(0.41 * 0.25, abs=1e-3)
    assert carved_classes(aw, "Grow") == set()


def test_preserve_has_no_commodities_to_carve():
    assert carved_classes(SIXTY_FORTY, "Preserve") == set()
    assert carved_classes(SIXTY_FORTY, "Grow") == {"Commodities"}


def test_a_held_commodity_is_neither_sold_by_the_rebalance_nor_by_the_core_card():
    """End to end, the screenshot's book shape: a 60/40 core plus a USO holding
    bought toward the commodity target. Nothing on Today may sell it back."""
    import logging

    from fastapi.testclient import TestClient

    import app.main as m

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
                {"ticker": "VTI", "market": "NYSE", "asset_class": "Equities", "depth": 1,
                 "spot_price": 376.88, "listing_price": 376.88, "quantity": 12.0,
                 "cost_basis": 370.0, "expected_return_pct": 7, "volatility_pct": 20},
                {"ticker": "BND", "market": "NYSE", "asset_class": "Fixed Income", "depth": 1,
                 "spot_price": 71.83, "listing_price": 71.83, "quantity": 40.0,
                 "cost_basis": 70.0, "expected_return_pct": 3, "volatility_pct": 6},
                {"ticker": "USO", "market": "NYSE", "asset_class": "Commodities", "depth": 2,
                 "spot_price": 80.0, "listing_price": 80.0, "quantity": 7.0,
                 "cost_basis": 80.0, "expected_return_pct": 5, "volatility_pct": 30}]})
            c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High",
                                        "horizon_years": 6, "target_amount": 137547})
            assert c.post("/api/v1/plan/core", json={"strategy_id": "bal_6040"}).json()["ok"]
            mix = c.get("/api/v1/mix").json()
            body = c.get("/api/v1/recommendations").json()
    finally:
        logging.disable(logging.NOTSET)
    assert mix["target_allocation"].get("Commodities") == pytest.approx(0.10, abs=0.005), mix
    cards = (body.get("recommendations") or []) + (body.get("more_recommendations") or [])
    for card in cards:
        text = " ".join(str(card.get(k) or "") for k in ("title", "action", "why", "how"))
        assert "Sell Commodities" not in text, card.get("title")
        if (card.get("apply") or {}).get("kind") == "core_to_basket":
            assert "USO" not in text, "the core card still treats USO as a stray core holding"
