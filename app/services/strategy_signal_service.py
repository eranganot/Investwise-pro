"""Daily evaluation of the user's active rule-based strategy.

A backtest says what a rule WOULD have done. This says what it wants today --
and, crucially, only speaks when that changes. A trend or swing rule emits a
target every session and almost always repeats yesterday's; notifying on the
target rather than on the change would produce a daily message saying nothing.

The execution firewall holds throughout: a flip becomes a card and a
notification, never an order. The app can tell you the rule fired; placing the
trade is yours.

Two deliberate refusals:

* No signal is emitted from stale prices. If the latest session in the feed is
  older than the freshness window the job abstains and says so, because "the
  rule says move to cash" derived from week-old closes is worse than silence.
* A flip stays pending until it is acted on or dismissed. Re-deriving it each
  day would let a signal you already declined reappear every morning.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.engines import strategy_backtest as bt
from app.models.tables import Plan, StrategySignalState, User
from app.services import strategy_catalog
from app.services.backtest_service import _fetch

logger = logging.getLogger(__name__)

# How old the newest close may be before a signal is refused. Three days covers
# a weekend plus a holiday without letting a genuinely stale feed through.
MAX_FEED_AGE_DAYS = 3

# Enough history for a 200-day average plus its confirmation window.
SIGNAL_DAYS = 400


def _describe(target: dict, catalog_entry: dict) -> str:
    if not target:
        return "no position"
    base = set(catalog_entry.get("base") or {})
    legs = ", ".join(f"{tk} {round(w * 100)}%" for tk, w in sorted(target.items()))
    if set(target) == base:
        return f"the core holding ({legs})"
    return legs


def evaluate(strategy_id: str, series: dict) -> dict:
    """Today's target for one strategy, or an abstention with a reason."""
    entry = strategy_catalog.get(strategy_id)
    if entry is None:
        return {"ok": False, "reason": "UNKNOWN_STRATEGY", "detail": strategy_id}
    spec = strategy_catalog.backtestable(only=[strategy_id])
    if not spec:
        return {"ok": False, "reason": "UNKNOWN_STRATEGY", "detail": strategy_id}
    spec = spec[0]

    missing = [tk for tk in bt.tickers_needed(spec) if tk not in series]
    if missing:
        return {"ok": False, "reason": bt.MISSING_TICKER,
                "detail": f"no price history for {', '.join(missing)}"}
    dates, px = bt.align(series)
    if len(dates) < 260:
        return {"ok": False, "reason": bt.INSUFFICIENT_HISTORY,
                "detail": f"{len(dates)} sessions"}

    # A signal derived from stale closes is worse than no signal: it reads as
    # today's instruction while describing last week's market.
    latest = dates[-1]
    try:
        age = (datetime.now(timezone.utc).date() - datetime.fromisoformat(latest).date()).days
    except ValueError:
        age = 0
    if age > MAX_FEED_AGE_DAYS:
        return {"ok": False, "reason": "STALE_FEED",
                "detail": f"newest close is {latest} ({age} days old)"}

    targets = bt.targets_for(px, spec)
    if isinstance(targets, dict):
        return targets
    target = {k: round(v, 4) for k, v in (targets[-1] or {}).items() if v > 0.005}
    return {"ok": True, "target": target, "as_of": latest,
            "describes": _describe(target, entry),
            "strategy_name": entry.get("name")}


async def _state(session: AsyncSession, subject: str, strategy_id: str) -> StrategySignalState | None:
    return (await session.execute(
        select(StrategySignalState).where(StrategySignalState.subject == subject,
                                          StrategySignalState.strategy_id == strategy_id)
    )).scalar_one_or_none()


async def active_strategy_id(session: AsyncSession, user: User) -> str | None:
    """The user's applied strategy, when it is one of the rule-based family."""
    plan = (await session.execute(
        select(Plan).where(Plan.user_id == user.id))).scalars().first()
    sid = getattr(plan, "strategy", None) if plan is not None else None
    return sid if sid and strategy_catalog.get(sid) else None


