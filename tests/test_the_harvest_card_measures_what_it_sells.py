"""The tax-loss card claimed a saving computed from positions it does not sell.

Measured on production, 2026-09-03, the morning after the core realign:

    apply.tickers            VTI, BND
    est_amount               146.46 ILS
    losses on VTI + BND      11.17 ILS  ->  2.79 ILS at the 25% CGT rate
    losses on SOXL + TQQQ   574.58 ILS  -> 143.65 ILS, excluded from the sale
                                            three lines earlier as strategy
                                            holdings, and counted anyway

98% of the headline number came from two positions the same code block had just
decided not to touch. `save` was read straight off `tax_opportunities`, which
scans the whole book, while `losers` had already been filtered.

Two more things the same card got wrong, and this file pins all four:

* **It sold the core.** The exclusion knew about sleeves only, so on a book that
  had realigned into `bal_6040` the day before, the card offered to sell VTI and
  BND -- the basket itself. PR 37 settled that a core is a strategy too.
* **It promised this year.** Every position in that book was below cost, so there
  were no gains to offset and `tax_opportunities` said so in its own description
  ("future gains (carry-forward)"). The card said "Lowers this year's tax bill"
  anyway. An unoffset loss carries forward; it does not reduce a bill that does
  not exist.
* **`gains` are unrealized.** Where the book does show winners, they owe no tax
  until they are sold, so the loss offsets nothing until they are.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402

# Seeded prices, quoted back so the book holds still. Every one is below its
# cost basis in the fixtures, which is the shape production was in.
PRICES = {"SOXL": 100.0, "SCHD": 39.0, "VTI": 370.0, "BND": 70.0}

# The sleeve the plan holds on purpose. It carries almost all of the loss, which
# is what makes the two figures diverge.
SLEEVE = {"SOXL"}


@pytest.fixture
def pinned(monkeypatch):
    """Quote the seeded prices, and pin the sleeve the plan claims.

    `sleeve_tickers` is patched rather than driven through the plan API so the
    exclusion set is an input to this test rather than another thing under test.
    The harvest block resolves it as a module attribute at call time.
    """
    from types import SimpleNamespace as NS

    from app.providers import registry
    from app.services import pricing_service, sleeve_service, strategy_service

    def _quote(tk):
        tk = (tk or "").upper()
        if tk not in PRICES:
            raise LookupError(tk)
        return NS(ticker=tk, price=PRICES[tk], currency="USD",
                  market="NYSE" if tk in ("SCHD", "VTI") else "NASDAQ")

    for mod in (registry, pricing_service, strategy_service):
        monkeypatch.setattr(mod, "guarded_quote", _quote, raising=False)

    async def _sleeve_tickers(session, user):
        return set(SLEEVE)

    monkeypatch.setattr(sleeve_service, "sleeve_tickers", _sleeve_tickers, raising=False)


def _usd(ticker, qty, cost, market="NASDAQ"):
    return {"ticker": ticker, "market": market, "asset_class": "Equities", "depth": 1,
            "spot_price": PRICES[ticker], "listing_price": PRICES[ticker],
            "quantity": qty, "cost_basis": cost,
            "expected_return_pct": 7, "volatility_pct": 20}


def _seed(c, positions):
    c.post("/api/v1/intake/portfolio",
           json={"entity_name": "Personal", "positions": positions})
    c.post("/api/v1/portfolio/cash", json={"amount_ils": 620.0})
    c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High",
                                "horizon_years": 6, "target_amount": 137547})


def _tax_card(c):
    body = c.get("/api/v1/recommendations").json()
    # Both lists: since PR 70 the first 12 are chosen by priority, and a small
    # harvest on a book left busy by earlier tests can sit in the collapsed rest.
    cards = [r for r in (body.get("recommendations") or []) + (body.get("more_recommendations") or [])
             if r.get("dimension") == "tax"]
    return cards[0] if cards else None


def _ils_loss(ticker, qty, cost):
    """What selling this holding realizes, in shekels, using the app's own FX."""
    from app.services.fx import fx_rate
    return (cost - PRICES[ticker]) * qty * (fx_rate("USD") or 1.0)


def test_the_saving_is_measured_on_the_tickers_the_card_sells(pinned):
    """SOXL is excluded from the sale. Its loss must leave with it."""
    from app.core.config import get_settings

    with TestClient(m.app) as c:
        # SOXL carries 2,000 USD of loss and is protected; SCHD carries 10 USD
        # and is the only thing the card can actually sell.
        _seed(c, [_usd("SOXL", 100.0, 120.0), _usd("SCHD", 10.0, 40.0, "NYSE")])
        card = _tax_card(c)
        assert card is not None, "no tax card was built on a book carrying losses"
        assert card["apply"]["tickers"] == ["SCHD"], (
            f"expected to sell only SCHD, got {card['apply']['tickers']}")

        expected = get_settings().cgt_rate * _ils_loss("SCHD", 10.0, 40.0)
        assert abs(card["est_amount"] - expected) < 1.0, (
            f"card claims {card['est_amount']:.2f} ILS but selling "
            f"{card['apply']['tickers']} is worth {expected:.2f} ILS in tax -- the "
            f"difference is SOXL's loss, which this card is not offering to realize")


def test_the_core_basket_is_not_offered_for_harvest(pinned):
    """A core is a strategy too. Harvesting it undoes the realign."""
    with TestClient(m.app) as c:
        _seed(c, [_usd("VTI", 10.0, 380.0, "NYSE"), _usd("BND", 30.0, 72.0, "NYSE"),
                  _usd("SCHD", 10.0, 40.0, "NYSE")])
        assert c.post("/api/v1/plan/core", json={"strategy_id": "bal_6040"}).json()["ok"] is True

        card = _tax_card(c)
        assert card is not None, "no tax card was built on a book carrying losses"
        sold = set(card["apply"]["tickers"])
        assert not (sold & {"VTI", "BND"}), (
            f"the card offers to sell {sorted(sold & {'VTI', 'BND'})} -- the Classic "
            f"60/40 basket the core holds on purpose")
        assert "VTI" in (card["audit_trail"]["raw_data"]["excluded_strategy_sleeve"]), (
            "the core was dropped silently; the card must say what it left alone")


def test_a_loss_with_nothing_to_offset_is_not_this_years_saving(pinned):
    """Every position below cost means no gains, so the loss carries forward."""
    with TestClient(m.app) as c:
        _seed(c, [_usd("SCHD", 10.0, 40.0, "NYSE"), _usd("VTI", 4.0, 380.0, "NYSE")])
        card = _tax_card(c)
        assert card is not None, "no tax card was built on a book carrying losses"

        text = " ".join(str(card.get(k)) for k in ("title", "why", "action", "impact", "how"))
        low = text.lower()
        # The two claims the card used to make verbatim. "not this year's" is
        # allowed and is the point, so this matches the promise, not the words.
        for promise in ("in tax this year", "lowers this year's tax bill"):
            assert promise not in low, (
                f"the card promises a saving this year on a book with no gains "
                f"to offset ({promise!r}):\n  {text}")
        assert card["audit_trail"]["raw_data"]["offsets_a_gain_now"] is False
        assert "carry-forward" in text.lower(), (
            "an unoffset loss is a carry-forward and the card should say so")
