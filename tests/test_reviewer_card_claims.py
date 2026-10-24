"""PR 77: apply the review's sell card and re-measure the book (the card-claim
invariant), with the holding split across TWO accounts -- the multi-row defect
recorded after PR 73: `trim`/`sell_position` valued one row and deleted both."""
import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.tables import Transaction
from app.schemas.intake import IntakePosition
from app.schemas.state_machine import Market
from app.services import buyback_hold as bh
from app.services import holding_review as hr
from app.services import recommendations as rr
from app.services.feed_service import ensure_user
from app.services.intake_service import (
    ensure_account, ensure_entity, get_cash, list_positions, upsert_positions)
from tests._review_fixtures import down_both, measurement, short_dip


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


def _pos(tk, qty, basis):
    return IntakePosition(ticker=tk, market=Market.TASE, depth=2, spot_price=150.0,
                          listing_price=150.0, quantity=qty, cost_basis=basis,
                          asset_class="Equities")


async def _two_account_book(s, email, tk):
    user = await ensure_user(s, email)
    ent = await ensure_entity(s, user, "Personal", "Personal")
    a1 = await ensure_account(s, ent, "Main")
    a2 = await ensure_account(s, ent, "Second")
    await upsert_positions(s, a1, [_pos(tk, 6.0, 100.0)])
    await upsert_positions(s, a2, [_pos(tk, 4.0, 140.0)])
    await s.commit()
    # Distinct ages: rows written in one commit share `created_at`, and the tie
    # is broken by row id, which is random -- the tests say which lot is older.
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.models.tables import Position
    for basis, year in ((100.0, 2020), (140.0, 2021)):
        await s.execute(update(Position).where(Position.ticker == tk, Position.cost_basis == basis)
                        .values(created_at=datetime(year, 1, 1, tzinfo=timezone.utc)))
    await s.commit()
    return user


def _patch_history(monkeypatch, tk, closes):
    from app.agents.screener_agent import OpportunityAgent
    orig = OpportunityAgent._history

    def fake(self, ticker, days):
        return (closes, "ok") if ticker.upper() == tk else orig(self, ticker, days)
    monkeypatch.setattr(OpportunityAgent, "_history", fake)


@pytest.mark.asyncio
@pytest.mark.parametrize("path,state,fraction", [(down_both, "Exit", 1.0), (short_dip, "Trim", 0.5)])
async def test_the_sell_card_does_what_it_says_across_two_accounts(monkeypatch, path, state, fraction):
    tk = f"RV{state[0]}"
    _patch_history(monkeypatch, tk, path())
    eng, S = _session()
    try:
        async with S() as s:
            user = await _two_account_book(s, f"review_claims_{state}@example.com", tk)
            await hr.save(s, {"ticker": tk, **measurement()})
            await s.commit()
            built = await rr.build_recommendations(s, user)
            card = next(c for c in built["recommendations"] + built["more_recommendations"]
                        if (c.get("meta") or {}).get("ticker") == tk
                        and c.get("dimension") == "holding_review")
            row = next(r for r in built["holding_review"] if r["ticker"] == tk)
            cash0 = await get_cash(s, user)
            out = await rr.apply_recommendation(s, user, card["id"])
            left = [p for p in await list_positions(s, user) if p.ticker == tk]
            cash1 = await get_cash(s, user)
            legs = (await s.scalars(select(Transaction).where(Transaction.ticker == tk,
                                                              Transaction.txn_type == "Sell"))).all()
            holds = await bh.load(s, user)
    finally:
        await eng.dispose()
    assert row["state"] == state and card["actionable"] is True
    sold = card["apply"]["shares"]
    assert sold == pytest.approx(10.0 * fraction)
    remaining = sum(float(p.quantity) for p in left)
    assert remaining == pytest.approx(10.0 - sold), "both accounts count, neither is wiped"
    # Oldest row first, each at its own basis: 6 @ 100 then 4 @ 140.
    take1 = min(sold, 6.0)
    take2 = sold - take1
    gross = sold * 150.0
    tax = ((150 - 100) * take1 + (150 - 140) * take2) * get_settings().cgt_rate
    assert cash1 - cash0 == pytest.approx(gross - tax, abs=0.02)
    assert out["tax_ils"] == pytest.approx(tax, abs=0.02)
    assert sum(float(t.quantity) for t in legs) == pytest.approx(sold)
    if state == "Trim":
        assert holds[tk]["level"] == pytest.approx(150.0), "not topped back up above the sale price"
    else:
        # An exit leaves no row to price the hold with, so it could never release
        # (found in review); the card makes no buy-back claim for an exit.
        assert tk not in holds and "top" not in card["impact"]


@pytest.mark.asyncio
async def test_the_no_edge_review_no_longer_arms_a_stop(monkeypatch):
    """Eran, 2026-09-28 made this card arm a stop-loss. A3r (D132, 2026-10-24): exits are the
    protection manager's alone -- a core fund has none, a sleeve holding one trailing stop --
    so neither the review nor the momentum card offers a stop any more, and nothing is sold."""
    tk = "RVN"
    _patch_history(monkeypatch, tk, down_both())
    eng, S = _session()
    try:
        async with S() as s:
            user = await _two_account_book(s, "review_stop@example.com", tk)
            await hr.save(s, {"ticker": tk, **measurement(edge_long=False)})
            await s.commit()
            built = await rr.build_recommendations(s, user)
    finally:
        await eng.dispose()
    mine = [c for c in built["recommendations"] + built["more_recommendations"]
            if tk in {(c.get("meta") or {}).get("ticker"),
                      *[str(r.get("ticker")) for r in (c.get("apply") or {}).get("rules") or []]}]
    arming = [c["id"] for c in mine for r in (c.get("apply") or {}).get("rules") or []
              if r.get("rule_type") in ("stop_loss", "trailing_stop", "take_profit", "profit_protect")]
    assert arming == [], arming
    assert not [c for c in mine if c.get("dimension") in ("holding_review", "momentum")
                and (c.get("apply") or {}).get("kind") in ("trim", "sell_position")], "no edge: the review sells nothing"
    row = next(r for r in built["holding_review"] if r["ticker"] == tk)
    assert row, "the review itself still reports the holding"


