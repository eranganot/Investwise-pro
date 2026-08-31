"""Tel Aviv quotes agorot and London quotes pence, and the app called them shekels.

Several exchanges quote in a MINOR unit and say so in the currency code rather
than in the price. Verified against Yahoo on 2026-08-29, the same endpoint
`YahooMarketDataProvider` calls:

    TEVA.TA  currency ILA  price  11030.0   ->   110.30 ILS
    POLI.TA  currency ILA  price   7761.0   ->    77.61 ILS
    ESLT.TA  currency ILA  price 211190.0   ->  2111.90 ILS
    VOD.L    currency GBp  price    118.45  ->     1.1845 GBP
    AAPL     currency USD  price    319.70  ->   319.70 USD, no trap

`pricing_service` stamped `price_currency` straight from the quote, so a Tel Aviv
position carried "ILA" and a price a hundred times too big. Nothing downstream
knew that code:

  * the BUILTIN FX provider has no ILA pair, so `get_rate` falls through to
    `rate = 1.0` and returns it WITHOUT ERRORING -- indistinguishable from a
    legitimate peg;
  * the LIVE provider raises, and `fx.fx_rate` caught everything and returned
    1.0, which its own docstring called "failing safe".

Either way agorot were valued as shekels: NAV, gain %, the allocation mix, the
concentration caps and every card sized against NAV, all 100x wrong on that
holding, with no exception and no log line.

This needs no TASE feature work to reach a real book. The holdings form already
offers Tel Aviv and London.
"""
import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402

# Exactly what Yahoo returned. Not invented, and not rounded.
LIVE_QUOTES = [
    ("TEVA.TA", "ILA", 11030.0, "ILS", 110.30),
    ("POLI.TA", "ILA", 7761.0, "ILS", 77.61),
    ("ESLT.TA", "ILA", 211190.0, "ILS", 2111.90),
    ("VOD.L", "GBp", 118.45, "GBP", 1.1845),
    ("AAPL", "USD", 319.70, "USD", 319.70),
]


@pytest.mark.parametrize("ticker,quoted_ccy,quoted_px,major_ccy,major_px", LIVE_QUOTES)
def test_a_quoted_price_is_converted_to_its_major_unit(ticker, quoted_ccy, quoted_px,
                                                       major_ccy, major_px):
    from app.services.fx import to_major_units

    px, ccy = to_major_units(quoted_px, quoted_ccy)
    assert ccy == major_ccy, f"{ticker}: {quoted_ccy} should report as {major_ccy}"
    assert px == pytest.approx(major_px, abs=1e-6), (
        f"{ticker}: {quoted_px} {quoted_ccy} is {major_px} {major_ccy}, not {px}")


def test_pence_is_not_mistaken_for_pounds_by_uppercasing():
    """The trap inside the trap: "GBp".upper() == "GBP".

    Uppercasing the code before looking it up turns the whole check into a no-op
    for the one currency whose minor unit shares its major's letters. A real
    GBP price must survive untouched; a GBp price must not.
    """
    from app.services.fx import major_unit, to_major_units

    assert major_unit("GBp") == ("GBP", 100.0)
    assert major_unit("GBX") == ("GBP", 100.0)
    assert major_unit("GBP") == ("GBP", 1.0), "pounds must not be divided by 100"
    assert to_major_units(250.0, "GBP") == (250.0, "GBP")
    assert to_major_units(250.0, "GBp") == (2.5, "GBP")


def test_a_stored_minor_unit_stamp_is_reported_as_its_major_currency():
    """Rows written before this fix still carry "ILA". Do not ask FX for it."""
    from app.services.fx import price_currency

    assert price_currency("TASE", {"price_currency": "ILA"}) == "ILS"
    assert price_currency("LSE", {"price_currency": "GBp"}) == "GBP"
    # Untouched cases stay untouched.
    assert price_currency("NASDAQ", {"price_currency": "USD"}) == "USD"
    assert price_currency("TASE", None) == "ILS"


def test_fx_keeps_the_last_real_rate_instead_of_inventing_one(monkeypatch):
    """A provider wobble must not value a USD book at 27% of its worth.

    Falling back to 1.0 was never "safe": USD/ILS is ~2.98 today, so the
    fallback understates a dollar holding by about two thirds. The stub below
    uses 3.71 because that is what the builtin provider hardcodes, and these
    tests must not depend on a live rate.
    """
    from app.providers import registry
    from app.services import fx as fxmod

    fxmod._LAST_GOOD_RATE.clear()

    class _Rate:
        rate = 3.71

    monkeypatch.setattr(registry, "guarded_fx", lambda b, q: _Rate())
    assert fxmod.fx_rate("USD", "ILS") == pytest.approx(3.71)

    def _down(_b, _q):
        raise RuntimeError("provider down")

    monkeypatch.setattr(registry, "guarded_fx", _down)
    assert fxmod.fx_rate("USD", "ILS") == pytest.approx(3.71), (
        "a provider outage fell back to 1.0 and revalued the whole USD book")


def test_an_unknown_currency_code_is_reported_not_silently_valued_at_par(monkeypatch, caplog):
    """"ILA" resolving to 1.0 without a word is how this bug lived."""
    from app.providers import registry
    from app.services import fx as fxmod

    fxmod._LAST_GOOD_RATE.clear()

    def _unknown(_b, _q):
        raise LookupError("no such pair")

    monkeypatch.setattr(registry, "guarded_fx", _unknown)
    with caplog.at_level(logging.ERROR, logger="investwise.fx"):
        rate = fxmod.fx_rate("XYZ", "ILS")
    assert rate == 1.0, "still returns a number; thirteen call sites do arithmetic on it"
    assert any("NO RATE FOR XYZ/ILS" in r.getMessage() for r in caplog.records), (
        "an unresolvable currency was valued at par with nothing said about it")