async def evaluate_user(session: AsyncSession, user: User) -> dict:
    """Evaluate the user's active strategy and record a flip if the target moved."""
    sid = await active_strategy_id(session, user)
    if not sid:
        return {"ok": False, "reason": "NO_RULE_STRATEGY"}
    spec = strategy_catalog.backtestable(only=[sid])[0]
    series, missing = _fetch(bt.tickers_needed(spec))
    if missing:
        return {"ok": False, "reason": bt.MISSING_TICKER, "detail": ", ".join(missing)}

    res = evaluate(sid, series)
    if not res.get("ok"):
        return res

    row = await _state(session, user.email, sid)
    target = res["target"]
    if row is None:
        # First evaluation establishes a baseline. Announcing it as a flip would
        # tell the user their strategy "changed" the moment they applied it.
        session.add(StrategySignalState(subject=user.email, strategy_id=sid,
                                        target=target, as_of=res["as_of"]))
        await session.commit()
        return {**res, "flipped": False, "first_run": True}

    changed = target != (row.target or {})
    if changed:
        row.previous_target = row.target or {}
        row.target = target
        row.flipped_at = datetime.now(timezone.utc)
        row.notified = False
    row.as_of = res["as_of"]
    await session.commit()
    return {**res, "flipped": changed, "pending": row.flipped_at is not None,
            "previous_target": row.previous_target or {}}


async def peek_user(session: AsyncSession, user: User) -> dict:
    """Evaluate without writing. For a diagnostic read; the job owns the state.

    Kept separate from ``evaluate_user`` on purpose: a GET that silently records
    a flip would mean opening a status page could consume the very signal it was
    showing you, and whether you were notified would depend on whether you
    happened to look.
    """
    sid = await active_strategy_id(session, user)
    if not sid:
        return {"ok": False, "reason": "NO_RULE_STRATEGY"}
    spec = strategy_catalog.backtestable(only=[sid])[0]
    series, missing = _fetch(bt.tickers_needed(spec))
    if missing:
        return {"ok": False, "reason": bt.MISSING_TICKER, "detail": ", ".join(missing)}
    res = evaluate(sid, series)
    row = await _state(session, user.email, sid)
    return {**res, "strategy_id": sid,
            "stored_target": (row.target if row else None),
            "pending": bool(row and row.flipped_at),
            "last_evaluated": (row.as_of if row else None)}


async def pending_signal_recs(session: AsyncSession, user: User) -> list[dict]:
    """A Today card for a flip the user has not yet acted on.

    Guidance, not an order: applying it rebalances the tracked book toward the
    new target, and the card says plainly that no brokerage order is placed.
    """
    sid = await active_strategy_id(session, user)
    if not sid:
        return []
    row = await _state(session, user.email, sid)
    if row is None or row.flipped_at is None:
        return []
    entry = strategy_catalog.get(sid) or {}
    now_txt = _describe(row.target or {}, entry)
    was_txt = _describe(row.previous_target or {}, entry)
    return [{
        "id": f"stratsig_{sid[:12]}",
        "dimension": "strategy",
        "severity": "HIGH",
        "title": f"{entry.get('name', sid)}: the rule changed what it wants to hold",
        "action": f"Move from {was_txt} to {now_txt}.",
        "why": entry.get("rule") or strategy_catalog.rule_summary(entry),
        "impact": ("This is the discipline you chose firing. Acting late is the main "
                   "reason a rule underperforms its own backtest."),
        "apply": {"kind": "set_plan", "strategy": sid},
        "how": [
            f"Signal derived from closes up to {row.as_of}.",
            "No brokerage order is placed — mirror the change at your broker.",
            "Ignore leaves your book alone and records that you declined.",
        ],
    }]


async def resolve_signal(session: AsyncSession, user: User, strategy_id: str) -> bool:
    """Clear a pending flip once the user has acted on or dismissed it."""
    row = await _state(session, user.email, strategy_id)
    if row is None or row.flipped_at is None:
        return False
    row.flipped_at = None
    row.notified = True
    await session.commit()
    return True


async def evaluate_all(session: AsyncSession) -> dict:
    users = (await session.execute(select(User))).scalars().all()
    checked, flipped = 0, 0
    for user in users:
        try:
            res = await evaluate_user(session, user)
        except Exception:  # noqa: BLE001
            logger.warning("strategy signal failed for a user", exc_info=True)
            continue
        if res.get("ok"):
            checked += 1
            if res.get("flipped"):
                flipped += 1
    return {"checked": checked, "flipped": flipped}


async def _evaluate_all_job() -> dict:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            return await evaluate_all(session)
    finally:
        await engine.dispose()


def run_strategy_signals_blocking() -> dict:
    """Sync entrypoint for APScheduler (runs in its own thread)."""
    try:
        res = asyncio.run(_evaluate_all_job())
        logger.info("strategy signals: %s", res)
        return res
    except Exception:  # noqa: BLE001
        logger.warning("scheduled strategy signal run failed", exc_info=True)
        return {"checked": 0, "flipped": 0}
