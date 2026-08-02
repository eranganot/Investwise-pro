"""Regression: the price refresh must never quote the synthetic CASH row.

Reported live: the dashboard showed 'Liquid cash ₪521,904 · 96.4% of portfolio'
while the edit modal showed the true 1,934.52. Cause: `refresh_all_positions`
iterated every Position including ticker "CASH" -- which is also a real NASDAQ
listing (Pathward Financial, ~$73) -- so the balance was repriced as a US bank
stock and stamped `price_currency: USD`. FX then multiplied it again:
1,934.52 x ~73 x ~3.70 ~= ₪521,904. That poisoned NAV, the allocation mix, the
funding engine's 'spendable cash' and every weight-based trading rule.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from decimal import Decimal  # noqa: E402

from app.services.intake_service import (  # noqa: E402
    CASH_META, is_cash_position, repair_cash_row,
)


class _Row:
    """Stand-in for a Position row (no DB needed for the invariant itself)."""

    def __init__(self, ticker, price, basis, meta):
        self.ticker, self.current_price, self.cost_basis, self.meta = (
            ticker, Decimal(str(price)), Decimal(str(basis)), meta)


def test_cash_is_detected_by_ticker_and_by_asset_class():
    assert is_cash_position("CASH")
    assert is_cash_position("cash")
    assert is_cash_position("XYZ", {"asset_class": "Cash"})
    # A real equity must not be mistaken for cash.
    assert not is_cash_position("META", {"asset_class": "Equities"})
    assert not is_cash_position("MSFT", None)


def test_repair_restores_the_ils_native_invariant():
    """A row already corrupted by an earlier refresh self-heals."""
    corrupted = _Row("CASH", 72.9, 1, {
        "asset_class": "Cash", "liquidity_score": 100, "volatility_pct": 0.0,
        "price_currency": "USD", "price_source": "yahoo", "price_as_of": "2026-08-01",
    })
    assert repair_cash_row(corrupted) is True
    assert float(corrupted.current_price) == 1.0
    assert corrupted.meta["price_currency"] == "ILS"
    # The quoting breadcrumbs are cleared, not merely overwritten.
    assert "price_source" not in corrupted.meta
    assert corrupted.meta["liquidity_score"] == CASH_META["liquidity_score"]
    # Idempotent: a healthy row reports no change.
    assert repair_cash_row(corrupted) is False


def test_refresh_skips_cash_and_reprices_everything_else():
    import asyncio

    from app.services import pricing_service

    rows = [_Row("CASH", 72.9, 1, {"asset_class": "Cash", "price_currency": "USD"}),
            _Row("MSFT", 100, 90, {})]

    class _Scalars:
        def __init__(self, data):
            self._data = data

        def all(self):
            return self._data

    class _Session:
        async def scalars(self, _q):
            return _Scalars(rows)

        async def get(self, *_a):
            return None

        def add(self, _o):
            pass

        async def commit(self):
            pass

    class _Quote:
        price, currency, as_of = 464.72, "USD", "2026-08-02"

    pricing_service.guarded_quote = lambda tk: _Quote()
    pricing_service.market_provider = lambda: type("P", (), {"name": "fmp"})()

    res = asyncio.run(pricing_service.refresh_all_positions(_Session()))

    assert res["skipped_cash"] == 1 and res["repaired_cash"] == 1
    assert res["updated"] == 1                      # only MSFT was quoted
    assert float(rows[0].current_price) == 1.0      # cash untouched by the quote
    assert rows[0].meta["price_currency"] == "ILS"
    assert float(rows[1].current_price) == 464.72   # the equity still reprices
