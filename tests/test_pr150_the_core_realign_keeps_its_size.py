"""PR 150 (C30) -- the core realign and the cash card no longer undo each other.

Read live from production on 2026-10-25:

* "Bring your core back to Diversified Global Growth" (`rec_f6f017`) trimmed QQQ,
  SMH, VTI and VXUS (₪3,153 on the card) and left ALL of it in cash ("the basket
  does not need it"), because the basket was sized to the core TARGET (26%) while
  the core held ~76% -- the money waiting for an unfunded 40% DBMF sleeve.
* It kept XLE, XLF, COMT, UGA and USO "for the sleeves" (PR 60's first claim).
* The cash card then spent that cash back into the core, XLE and XLF included:
  06:07 UTC, ₪1,197 sold by the realign, ₪1,758 bought by `rec_41ac4b`.
* `rec_f6f017` was accepted seven times 10-23..10-25 and was still on Today.

Owner, 2026-10-25: "Swap contents, keep size". While a sleeve the app may buy is
unfunded, the realign sells the off-basket names into the basket and never trims
the core to cash; moving money from the core into the sleeves is "Fund sleeves".
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services.core_realign import plan_realign  # noqa: E402

# The live core region on 2026-10-25 (₪), Diversified Global Growth, core target 26%.
LIVE = dict(basket={"VTI": 0.40, "QQQ": 0.25, "VXUS": 0.20, "SMH": 0.15}, core_pct=0.2624,
            nav=19778.0,
            held={"VTI": 3740.0, "QQQ": 2095.0, "VXUS": 1433.0, "SMH": 1075.0,
                  "XLE": 1872.0, "XLF": 2361.0, "COMT": 632.0, "UGA": 1422.0, "USO": 407.0},
            cash_ils=4699.0, cash_floor_ils=150.0, cgt_rate=0.25)


def test_on_the_target_size_the_realign_sends_the_core_to_cash():
    """The old sizing, kept as the reproduction: sized to 26% of the book, the
    realign raises far more than the basket needs and the rest becomes cash."""
    plan = plan_realign(**LIVE)
    assert plan["to_cash_ils"] > 5000, plan["to_cash_ils"]


def test_keeping_its_size_the_realign_leaves_nothing_in_cash():
    plan = plan_realign(**LIVE, keep_size=True)
    sold = {leg["ticker"] for leg in plan["legs"] if leg["action"] == "sell" and leg.get("full_exit")}
    bought = {leg["ticker"] for leg in plan["legs"] if leg["action"] == "buy"}
    assert sold == {"XLE", "XLF", "COMT", "UGA", "USO"}, plan["legs"]
    assert bought and bought <= set(LIVE["basket"]), plan["legs"]
    # nothing left for the cash card to buy back (below a leg)
    assert plan["to_cash_ils"] < 100, plan["to_cash_ils"]
    # the core keeps its size: what it buys is what it sold, less tax
    assert plan["buys_ils"] == pytest.approx(plan["sells_ils"] - plan["tax_ils"], abs=150), plan
    assert plan["keep_size"] and plan["core_size_pct"] == pytest.approx(
        sum(LIVE["held"].values()) / LIVE["nav"], abs=1e-3)


def test_keep_size_never_shrinks_a_core_that_is_under_its_target():
    """NOT CHANGED: a core below its target still grows to it from cash."""
    small = {**LIVE, "held": {"VTI": 1000.0, "QQQ": 500.0}}
    a, b = plan_realign(**small), plan_realign(**small, keep_size=True)
    assert a["want_ils"] == b["want_ils"] and not b["keep_size"]


def _pos(ticker, qty, price=100.0, cls="Equities"):
    return {"ticker": ticker, "market": "NASDAQ", "asset_class": cls, "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": 100.0, "expected_return_pct": 8, "volatility_pct": 20}


@pytest.fixture
def quotes(monkeypatch):
    from types import SimpleNamespace as NS

    from app.providers import registry
    from app.services import pricing_service, strategy_service

    def _q(tk):
        return NS(ticker=(tk or "").upper(), price=100.0, currency="USD", market="NASDAQ")
    for mod in (registry, pricing_service, strategy_service):
        monkeypatch.setattr(mod, "guarded_quote", _q, raising=False)


def test_accepting_the_core_card_leaves_no_cash_for_the_cash_card(quotes):
    """End to end: an unfunded sleeve, a core holding off-basket names. Accept
    the core card: cash does not grow, so no cash card buys the core back."""
    from tests._cards import all_cards
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("VTI", 40.0), _pos("QQQ", 20.0), _pos("XLE", 20.0), _pos("XLF", 20.0)]})
        c.post("/api/v1/recommendations/restore")
        c.post("/api/v1/recommendations/restore-completed")
        assert c.post("/api/v1/strategies/btm_qqq_hold/apply?sleeve_pct=30").status_code == 200
        assert c.post("/api/v1/plan/core", json={"strategy_id": "grow_diversified"}).status_code == 200
        cash0 = float(c.get("/api/v1/portfolio").json().get("cash_ils") or 0.0)
        core = [x for x in all_cards(c.get("/api/v1/recommendations").json())
                if (x.get("apply") or {}).get("kind") == "core_to_basket"]
        assert core, "the core holds XLE and XLF; there must be a core card"
        r = c.post(f"/api/v1/recommendations/{core[0]['id']}/accept")
        assert r.status_code == 200, r.text
        pos = {p["ticker"] for p in c.get("/api/v1/portfolio").json()["positions"]}
        assert not ({"XLE", "XLF"} & pos), pos
        cash1 = float(c.get("/api/v1/portfolio").json().get("cash_ils") or 0.0)
        assert cash1 <= cash0 + 100, (cash0, cash1)
