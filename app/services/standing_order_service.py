"""Standing orders: a monthly deposit into cash that records itself.

Eran, 2026-09-20: *"I want to allow and create repetitive contribution
(standing order per monthly) to my cash."* Chosen: **auto-credit + notify** --
on the day, the app adds the amount to cash on its own and sends a push; the
order can be edited or paused.

WHAT A RUN IS. Not a trade. The bank moves the money; this records that it
arrived. So one run is exactly what a manual "I deposited X" already is -- a
``contributions`` row plus a cash credit -- and nothing else in the app needs to
learn a new kind of event. The gain figure, ``nav_history``'s flow detector and
``contribution_rate`` all see a deposit, never a return.

ONE COMMIT PER RUN, AND TWO GUARDS AGAINST A DOUBLE CREDIT:

  * ``next_run_on`` advances in the same commit as the credit, so a crash leaves
    either both or neither.
  * the contribution's note names the order and the date it is FOR
    (``standing order ab12cd34 2026-10-01``); a run that finds its own note
    already in the ledger advances the date and credits nothing. That is the
    guard that holds if two workers ever race.

MISSED DAYS ARE CAUGHT UP, each on its own date. If the service was down on the
1st, the 1st is credited when it comes back -- dated the 1st, so the ledger says
when the money arrived rather than when the app noticed. Capped at
``MAX_CATCH_UP`` so a paused-and-forgotten order cannot mint a year of deposits
in one tick without anyone looking.

"TODAY" IS ISRAEL'S TODAY. A deposit on the 1st must not land on the evening of
the 30th because the server clock is UTC.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Contribution, StandingOrder, User

logger = logging.getLogger("investwise.standing_orders")

LOCAL_TZ = ZoneInfo("Asia/Jerusalem")
MAX_CATCH_UP = 3
MIN_DAY, MAX_DAY = 1, 28


def local_today(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ).date()


def _add_month(d: date, day: int) -> date:
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(y, m, day)


def first_run_on(day_of_month: int, today: date) -> date:
    """The next occurrence of `day_of_month` STRICTLY after today.

    Not today: an order created on the 1st has not seen today's transfer
    arrive yet as far as the app knows, and crediting it the moment it is saved
    would record a deposit nobody has made.
    """
    d = date(today.year, today.month, day_of_month)
    return d if d > today else _add_month(d, day_of_month)


def _check(amount_ils: float, day_of_month: int) -> None:
    if not (amount_ils and float(amount_ils) > 0):
        raise ValueError("amount must be more than 0")
    if not (MIN_DAY <= int(day_of_month) <= MAX_DAY):
        raise ValueError(f"day of month must be {MIN_DAY}-{MAX_DAY} "
                         f"(every month has a {MAX_DAY}th)")


def to_dict(o: StandingOrder) -> dict:
    return {"id": str(o.id), "amount_ils": round(float(o.amount_ils), 2),
            "day_of_month": o.day_of_month, "active": bool(o.active),
            "next_run_on": o.next_run_on if o.active else None,
            "last_run_on": o.last_run_on, "runs": o.runs, "note": o.note or "",
            "per_year_ils": round(float(o.amount_ils) * 12, 2)}


async def list_orders(session: AsyncSession, user: User) -> list[StandingOrder]:
    res = await session.execute(
        select(StandingOrder).where(StandingOrder.subject == user.email)
        .order_by(StandingOrder.created_at.asc()))
    return list(res.scalars().all())


async def monthly_total(session: AsyncSession, user: User) -> float:
    """Sum of ACTIVE orders per month. A paused order is not a commitment."""
    return round(sum(float(o.amount_ils) for o in await list_orders(session, user)
                     if o.active), 2)


async def _get(session: AsyncSession, user: User, order_id: str) -> StandingOrder:
    import uuid
    try:
        oid = uuid.UUID(str(order_id))
    except ValueError as exc:
        raise LookupError("no such standing order") from exc
    o = await session.get(StandingOrder, oid)
    if o is None or o.subject != user.email:
        raise LookupError("no such standing order")
    return o


async def create_order(session: AsyncSession, user: User, *, amount_ils: float,
                       day_of_month: int = 1, note: str = "",
                       today: date | None = None) -> StandingOrder:
    _check(amount_ils, day_of_month)
    today = today or local_today()
    o = StandingOrder(subject=user.email, amount_ils=Decimal(str(round(float(amount_ils), 2))),
                      day_of_month=int(day_of_month), active=True,
                      next_run_on=first_run_on(int(day_of_month), today).isoformat(),
                      runs=0, note=(note or "")[:200])
    session.add(o)
    await session.commit()
    return o


async def update_order(session: AsyncSession, user: User, order_id: str, *,
                       amount_ils: float | None = None, day_of_month: int | None = None,
                       active: bool | None = None, note: str | None = None,
                       today: date | None = None) -> StandingOrder:
    o = await _get(session, user, order_id)
    today = today or local_today()
    amt = float(o.amount_ils) if amount_ils is None else float(amount_ils)
    day = o.day_of_month if day_of_month is None else int(day_of_month)
    _check(amt, day)
    o.amount_ils = Decimal(str(round(amt, 2)))
    reschedule = day != o.day_of_month or (active is True and not o.active)
    o.day_of_month = day
    if active is not None:
        o.active = bool(active)
    if note is not None:
        o.note = note[:200]
    if reschedule:
        # Resuming a paused order must NOT catch up the months it was paused
        # for -- pausing means "no deposits". It restarts from the next day.
        o.next_run_on = first_run_on(day, today).isoformat()
    await session.commit()
    return o


async def delete_order(session: AsyncSession, user: User, order_id: str) -> None:
    o = await _get(session, user, order_id)
    await session.delete(o)
    await session.commit()


def _note_for(o: StandingOrder, on: str) -> str:
    return f"standing order {str(o.id)[:8]} {on}"


async def run_due(session: AsyncSession, user: User, *,
                  today: date | None = None) -> list[dict]:
    """Credit every due run for this user. Returns what was credited.

    Safe to call as often as you like: a run that is not due does nothing, and a
    run whose deposit is already in the ledger credits nothing a second time.
    """
    from app.services.intake_service import credit_cash

    today = today or local_today()
    done: list[dict] = []
    for o in await list_orders(session, user):
        if not o.active:
            continue
        n = 0
        while o.next_run_on and date.fromisoformat(o.next_run_on) <= today and n < MAX_CATCH_UP:
            on = o.next_run_on
            note = _note_for(o, on)
            already = (await session.execute(
                select(Contribution.id).where(Contribution.subject == user.email,
                                              Contribution.note == note))).first()
            amount = float(o.amount_ils)
            if already is None:
                # Dated the day it was FOR, at local noon, so the ledger says
                # when the money arrived rather than when a job noticed.
                when = datetime.combine(date.fromisoformat(on), time(12, 0),
                                        tzinfo=LOCAL_TZ).astimezone(timezone.utc)
                session.add(Contribution(subject=user.email,
                                         amount_ils=Decimal(str(round(amount, 2))),
                                         kind="deposit", occurred_at=when, note=note))
                await credit_cash(session, user, amount, commit=False)
                o.runs = int(o.runs or 0) + 1
                o.last_run_on = on
                done.append({"order_id": str(o.id), "for": on, "amount_ils": round(amount, 2)})
            o.next_run_on = _add_month(date.fromisoformat(on), o.day_of_month).isoformat()
            await session.commit()
            n += 1
    return done


async def _notify(session: AsyncSession, user: User, credited: list[dict]) -> int:
    if not credited:
        return 0
    from app.services.intake_service import get_cash
    from app.services.push_service import send_to_subject

    total = sum(c["amount_ils"] for c in credited)
    cash = await get_cash(session, user)
    body = (f"₪{total:,.0f} from your standing order was added to cash. "
            f"Cash is now ₪{cash:,.0f}.")
    try:
        return await send_to_subject(
            session, user.email, "Monthly deposit added", body,
            tag=f"standing-order-{credited[-1]['for']}", category="info",
            data={"kind": "standing_order", "credited": credited})
    except Exception:  # noqa: BLE001 -- a failed push must not undo a credit
        logger.warning("standing order push failed", exc_info=True)
        return 0


def run_standing_orders_blocking() -> dict:
    """APScheduler entrypoint. Own NullPool engine in its own loop -- see
    CLAUDE.md "Cross-loop sessions" and ``nav_history.run_nav_snapshot_blocking``."""
    import asyncio

    async def _run() -> dict:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.config import get_settings
        from app.services.feed_service import ensure_user

        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        credited, users, sent = 0, 0, 0
        try:
            async with Session() as session:
                today = local_today().isoformat()
                subjects = list((await session.scalars(
                    select(StandingOrder.subject).where(
                        StandingOrder.active.is_(True),
                        StandingOrder.next_run_on <= today).distinct())).all())
            for subj in subjects:
                async with Session() as session:
                    user = await ensure_user(session, subj)
                    await session.flush()
                    done = await run_due(session, user)
                    credited += len(done)
                    users += 1
                    sent += await _notify(session, user, done)
        finally:
            await engine.dispose()
        return {"users": users, "credited": credited, "pushes": sent}

    return asyncio.run(_run())


# PR 118b: the standing-orders view (was routes/intake._orders_payload).
async def orders_payload(session: AsyncSession, user: User) -> dict:
    orders = await list_orders(session, user)
    return {"orders": [to_dict(o) for o in orders],
            "monthly_total_ils": await monthly_total(session, user),
            "today": local_today().isoformat(),
            "note": ("On the day, the amount is added to your cash and recorded as a "
                     "deposit. No money is moved: this records the transfer your bank makes.")}
