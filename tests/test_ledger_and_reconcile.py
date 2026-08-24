"""The trade ledger, and the repair it makes recordable.

`Transaction` existed from the first schema and had **zero writers** for the
life of the app. That is why `nav_history` reported a book change as +34.62%
market performance, and why the 7,388 an FX bug minted on 2026-08-21 left
nothing to reconstruct it from.

The repair itself reduces to one number, and that is not luck: the bug
multiplied every holding in a class by the SAME factor and never touched
`cost_basis` per share. The book has the right shape and the wrong size, so
`true_qty = qty / s` for a single unknown `s`.
"""
import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402


def _seed(c, cash=621.79, contributed=20000):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "V", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
         "spot_price": 371.04, "listing_price": 371.04, "quantity": 10.0,
         "cost_basis": 340.51, "expected_return_pct": 7, "volatility_pct": 20},
        {"ticker": "SCHD", "market": "NYSE", "asset_class": "Equities", "depth": 1,
         "spot_price": 35.11, "listing_price": 35.11, "quantity": 60.0,
         "cost_basis": 33.36, "expected_return_pct": 5, "volatility_pct": 12},
    ]})
    if cash is not None:
        c.post("/api/v1/portfolio/cash", json={"amount_ils": cash})
    if contributed is not None:
        c.post("/api/v1/portfolio/contributions",
               json={"amount_ils": contributed, "kind": "deposit", "note": "opening"})


def _client():
    logging.disable(logging.CRITICAL)
    return TestClient(m.app)


# --------------------------------------------------------------------------- #
# the ledger
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_ledger_row_outlives_the_position_it_describes(own_session):
    """The whole reason `position_id` stopped being NOT NULL/CASCADE.

    A full exit DELETES the position. With the original schema that cascade
    deleted the record of the sale too -- so the ledger erased precisely the
    events it exists to remember, and did it silently.
    """
    from sqlalchemy import select

    from app.models.tables import Transaction, User
    from app.services import ledger_service as ledger
    from app.services.intake_service import delete_position, list_positions

    try:
        with _client() as c:
            _seed(c)
            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                pos = next(p for p in await list_positions(s, user) if p.ticker == "V")
                await ledger.record_leg(
                    s, user, ticker="V", market="NASDAQ", txn_type=ledger.SELL,
                    quantity=10, price=371.04, value_ils=11071.0, tax_ils=200.0,
                    position_id=pos.id, source="sell_position")
                await s.commit()
                await delete_position(s, user, "V", "NASDAQ")
                await s.commit()
                rows = (await s.execute(
                    select(Transaction).where(Transaction.ticker == "V"))).scalars().all()
    finally:
        logging.disable(logging.NOTSET)

    assert len(rows) == 1, "the sale record was deleted along with its position"
    # The guarantee is that the row SURVIVES and still says what it was. That is
    # why every identifying field is denormalised: `position_id` becomes a
    # dangling reference on SQLite (which ships with foreign_keys=OFF, so no
    # ondelete fires) and NULL on Postgres, and the row has to be readable
    # either way. Depending on the FK to carry meaning is what got this wrong
    # the first time.
    assert rows[0].ticker == "V" and rows[0].txn_type == "Sell"
    assert rows[0].market == "NASDAQ"
    assert float(rows[0].value_ils) == pytest.approx(11071.0)
    assert float(rows[0].tax_ils) == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_accepting_an_actionable_card_writes_the_ledger(own_session):
    """A book change nothing recorded is the bug class this table exists for."""
    from sqlalchemy import select

    from app.models.tables import Transaction, User
    from app.services.recommendations import _apply_buy_funded, _record_apply

    try:
        with _client() as c:
            _seed(c)
            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                detail = await _apply_buy_funded(s, user, {
                    "kind": "buy_funded", "ticker": "SCHD", "market": "NYSE",
                    "asset_class": "Equities", "amount_ils": 1000.0,
                    "from_cash_ils": 0.0,
                    "sells": [{"ticker": "V", "market": "NASDAQ", "shares": 1,
                               "value_ils": 1107.4, "tax_ils": 20.0,
                               "asset_class": "Equities"}]})
                await _record_apply(s, user, "buy_funded", "rec_test01", detail)
                rows = (await s.execute(select(Transaction))).scalars().all()
    finally:
        logging.disable(logging.NOTSET)

    sides = {r.ticker: r.txn_type for r in rows}
    assert sides.get("V") == "Sell", f"the sell leg was not recorded: {sides}"
    assert sides.get("SCHD") == "Buy", f"the buy leg was not recorded: {sides}"
    assert all(r.source == "buy_funded" for r in rows)
    assert all(r.note == "card rec_test01" for r in rows)