@pytest.mark.asyncio
async def test_a_tel_aviv_holding_is_not_valued_a_hundred_times_over(own_session):
    """End to end, through the real refresh path, on the real numbers."""
    from types import SimpleNamespace as NS

    from sqlalchemy import select

    from app.models.tables import User
    from app.providers import registry
    from app.services import pricing_service
    from app.services.intake_service import list_positions

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            # 100 shares of Teva on the Tel Aviv listing. The user types the
            # price in shekels, because that is what the screen shows him.
            c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
                {"ticker": "TEVA.TA", "market": "TASE", "asset_class": "Equities",
                 "depth": 2, "spot_price": 110.30, "listing_price": 110.30,
                 "quantity": 100, "cost_basis": 100.0,
                 "expected_return_pct": 6, "volatility_pct": 25}]})
            nav_seeded = float(c.get("/api/v1/portfolio").json()["nav_ils"])
            assert nav_seeded == pytest.approx(11030.0, abs=1.0), (
                "seeded book should be 100 x 110.30 ILS")

            # Now the scheduled refresh runs and Yahoo answers in agorot, which
            # is what it really does.
            def _quote(tk):
                return NS(ticker=tk, price=11030.0, currency="ILA", market="Tel Aviv",
                          as_of="2026-08-28T13:30:00+00:00", as_of_source="market")

            monkey = registry.guarded_quote
            registry.guarded_quote = _quote
            pricing_service.guarded_quote = _quote
            try:
                async with own_session() as s:
                    user = (await s.execute(select(User))).scalars().first()
                    rows = await list_positions(s, user)
                    res = await pricing_service.refresh_all_positions(s, rows)
                    await s.commit()
            finally:
                registry.guarded_quote = monkey
                pricing_service.guarded_quote = monkey

            row = c.get("/api/v1/portfolio").json()["positions"][0]
            assert row["currency"] == "ILS", (
                f"the position is stamped {row['currency']!r}; no FX provider "
                "knows that code, so the rate silently resolves to 1.0")
            assert float(row["current_price"]) == pytest.approx(110.30, abs=0.01), (
                f"stored {row['current_price']} - agorot were written as shekels")
            nav = float(c.get("/api/v1/portfolio").json()["nav_ils"])
            assert nav == pytest.approx(11030.0, rel=1e-4), (
                f"NAV is {nav:.2f} against a true 11,030.00 - a factor of "
                f"{nav / 11030.0:.0f}")
            assert res.get("minor_unit_quotes") == 1, (
                "the refresh did not report that it normalised a minor-unit quote")
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.asyncio
async def test_a_row_already_stored_in_agorot_is_repaired(own_session):
    """The corrupted rows are already written; nothing else goes back for them.

    Repaired BEFORE the fresh quote, deliberately: a ticker whose quote fails or
    is stale keeps the price it already has, so on exactly the runs where the row
    is least likely to be rewritten the 100x value would survive another cycle.
    """
    from sqlalchemy import select

    from app.models.tables import User
    from app.providers import registry
    from app.services import pricing_service
    from app.services.intake_service import list_positions

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
                {"ticker": "TEVA.TA", "market": "TASE", "asset_class": "Equities",
                 "depth": 2, "spot_price": 110.30, "listing_price": 110.30,
                 "quantity": 100, "cost_basis": 100.0,
                 "expected_return_pct": 6, "volatility_pct": 25}]})
            # Forge the damage an old refresh did: agorot in the price, "ILA" in
            # the stamp.
            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                p = (await list_positions(s, user))[0]
                from app.models.tables import money
                p.current_price = money(11030.0)
                p.meta = {**(p.meta or {}), "price_currency": "ILA"}
                await s.commit()
            assert float(c.get("/api/v1/portfolio").json()["nav_ils"]) > 1_000_000.0, (
                "the forged row should read 100x - if not, this test proves nothing")

            # No quote available at all: the repair must still happen.
            def _no_quote(_tk):
                raise LookupError("provider down")

            # AND the secondary provider, which is NOT reached through
            # guarded_quote. refresh_all_positions builds a real
            # YahooMarketDataProvider and calls it directly when the primary
            # returns nothing, so patching the two guarded_quote references --
            # which is what the house rule says to do -- left this test talking
            # to the live market. It asserted against TEVA.TA's real price and
            # failed with a different number on every machine and every CI run.
            monkey = registry.guarded_quote
            monkey_fb = pricing_service.fallback_provider
            registry.guarded_quote = _no_quote
            pricing_service.guarded_quote = _no_quote
            pricing_service.fallback_provider = lambda: None
            try:
                async with own_session() as s:
                    user = (await s.execute(select(User))).scalars().first()
                    rows = await list_positions(s, user)
                    res = await pricing_service.refresh_all_positions(s, rows)
                    await s.commit()
            finally:
                registry.guarded_quote = monkey
                pricing_service.guarded_quote = monkey
                pricing_service.fallback_provider = monkey_fb

            assert res.get("repaired_minor_unit_rows") == 1
            nav = float(c.get("/api/v1/portfolio").json()["nav_ils"])
            assert nav == pytest.approx(11030.0, rel=1e-4), (
                f"NAV is {nav:.2f}; the repair did not run when the quote failed, "
                "which is the run that needed it most")
    finally:
        logging.disable(logging.NOTSET)
