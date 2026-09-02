"""PR 32 -- a provider answered 0, and the app wrote the holding down to zero.

Eran opened the app on 2026-09-01, found holdings showing **0 ILS**, and deleted
two of them, because a holding worth nothing looks like junk. By the next morning
the remaining rows were priced normally again and there was nothing on any screen
to say what had happened. The audit log has the deletes at 17:19:09 and 17:19:49;
it has nothing about the zero, because nothing recorded one.

`refresh_all_positions` guards, refuses and DISCLOSES every other way a quote can
be unusable:

    q is None                       -> `failed`, error recorded, price untouched
    quote older than the window     -> `stale`, `not_written`, disclosed on the row
    currency contradicts the market -> `conflict`, `not_written`, disclosed

Its own comment states the rule: *"Do NOT overwrite the price: a quote nothing
has traded against is not a current price."* A price of **0** is the same class
of thing, and it was the one case with no guard at all -- straight through to
`p.current_price = money(q_price)`.

Where a zero comes from:

  * Yahoo returns a meta block with `regularMarketPrice: 0` for a symbol it only
    half-resolves -- a delisted line, a venue suffix it does not carry, a ticker
    that has changed. The adapter's guard was `if price is None`.
  * FMP answers an unknown or delisted symbol with `"price": 0` rather than an
    error, so `is None` never fires there either.

Why it is not a display bug. `value_ils` is `quantity x price x rate`, so a
zeroed holding leaves **NAV** -- and NAV is what the mix, the rebalance sizing,
the concentration cap, the projection and the 22:10 snapshot are all measured
against. The snapshot is the one number the app cannot recompute later, so a
snapshot taken inside a zeroed window is wrong permanently.

Two layers, deliberately. The adapters raise, so the fallback provider gets its
turn and `guarded_quote` caches no poisoned zero. The refresh refuses as well,
so no quote source -- present or future, including the builtin one the tests run
on -- can write a holding down to zero.

Not financial advice.
"""
import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from types import SimpleNamespace as NS  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402

BOOK = {"entity_name": "Personal", "positions": [
    {"ticker": "AAA", "market": "NASDAQ", "asset_class": "Equities", "depth": 1,
     "spot_price": 100, "listing_price": 100, "quantity": 10, "cost_basis": 90,
     "expected_return_pct": 7, "volatility_pct": 14},
]}


def _quote(price, ccy="USD", market="NASDAQ"):
    return NS(ticker="AAA", market=market, price=price, currency=ccy,
              as_of="2026-09-01T20:00:00+00:00", as_of_source="market")


async def _refresh(own_session, stub, monkeypatch):
    from sqlalchemy import select

    from app.models.tables import User
    from app.providers import registry
    from app.services import pricing_service
    from app.services.intake_service import list_positions

    # PATCHED IN EVERY MODULE THAT HOLDS ITS OWN REFERENCE. `pricing_service`
    # binds `guarded_quote` at import time, so a patch on `registry` alone never
    # reaches it -- the house rule from test_accept_conserves_nav.
    for mod in (registry, pricing_service):
        monkeypatch.setattr(mod, "guarded_quote", stub, raising=False)
    # And the second source. `fallback_provider()` is a real Yahoo client behind
    # a seam; left alone it reaches the internet from inside the suite.
    monkeypatch.setattr(pricing_service, "fallback_provider", lambda: None, raising=False)

    async with own_session() as s:
        user = (await s.execute(select(User))).scalars().first()
        rows = await list_positions(s, user)
        res = await pricing_service.refresh_all_positions(s, rows)
        await s.commit()
    return res


# --------------------------------------------------------------------------- #
# the provider adapters
# --------------------------------------------------------------------------- #
def test_the_yahoo_adapter_refuses_a_zero(monkeypatch):
    """`regularMarketPrice: 0` must raise, not become a Quote."""
    import json

    from app.providers import live

    payload = {"chart": {"result": [{"meta": {"regularMarketPrice": 0,
                                              "currency": "USD",
                                              "regularMarketTime": 1788300000,
                                              "fullExchangeName": "NasdaqGS"}}]}}
    monkeypatch.setattr(live, "_http_text", lambda url, **kw: json.dumps(payload))
    with pytest.raises(ValueError, match="no usable price"):
        live.YahooMarketDataProvider().get_quote("AAA")

    # and the same adapter still works on a real number, so the guard is a guard
    # and not a wall
    payload["chart"]["result"][0]["meta"]["regularMarketPrice"] = 71.79
    q = live.YahooMarketDataProvider().get_quote("AAA")
    assert q.price == 71.79