@pytest.mark.asyncio
async def test_a_swap_is_not_an_external_flow(own_session):
    """Selling V to buy SCHD moves nothing in or out of the book.

    Counting either leg as a flow would make a rebalance read as a deposit --
    the same failure that made a book change read as performance, in reverse.
    """
    from sqlalchemy import select

    from app.models.tables import User
    from app.services import ledger_service as ledger

    try:
        with _client() as c:
            _seed(c)
            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                await ledger.record_legs(
                    s, user,
                    sold=[{"ticker": "V", "shares": 1, "value_ils": 1107.4}],
                    bought=[{"ticker": "SCHD", "shares": 30, "amount_ils": 1100.0}],
                    source="buy_funded")
                await s.commit()
                flows = await ledger.net_flows_ils(s, user)
    finally:
        logging.disable(logging.NOTSET)

    assert flows == 0.0, f"a swap produced a flow of {flows}"


# --------------------------------------------------------------------------- #
# the repair
# --------------------------------------------------------------------------- #
def test_preview_is_read_only_and_names_its_anchors():
    try:
        with _client() as c:
            _seed(c)
            before = c.get("/api/v1/portfolio").json()
            pv = c.get("/api/v1/portfolio/reconcile/preview").json()
            after = c.get("/api/v1/portfolio").json()
    finally:
        logging.disable(logging.NOTSET)

    assert pv["ok"] is True
    assert after["nav_ils"] == before["nav_ils"], "preview must not write"
    assert pv["auto_apply_forbidden"] is True
    ids = {a["id"] for a in pv["anchors"]}
    assert "deposits" in ids
    for a in pv["anchors"]:
        assert a["basis"], "every anchor must show its own arithmetic"
        assert a["caveat"], "every anchor must say how it is wrong"


def test_apply_refuses_without_confirm():
    """A write that fires on a bare POST is one a stray retry can trigger."""
    try:
        with _client() as c:
            _seed(c)
            before = c.get("/api/v1/portfolio").json()["nav_ils"]
            r = c.post("/api/v1/portfolio/reconcile/apply", json={"scale": 1.2}).json()
            after = c.get("/api/v1/portfolio").json()["nav_ils"]
    finally:
        logging.disable(logging.NOTSET)

    assert r["ok"] is False and r["would_scale_by"] == 1.2
    assert after == before, "an unconfirmed apply changed the book"


def test_applying_a_scale_shrinks_every_holding_and_leaves_cash_alone():
    """Cash is ILS-native and was never scaled by the bug -- it was ZEROED.

    That is a different injury and this repair does not pretend to fix it.
    """
    try:
        with _client() as c:
            _seed(c)
            before = {p["ticker"]: p for p in c.get("/api/v1/portfolio").json()["positions"]}
            r = c.post("/api/v1/portfolio/reconcile/apply",
                       json={"scale": 1.25, "confirm": True}).json()
            after = {p["ticker"]: p for p in c.get("/api/v1/portfolio").json()["positions"]}
    finally:
        logging.disable(logging.NOTSET)

    assert r["ok"] is True
    assert after["V"]["quantity"] == pytest.approx(before["V"]["quantity"] / 1.25)
    assert after["SCHD"]["quantity"] == pytest.approx(before["SCHD"]["quantity"] / 1.25)
    assert after["CASH"]["quantity"] == pytest.approx(before["CASH"]["quantity"])
    # The shape survives: the ratio between holdings is what the bug preserved.
    r_before = before["V"]["quantity"] / before["SCHD"]["quantity"]
    r_after = after["V"]["quantity"] / after["SCHD"]["quantity"]
    assert r_after == pytest.approx(r_before), "a uniform scale must preserve proportions"


@pytest.mark.asyncio
async def test_the_correction_records_itself_in_the_ledger(own_session):
    """A repair that is itself invisible leaves the next reader where we started."""
    from sqlalchemy import select

    from app.models.tables import Transaction, User

    try:
        with _client() as c:
            _seed(c)
            c.post("/api/v1/portfolio/reconcile/apply",
                   json={"scale": 1.25, "confirm": True, "note": "anchor=deposits"})
            async with own_session() as s:
                (await s.execute(select(User))).scalars().first()
                rows = (await s.execute(
                    select(Transaction).where(Transaction.source == "reconcile")
                )).scalars().all()
    finally:
        logging.disable(logging.NOTSET)

    assert len(rows) == 2, f"expected one Adjust per holding, got {len(rows)}"
    assert all(r.txn_type == "Adjust" for r in rows)
    assert all(float(r.quantity) < 0 for r in rows), "a correction that removes shares is negative"
    assert all(r.note == "anchor=deposits" for r in rows)


def test_a_scale_derived_from_one_known_share_count():
    from app.services.reconcile_service import scale_from_share_count

    assert scale_from_share_count(10.0, 8.0) == pytest.approx(1.25)
    with pytest.raises(ValueError):
        scale_from_share_count(10.0, 0)


def test_a_scale_that_would_zero_a_holding_is_refused():
    try:
        with _client() as c:
            _seed(c)
            r = c.post("/api/v1/portfolio/reconcile/apply",
                       json={"scale": 1e12, "confirm": True}).json()
    finally:
        logging.disable(logging.NOTSET)
    assert r["ok"] is False and "refusing" in r["error"]
