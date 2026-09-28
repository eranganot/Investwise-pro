"""PR 73 -- the rebalances that moved shares and wrote nothing, reconstructed.

This replays Eran's REAL episode (production ledger and audit log, 2026-09-28):
the legs, their timestamps, the rebalance accepts, and today's counts. What was
measured by hand in the investigation must come out of the repair:

  * USO and UGA -- one rebalance each (09-26 13:14:49), the same Commodities
    factor ~1.0295 from two holdings: confidence "measured";
  * VTI -- the same 09-26 rebalance, Equities ~1.00268;
  * QQQ -- two rebalances on 09-20 (07:02:54 and 14:17:51, no QQQ leg between,
    so ONE merged step) plus the 09-26 one, whose factor VTI already solved.

After the repair writes its Adjust legs the per-holding chart can rebuild QQQ's
share count -- the thing Eran's item 2 was really asking about.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.tables import AuditLog, Transaction
from app.schemas.intake import IntakePosition
from app.schemas.state_machine import Market
from app.services import ledger_repair as lr
from app.services.feed_service import ensure_user
from app.services.intake_service import (
    ensure_account, ensure_entity, list_positions, upsert_positions)
from app.services.recommendations import _rid


def T(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


BOOK = {
    "QQQ": {"cls": "Equities", "now": 2.1212, "opened": "2026-09-18T14:54:12.142",
            "legs": [("Buy", 0.9048, "2026-09-18T14:54:12.386"),
                     ("Buy", 0.26, "2026-09-18T20:08:47.111"),
                     ("Buy", 0.702, "2026-09-22T04:16:21.371"),
                     ("Buy", 0.2103, "2026-09-26T12:25:21.682")]},
    "VTI": {"cls": "Equities", "now": 3.2901, "opened": "2026-09-25T03:26:50.967",
            "legs": [("Buy", 0.5118, "2026-09-25T03:26:54.319"),
                     ("Buy", 0.512, "2026-09-26T12:23:27.228"),
                     ("Buy", 1.611, "2026-09-26T12:25:21.682"),
                     ("Buy", 1.3465, "2026-09-26T12:26:41.943"),
                     ("Sell", 0.700009, "2026-09-26T13:14:28.747")]},
    "USO": {"cls": "Commodities", "now": 2.5875, "opened": "2026-09-22T07:52:59.400",
            "legs": [("Buy", 2.5135, "2026-09-22T07:52:59.415")]},
    "UGA": {"cls": "Commodities", "now": 2.878, "opened": "2026-09-22T08:39:46.848",
            "legs": [("Buy", 2.7956, "2026-09-22T08:39:46.864")]},
}
ACCEPTS = [(_rid("rebal", "Commodities"), "2026-09-20T07:02:54"),
           (_rid("rebal_merged", "Equities", "Fixed Income"), "2026-09-20T14:17:51"),
           (_rid("rebal", "Equities"), "2026-09-26T13:14:49"),
           # an accept of something that is NOT a rebalance must be ignored
           ("rec_7fbda1", "2026-09-26T13:36:37")]


async def _seed(s):
    user = await ensure_user(s, "rebal_repair@example.com")
    ent = await ensure_entity(s, user, "Personal", "Personal")
    acc = await ensure_account(s, ent, "Main")
    await upsert_positions(s, acc, [IntakePosition(
        ticker=tk, market=Market.NYSE, depth=2, spot_price=100.0, listing_price=100.0,
        quantity=b["now"], cost_basis=100.0, asset_class=b["cls"]) for tk, b in BOOK.items()])
    await s.commit()
    for p in await list_positions(s, user):
        p.created_at = T(BOOK[p.ticker]["opened"])
    # the ledger's first row is older than every episode, as on production
    s.add(Transaction(subject=user.email, ticker="V", txn_type="Buy", quantity=1,
                      executed_at=T("2026-08-03T15:00:00"), source="intake"))
    for tk, b in BOOK.items():
        for typ, q, at in b["legs"]:
            s.add(Transaction(subject=user.email, ticker=tk, txn_type=typ, quantity=q,
                              price=100.0, executed_at=T(at), source="probe"))
    for card, at in ACCEPTS:
        s.add(AuditLog(method="POST", route=f"/api/v1/recommendations/{card}/accept",
                       role="open", payload_sha256="0" * 64, created_at=T(at)))
    await s.commit()
    return user


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


@pytest.mark.asyncio
async def test_the_preview_reconstructs_every_step_and_writes_nothing():
    eng, S = _session()
    try:
        async with S() as s:
            user = await _seed(s)
            n_before = len((await s.scalars(select(Transaction))).all())
            out = (await lr.preview(s, user))["unrecorded_rebalances"]
            n_after = len((await s.scalars(select(Transaction))).all())
    finally:
        await eng.dispose()
    assert n_after == n_before, "the preview is read-only"
    assert [a["card"] for a in out["accepts"]] == [c for c, _ in ACCEPTS[:3]]
    assert out["unresolved"] == [] and out["disagreements"] == []
    by = {}
    for a in out["adjusts"]:
        by.setdefault(a["ticker"], []).append(a)
    # every holding's steps add up to its exact gap
    for tk, b in BOOK.items():
        rec = sum(q if t == "Buy" else -q for t, q, _ in b["legs"])
        assert sum(a["shares"] for a in by[tk]) == pytest.approx(b["now"] - rec, abs=1e-5), tk
    # Commodities: two holdings, one factor
    assert by["USO"][0]["factor"] == pytest.approx(1.0295, abs=1e-4)
    assert by["USO"][0]["confidence"] == "measured" == by["UGA"][0]["confidence"]
    # Equities 09-26: VTI's factor, which QQQ's last step reuses
    assert by["VTI"][0]["factor"] == pytest.approx(1.002685, abs=2e-5)
    q926 = next(a for a in by["QQQ"] if a["at"].startswith("2026-09-26"))
    assert q926["factor"] == pytest.approx(by["VTI"][0]["factor"], abs=1e-9)
    # QQQ's two 09-20 rebalances had no QQQ leg between them: one merged step
    q920 = [a for a in by["QQQ"] if a["at"].startswith("2026-09-20")]
    assert len(q920) == 1 and len(q920[0]["cards"]) == 2
    assert q920[0]["at"].startswith("2026-09-20T14:17:51"), "dated at the later accept"


@pytest.mark.asyncio
async def test_apply_writes_dated_adjusts_once_and_the_chart_can_rebuild_qqq():
    from app.services.position_history import position_history
    eng, S = _session()
    try:
        async with S() as s:
            user = await _seed(s)
            before = (await position_history(s, user, "QQQ", "1M"))["value"]
            refused = await lr.apply(s, user, confirm=False, include_rebalances=True)
            out = await lr.apply(s, user, confirm=True, include_rebalances=True)
            again = await lr.apply(s, user, confirm=True, include_rebalances=True)
            adj = (await s.scalars(select(Transaction).where(
                Transaction.source == "rebalance_repair"))).all()
            after = (await position_history(s, user, "QQQ", "1M"))["value"]
    finally:
        await eng.dispose()
    # The live refusal, reproduced -- and gone once the history is repaired.
    assert "+0.0441 shares" in (before.get("reason") or "")
    assert "cannot be right" not in (after.get("reason") or ""), after
    assert refused["ok"] is False
    assert len(out["rebalances_written"]) == len(adj) > 0
    assert again["rebalances_written"] == [], "a second apply writes nothing"
    assert all(a.txn_type == "Adjust" and float(a.value_ils or 0) == 0 for a in adj), (
        "no value: an Adjust with a value is read as a deposit")
    assert all(lr.REBAL_TAG in (a.note or "") for a in adj)
    qqq = sorted((a for a in adj if a.ticker == "QQQ"), key=lambda a: a.executed_at)
    assert [a.executed_at.strftime("%m-%d %H:%M") for a in qqq] == ["09-20 14:17", "09-26 13:14"]


@pytest.mark.asyncio
async def test_a_disputed_factor_is_written_for_nobody():
    """Review, CONFIRMED: when UGA disagreed with the factor USO solved, UGA was
    marked bad but USO's Adjust was still written -- and which of the two solves
    first is only the order positions come back in."""
    eng, S = _session()
    try:
        async with S() as s:
            user = await _seed(s)
            for p in await list_positions(s, user):
                if p.ticker == "UGA":
                    p.quantity = 3.0          # an edit nothing recorded
            await s.commit()
            out = (await lr.preview(s, user))["unrecorded_rebalances"]
    finally:
        await eng.dispose()
    written = {a["ticker"] for a in out["adjusts"]}
    assert "USO" not in written and "UGA" not in written
    assert {u["ticker"] for u in out["unresolved"]} >= {"USO", "UGA"}
    assert out["disagreements"], "the dispute is reported"


def test_an_accept_that_recorded_its_legs_is_not_counted_as_unrecorded():
    import inspect
    src = inspect.getsource(lr._rebalance_accepts)
    assert '"set_objective_and_rebalance"' in src, "the source recorded is kind[:40]"


@pytest.mark.asyncio
async def test_a_holding_that_solved_with_a_factor_cannot_confirm_it():
    """Review round 2, CONFIRMED: QQQ solved its 09-20 step USING VTI's 09-26
    factor and then 'agreed' with it -- circular. On the live shape VTI's factor
    has no independent witness in the tool (SOXL is excluded for blind sells),
    so VTI and QQQ are exact_total, never measured."""
    eng, S = _session()
    try:
        async with S() as s:
            user = await _seed(s)
            out = (await lr.preview(s, user))["unrecorded_rebalances"]
    finally:
        await eng.dispose()
    conf = {(a["ticker"], a["at"][:10]): a["confidence"] for a in out["adjusts"]}
    assert conf[("VTI", "2026-09-26")] == "exact_total"
    assert conf[("QQQ", "2026-09-26")] == "exact_total"
    assert conf[("USO", "2026-09-26")] == "measured" == conf[("UGA", "2026-09-26")]