def test_the_fmp_adapter_refuses_a_zero(monkeypatch):
    """FMP answers an unknown symbol with price 0 instead of an error."""
    from app.providers import live

    prov = live.FMPMarketDataProvider.__new__(live.FMPMarketDataProvider)
    monkeypatch.setattr(type(prov), "_get",
                        lambda self, *a, **k: [{"price": 0, "exchange": "NASDAQ"}],
                        raising=False)
    monkeypatch.setattr(type(prov), "to_symbol", lambda self, t: t.upper(), raising=False)
    with pytest.raises(ValueError, match="no usable price"):
        prov.get_quote("AAA")


# --------------------------------------------------------------------------- #
# the refresh -- THE regression
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_zero_quote_does_not_write_the_holding_down(own_session, monkeypatch):
    """THE defect, end to end. NAV must not move because a provider said 0."""
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            c.post("/api/v1/intake/portfolio", json=BOOK)
            before = c.get("/api/v1/portfolio").json()
            assert before["nav_ils"] > 0

            res = await _refresh(own_session, lambda tk: _quote(0.0), monkeypatch)

            # THE DAMAGE FIRST, so a failure here names what it cost rather than
            # reporting a missing counter. Against the old code this line reads
            #   NAV went 3710.0 -> 0.0 because a provider answered 0
            after = c.get("/api/v1/portfolio").json()
            assert after["nav_ils"] == pytest.approx(before["nav_ils"], abs=0.01), (
                f"NAV went {before['nav_ils']} -> {after['nav_ils']} because a "
                "provider answered 0. Nothing was bought or sold.")
            row = after["positions"][0]
            assert row["current_price"] == pytest.approx(100.0), (
                "the old price must be left alone, exactly as it is for a stale "
                "quote and a currency conflict")

            # then that it was refused deliberately rather than by accident
            assert res.get("unpriced") == 1, (
                f"the zero was not refused: {res.get('unpriced')!r}")
            assert res.get("updated") == 0, "a zero must not count as an update"
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.asyncio
async def test_a_real_quote_afterwards_heals_the_row(own_session, monkeypatch):
    """A warning that outlives its cause is one nobody reads."""
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            c.post("/api/v1/intake/portfolio", json=BOOK)
            await _refresh(own_session, lambda tk: _quote(0.0), monkeypatch)
            assert c.get("/api/v1/portfolio").json()["unpriced_positions"] == []

            res = await _refresh(own_session, lambda tk: _quote(123.45), monkeypatch)
            assert res.get("updated") == 1
            after = c.get("/api/v1/portfolio").json()
            assert after["positions"][0]["current_price"] == pytest.approx(123.45)
            assert after["unpriced_positions"] == []
            assert after["positions"][0].get("price_freshness") != "unpriced"
    finally:
        logging.disable(logging.NOTSET)


# --------------------------------------------------------------------------- #
# the disclosure -- what was missing on 2026-09-01
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_row_already_at_zero_is_named_rather_than_valued_at_zero(own_session):
    """Rows zeroed BEFORE this shipped must be disclosed too.

    Derived from the price, not from a meta flag: a flag only ever describes
    rows written by the build that sets it, and the rows that caused this were
    written by the build that had no flag.
    """
    from sqlalchemy import select

    from app.models.tables import User, money
    from app.services.intake_service import list_positions

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            c.post("/api/v1/intake/portfolio", json=BOOK)
            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                row = (await list_positions(s, user))[0]
                row.current_price = money(0)      # what the old refresh wrote
                await s.commit()

            pf = c.get("/api/v1/portfolio").json()
            names = [u["ticker"] for u in pf["unpriced_positions"]]
            assert names == ["AAA"], (
                f"a holding valued at 0 ILS was not disclosed anywhere: {pf['unpriced_positions']}")
            # what NAV is missing, at cost -- the only number the app has for a
            # holding it cannot price
            assert pf["unpriced_cost_ils"] > 0
            assert "could not be valued" in pf["unpriced_positions"][0]["reason"]
    finally:
        logging.disable(logging.NOTSET)


def test_cash_is_never_reported_as_unpriced():
    """The synthetic CASH row is priced at 1.0 by invariant, not by a provider."""
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            c.post("/api/v1/intake/portfolio", json=BOOK)
            c.post("/api/v1/portfolio/cash", json={"amount_ils": 500})
            pf = c.get("/api/v1/portfolio").json()
            assert all(u["ticker"] != "CASH" for u in pf["unpriced_positions"])
    finally:
        logging.disable(logging.NOTSET)
