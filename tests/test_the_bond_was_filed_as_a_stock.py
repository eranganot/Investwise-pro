"""PR 31 -- 13.7% of a live book was bonds, and every screen called it equity.

Measured on production `63c0559` on 2026-09-02, read-only:

    /api/v1/portfolio   BND  13.1331 sh @ 71.79 USD = 2,842.15 ILS of a 20,777.45 NAV
                        meta.asset_class = "Equities"
    /api/v1/mix         current_allocation {"Equities": 0.9703, "Cash": 0.0298}
                        Fixed Income: absent. Not 0.0 -- absent.
                        rebalance_actions  BUY  Fixed Income  2,243.96
                                           SELL Equities      1,626.87

2,243.96 is exactly 10.8% of NAV: the WHOLE fixed-income target, because the app
believed the holding was zero. Accepting that card lands the book at ~24% bonds
against a 10.8% target, and the equity leg is wrong in the same breath -- the
book was 83.3% equities against 89.2%, so the card asked to sell what it was
short of and buy what it already held too much of. Both signs inverted, from one
string.

The chain, end to end:

1.  `intake_service.buy_into_book` defaulted `asset_class="Equities"`.
2.  `strategy_service._execute_funded_sleeve` -- the sleeve funder, the busiest
    caller -- never passes one. (Audit log: two `POST /plan/sleeves/fund` calls
    at 17:23 and 17:24 on 2026-08-31 are where this BND row was created.)
3.  So the row was stamped "Equities" on the way in.
4.  `allocation_mix.classify` returned that stamp on its FIRST line, before
    reading `FIXED_INCOME_TICKERS` four lines below -- where "BND" has sat since
    the fix for this exact failure one asset class over. Its comment ends
    "every Vanguard and Schwab ticker, i.e. all the cheap ones."

Nothing here is a new capability. Every part of the answer was already in the
file; the precedence read them in the wrong order, and the only tests in the
area asserted on `classify(...)` calls that passed no meta at all -- so the
lookup was well covered and the branch that outranked it was not covered once.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services.allocation_mix import (  # noqa: E402
    ASSET_CLASS_SOURCE_USER, classify, classify_holding,
)


# --------------------------------------------------------------------------- #
# the precedence itself
# --------------------------------------------------------------------------- #
def test_the_table_outranks_a_class_nobody_confirmed():
    """A stored class with no provenance is a DEFAULT, and loses to the table."""
    assert classify("BND", "NASDAQ", "Equities") == "Fixed Income"
    assert classify("GLD", "NYSE", "Equities") == "Commodities"
    assert classify("VGIT", "NASDAQ", "Equities") == "Fixed Income"


def test_a_class_a_human_set_outranks_the_table():
    """The override still exists. It just has to be one.

    `update_position` is the only writer of the flag, so reaching this branch
    means a person named the class through the API. The app has no business
    overruling that -- an ADR that the tables do not know, held for its bond
    exposure, is the user's call and not a lookup's.
    """
    assert classify("BND", "NASDAQ", "Equities",
                    asset_class_source=ASSET_CLASS_SOURCE_USER) == "Equities"
    assert classify("GLD", "NYSE", "Alternatives",
                    asset_class_source=ASSET_CLASS_SOURCE_USER) == "Alternatives"


def test_cash_is_decided_before_the_tables_run():
    """The synthetic CASH row must never be classified by ticker.

    "CASH" is in neither table, so if the tables ran first the fallthrough would
    file the user's shekels as an equity -- and `_class_gap_recs` would then
    offer to top it up, i.e. buy cash with cash. `CASH_META` carries
    `asset_class: "Cash"` for precisely this reason and it is not an override,
    it is an invariant.
    """
    assert classify("CASH", "TASE", "Cash") == "Cash"
    assert classify("CASH", "TASE", "Cash",
                    asset_class_source=ASSET_CLASS_SOURCE_USER) == "Cash"


def test_an_unknown_ticker_keeps_whatever_it_was_given():
    """"I do not recognise this" is not "this is an equity".

    Splitting `_from_tables` out of `classify` is what makes those two answers
    distinguishable. A ticker no table knows still falls back to the stored
    class, and then to Equities -- unchanged behaviour, and the reason this
    change moves no holding that was already right.
    """
    assert classify("MSFT", "NASDAQ", None) == "Equities"
    assert classify("SOME.TA", "TASE", "Real Estate") == "Real Estate"
    assert classify("SOME.TA", "TASE", None) == "Equities"


def test_classify_holding_reads_a_row_the_way_the_screens_do():
    """The production row, reproduced exactly."""
    class _Row:
        ticker, market = "BND", "NASDAQ"
        meta = {"asset_class": "Equities", "depth": 3}

    assert classify_holding(_Row()) == "Fixed Income"
    # dicts too: `funding_service` and `context_service` hold projections, not rows
    assert classify_holding({"ticker": "BND", "market": "NASDAQ",
                             "meta": {"asset_class": "Equities"}}) == "Fixed Income"
    assert classify_holding({"ticker": "BND", "market": "NASDAQ",
                             "asset_class": "Equities"}) == "Fixed Income"
    assert classify_holding({"ticker": "BND", "market": "NASDAQ",
                             "meta": {"asset_class": "Equities",
                                      "asset_class_source": "user"}}) == "Equities"


# --------------------------------------------------------------------------- #
# the writer that created the bad stamp
# --------------------------------------------------------------------------- #
def test_the_sleeve_funder_no_longer_stamps_a_class_it_did_not_choose():
    """`buy_into_book` defaulted to "Equities"; its busiest caller passes nothing.

    The signature default is the whole defect -- a value invented at the moment
    of least information and then treated downstream as a decision.
    """
    import inspect

    from app.services.intake_service import buy_into_book
    assert inspect.signature(buy_into_book).parameters["asset_class"].default is None


@pytest.mark.asyncio
async def test_a_bond_bought_by_the_funder_is_counted_as_a_bond(own_session):
    """END TO END, on the production book shape. This is the regression.

    BND is added the way production added it: through `buy_into_book` with no
    `asset_class` argument, exactly as `_execute_funded_sleeve` calls it. Before
    this PR the row was stamped "Equities" and `/mix` reported Fixed Income at
    0% on a book holding 13.7% of it.
    """
    from sqlalchemy import select

    from app.models.tables import User
    from app.services.intake_service import buy_into_book

    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "TQQQ", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
             "spot_price": 69.15, "listing_price": 69.15, "quantity": 38.7228,
             "cost_basis": 71.8368, "expected_return_pct": 12, "volatility_pct": 60},
            {"ticker": "V", "market": "NASDAQ", "asset_class": "Equities", "depth": 1,
             "spot_price": 372.67, "listing_price": 372.67, "quantity": 0.8314,
             "cost_basis": 340.51, "expected_return_pct": 7, "volatility_pct": 20},
        ]})
        async with own_session() as s:
            user = (await s.execute(select(User))).scalars().first()
            await buy_into_book(s, user, ticker="BND", market="NASDAQ",
                                price=71.79, shares=13.1331, depth=3)
            await s.commit()

        mix = c.get("/api/v1/mix").json()["current_allocation"]
        assert mix.get("Fixed Income", 0.0) > 0.10, (
            f"BND is 13% of this book and the mix says {mix}")
        assert mix.get("Equities", 1.0) < 0.90

        # and the row itself carries no class it was never told
        pos = {p["ticker"]: p for p in c.get("/api/v1/portfolio").json()["positions"]}
        assert pos["BND"]["asset_class"] == "Fixed Income"


@pytest.mark.asyncio
async def test_an_override_survives_a_re_import(own_session):
    """`upsert_positions` replaces `row.meta` wholesale.

    Without the carry-over, a second intake of the same book demotes a
    deliberate class back to a guess -- silently, because the value it lands on
    is usually the same one, so nothing looks wrong until the ticker is one the
    tables know.
    """
    from sqlalchemy import select

    from app.models.tables import User
    from app.services.intake_service import list_positions, update_position

    with TestClient(m.app) as c:
        seed = {"entity_name": "Personal", "positions": [
            {"ticker": "BND", "market": "NASDAQ", "asset_class": None, "depth": 1,
             "spot_price": 71.79, "listing_price": 71.79, "quantity": 13.1331,
             "cost_basis": 72.1359, "expected_return_pct": 3, "volatility_pct": 6}]}
        c.post("/api/v1/intake/portfolio", json=seed)

        async with own_session() as s:
            user = (await s.execute(select(User))).scalars().first()
            row = (await list_positions(s, user))[0]
            await update_position(s, user, str(row.id), asset_class="Alternatives")

        assert c.get("/api/v1/portfolio").json()["positions"][0]["asset_class"] == "Alternatives"

        c.post("/api/v1/intake/portfolio", json=seed)   # same book, no class named
        assert c.get("/api/v1/portfolio").json()["positions"][0]["asset_class"] == "Alternatives"