def test_the_nightly_job_runs_and_stores(monkeypatch):
    """CLAUDE.md: a job nothing calls is a job nobody has tested."""
    from app.services import backtest_service
    from tests._review_fixtures import series, up

    async def seed():
        eng, S = _session()
        try:
            async with S() as s:
                user = await ensure_user(s, "review_job@example.com")
                ent = await ensure_entity(s, user, "Personal", "Personal")
                acc = await ensure_account(s, ent, "Main")
                await upsert_positions(s, acc, [_pos("RVJOB", 3.0, 100.0)])
                await s.commit()
        finally:
            await eng.dispose()
    asyncio.run(seed())
    monkeypatch.setattr(backtest_service, "_fetch",
                        lambda tks, classify=False, basis=None: ({t: series(t, up(700))[t] for t in tks}, []))
    done = hr.run_holding_review_blocking()
    assert any(d["ticker"] == "RVJOB" and d["ok"] for d in done)

    async def read():
        eng, S = _session()
        try:
            async with S() as s:
                return await hr.stored(s, ["RVJOB"])
        finally:
            await eng.dispose()
    got = asyncio.run(read())["RVJOB"]
    assert got["long"]["measured"] is True and hr.fresh(got)[0] is True


def _card_ils(text):
    """Every ₪ figure a card states, as numbers."""
    import re
    return [float(x.replace(",", "")) for x in re.findall(r"₪([0-9,]+(?:\.[0-9]+)?)", text)]


@pytest.mark.asyncio
async def test_the_card_values_the_same_lots_accept_sells(monkeypatch):
    """Found in review: the card valued rows in `list_positions` order (no ORDER
    BY) while Accept sold oldest-first. Here the OLDER lot is the second row
    returned, and the card's tax and cash must still be what Accept does."""
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.models.tables import Position
    tk = "RVORD"
    _patch_history(monkeypatch, tk, short_dip())
    eng, S = _session()
    try:
        async with S() as s:
            user = await ensure_user(s, "review_order@example.com")
            ent = await ensure_entity(s, user, "Personal", "Personal")
            a1 = await ensure_account(s, ent, "Main")
            a2 = await ensure_account(s, ent, "Second")
            await upsert_positions(s, a1, [_pos(tk, 4.0, 140.0)])
            await upsert_positions(s, a2, [_pos(tk, 6.0, 100.0)])
            await s.commit()
            await s.execute(update(Position).where(Position.ticker == tk, Position.cost_basis == 100)
                            .values(created_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
            await hr.save(s, {"ticker": tk, **measurement()})
            await s.commit()
            built = await rr.build_recommendations(s, user)
            card = next(c for c in built["recommendations"] + built["more_recommendations"]
                        if c.get("dimension") == "holding_review" and c["meta"]["ticker"] == tk)
            cash0 = await get_cash(s, user)
            out = await rr.apply_recommendation(s, user, card["id"])
            cash1 = await get_cash(s, user)
    finally:
        await eng.dispose()
    tax = 5 * (150 - 100) * get_settings().cgt_rate          # 5 from the older 6 @ 100
    assert out["tax_ils"] == pytest.approx(tax, abs=0.02)
    gross_c, tax_c, net_c = _card_ils(card["action"])[:3]
    assert tax_c == pytest.approx(round(tax), abs=1), card["action"]
    assert net_c == pytest.approx(round(cash1 - cash0), abs=1), card["action"]


@pytest.mark.asyncio
@pytest.mark.parametrize("rt,level", [("stop_loss", 200.0), ("take_profit", 120.0)])
async def test_a_rule_sale_counts_every_account(rt, level):
    """Found in review: the rules index kept ONE row per ticker, so a stop's
    "Sell all" on a ticker held 6 + 4 sold 4 and consumed the rule with 6 shares
    left unprotected. The rule card now sizes and values across every row."""
    from app.services import rules_service as rs
    tk = f"RVS{rt[0].upper()}"
    eng, S = _session()
    try:
        async with S() as s:
            user = await _two_account_book(s, f"review_rule_{rt}@example.com", tk)
            await rs.create_rule(s, user, ticker=tk, rule_type=rt, mode="price", level=level,
                                 note="two accounts")
            await s.commit()
            await rs.evaluate_user(s, user)
            built = await rr.build_recommendations(s, user)
            card = next(c for c in built["recommendations"] + built["more_recommendations"]
                        if c.get("dimension") == "rule" and (c.get("meta") or {}).get("ticker") == tk)
            cash0 = await get_cash(s, user)
            out = await rr.apply_recommendation(s, user, card["id"])
            cash1 = await get_cash(s, user)
            left = sum(float(p.quantity) for p in await list_positions(s, user) if p.ticker == tk)
    finally:
        await eng.dispose()
    if rt == "stop_loss":
        assert card["apply"]["shares"] == pytest.approx(10.0), "Sell all means both accounts"
        assert left == pytest.approx(0.0) and out["full_exit"] is True
    else:
        assert card["apply"]["shares"] + left == pytest.approx(10.0)
    net_claim = _card_ils(" ".join(card["how"]))[0]
    assert net_claim == pytest.approx(round(cash1 - cash0), abs=1), card["how"]
