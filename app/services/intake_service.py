"""Portfolio intake persistence (Section 5).

Persists entities -> accounts -> positions and rebuilds Lag observations from
stored positions so the pipeline can run on the real portfolio. Lag/risk inputs
(depth, spot/listing price, expected return, volatility, action) live in the
position's JSON `meta`.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Account, Contribution, Entity, Position, User, money
from app.schemas.intake import IntakePosition
from app.services.allocation_mix import ASSET_CLASS_SOURCE_USER
from app.schemas.lag import LagObservation


async def ensure_entity(session: AsyncSession, user: User, name: str, entity_type: str) -> Entity:
    res = await session.execute(
        select(Entity).where(Entity.user_id == user.id, Entity.name == name)
    )
    entity = res.scalar_one_or_none()
    if entity is None:
        entity = Entity(user_id=user.id, name=name, entity_type=entity_type)
        session.add(entity)
        await session.flush()
    return entity


async def ensure_account(session: AsyncSession, entity: Entity, name: str) -> Account:
    res = await session.execute(
        select(Account).where(Account.entity_id == entity.id, Account.name == name)
    )
    account = res.scalar_one_or_none()
    if account is None:
        account = Account(entity_id=entity.id, name=name, currency="ILS")
        session.add(account)
        await session.flush()
    return account


async def upsert_positions(
    session: AsyncSession, account: Account, positions: list[IntakePosition]
) -> int:
    existing = {
        p.ticker: p for p in (await session.execute(
            select(Position).where(Position.account_id == account.id)
        )).scalars().all()
    }
    for ip in positions:
        # Cash is ILS-native: 1 unit = 1 shekel, price and basis pinned at 1.0.
        # This function overwrites price and meta wholesale, so a caller passing
        # a CASH line would reprice the balance as whatever it supplied -- the
        # 2026-08-10 incident, through a third door. No caller does this today;
        # the guard is here so that "no caller does" stops being load-bearing.
        if is_cash_position(ip.ticker, {"asset_class": ip.asset_class}):
            row = existing.get(ip.ticker)
            if row is None:
                session.add(Position(
                    account_id=account.id, ticker="CASH", market="TASE",
                    quantity=Decimal(str(round(float(ip.quantity), 2))),
                    cost_basis=Decimal("1"), current_price=Decimal("1"),
                    lifecycle_stage="ACTIVE", meta=dict(CASH_META)))
            else:
                row.quantity = Decimal(str(round(float(ip.quantity), 2)))
                repair_cash_row(row)
            continue
        meta = {
            "depth": ip.depth,
            "spot_price": ip.spot_price,
            "listing_price": ip.listing_price,
            "expected_return_pct": ip.expected_return_pct,
            "volatility_pct": ip.volatility_pct,
            "action_type": ip.action_type.value,
            "asset_class": ip.asset_class,
            "expense_ratio_pct": ip.expense_ratio_pct,
        }
        row = existing.get(ip.ticker)
        # A class the user set DELIBERATELY survives a re-import that does not
        # name one. This function replaces `row.meta` wholesale, so without this
        # a second intake of the same book silently demotes an override back to
        # a guess -- and the demotion is invisible, because the value it lands
        # on is usually the same one.
        if row is not None and ip.asset_class is None:
            old = row.meta if isinstance(row.meta, dict) else {}
            if old.get("asset_class_source") == ASSET_CLASS_SOURCE_USER and old.get("asset_class"):
                meta["asset_class"] = old["asset_class"]
                meta["asset_class_source"] = ASSET_CLASS_SOURCE_USER
        if row is None:
            session.add(Position(
                account_id=account.id, ticker=ip.ticker, market=ip.market.value,
                quantity=money(ip.quantity), cost_basis=money(ip.cost_basis),
                current_price=money(ip.listing_price), lifecycle_stage="DETECTED",
                meta=meta,
            ))
        else:
            row.market = ip.market.value
            row.quantity = money(ip.quantity)
            row.cost_basis = money(ip.cost_basis)
            row.current_price = money(ip.listing_price)
            row.meta = meta
    await session.flush()
    return len(positions)


async def buy_into_book(
    session: AsyncSession, user: User, *, ticker: str, market: str, price: float,
    shares: float, asset_class: str | None = None, depth: int = 1,
) -> bool:
    """Add `shares` of `ticker` at `price` (native currency) to the user's book.

    `asset_class` defaults to **None**, not to "Equities". It used to default to
    the string, and `_execute_funded_sleeve` -- the sleeve funder, the busiest
    caller here -- never passes one. So every holding it bought was stamped
    "Equities" on the way in, BND included, and `classify` returned that stamp
    ahead of its own bond table. None means "nobody has said", which is the
    truth at this call site and lets the lookup answer.

    THE ONE implementation of "top up a holding", because there were three and two
    of them were wrong.

    `upsert_positions` **replaces** ``row.quantity`` (see line 91) -- correct for an
    intake, catastrophic for a purchase: buying more of something you already own
    overwrote the whole position with just the shares bought. It also scopes its
    `existing` lookup to ONE account, so a buy landing in a different account than
    the original silently created a duplicate row and double-counted NAV. This
    looks the ticker up across the whole book and blends, so neither can happen.

    Returns True if a NEW position was created, False if an existing one grew.
    """
    from app.schemas.state_machine import Market

    tk = (ticker or "").upper()
    if shares <= 0 or price <= 0 or not tk:
        raise ValueError(f"refusing to book {shares} of {tk!r} at {price}")

    rows = await list_positions(session, user)
    existing = next((p for p in rows if (p.ticker or "").upper() == tk), None)
    if existing is not None:
        # Blend the basis so gain/loss stays honest after topping up.
        old_qty, old_basis = float(existing.quantity), float(existing.cost_basis or 0)
        new_qty = old_qty + shares
        existing.quantity = money(new_qty)
        if new_qty > 0:
            existing.cost_basis = money(
                (old_qty * old_basis + shares * price) / new_qty)
        existing.current_price = money(price)
        await session.flush()
        return False

    mk = market if market in {m.value for m in Market} else "NASDAQ"
    entity = await ensure_entity(session, user, "Personal", "Personal")
    account = await ensure_account(session, entity, "Main")
    await upsert_positions(session, account, [IntakePosition(
        ticker=tk, market=Market(mk), depth=depth, spot_price=price,
        listing_price=price, quantity=shares, cost_basis=price,
        asset_class=asset_class)])
    return True


async def list_positions(
    session: AsyncSession, user: User, entity_name: str | None = None
) -> list[Position]:
    q = (select(Position).join(Account, Position.account_id == Account.id)
         .join(Entity, Account.entity_id == Entity.id)
         .where(Entity.user_id == user.id))
    if entity_name:
        q = q.where(Entity.name == entity_name)
    return (await session.execute(q)).scalars().all()


async def delete_position(
    session: AsyncSession, user: User, ticker: str, market: str | None = None
) -> int:
    """Delete the acting user's holding(s) by ticker (optionally market). Returns count removed."""
    q = (select(Position).join(Account, Position.account_id == Account.id)
         .join(Entity, Account.entity_id == Entity.id)
         .where(Entity.user_id == user.id, Position.ticker == ticker))
    if market:
        q = q.where(Position.market == market)
    rows = (await session.execute(q)).scalars().all()
    for p in rows:
        await session.delete(p)
    await session.commit()
    return len(rows)


async def update_position(
    session: AsyncSession, user: User, position_id: str, *,
    ticker: str | None = None, market: str | None = None, asset_class: str | None = None,
    quantity: float | None = None, cost_basis: float | None = None,
    current_price: float | None = None,
) -> Position | None:
    """Edit a single holding the acting user owns (by id). Returns the row, or None if not found."""
    try:
        pid = uuid.UUID(str(position_id))
    except (ValueError, TypeError, AttributeError):
        return None
    q = (select(Position).join(Account, Position.account_id == Account.id)
         .join(Entity, Account.entity_id == Entity.id)
         .where(Entity.user_id == user.id, Position.id == pid))
    row = (await session.execute(q)).scalars().first()
    if row is None:
        return None

    # The cash row is ILS-native: 1 unit = 1 shekel, price and basis pinned at
    # 1.0. Only `quantity` is the balance. This is the last writer of
    # current_price that had no guard -- the audit after the 2026-08-10 incident
    # found the reprice path (fixed by sharing one implementation) and this one.
    # Nothing in the UI reaches it, but the API does, and "no UI path" is not a
    # guarantee. Editing the balance still works; mispricing it does not.
    if is_cash_position(row.ticker, row.meta if isinstance(row.meta, dict) else None):
        if quantity is not None:
            row.quantity = Decimal(str(round(max(0.0, float(quantity)), 2)))
        repair_cash_row(row)
        await session.commit()
        return row

    if ticker:
        row.ticker = ticker
    if market:
        row.market = market
    if quantity is not None:
        row.quantity = money(quantity)
    if cost_basis is not None:
        row.cost_basis = money(cost_basis)
    if current_price is not None:
        row.current_price = money(current_price)
    if asset_class is not None:
        # THE ONE WRITER of the provenance flag. Reaching this line means a
        # human named the class through the API, which is the only case where a
        # stored class is allowed to outrank `allocation_mix._from_tables`.
        # Every other writer of `meta["asset_class"]` is supplying a default.
        meta = dict(row.meta or {})
        meta["asset_class"] = asset_class
        meta["asset_class_source"] = ASSET_CLASS_SOURCE_USER
        row.meta = meta
    await session.commit()
    return row


# Cash is the most liquid thing you can hold; without an explicit score it fell
# back to the generic 70 and dragged the liquidity health score down.
CASH_META = {"asset_class": "Cash", "price_currency": "ILS", "liquidity_score": 100,
             "volatility_pct": 0.0}


def is_cash_position(ticker: str | None, meta: dict | None = None) -> bool:
    """True for the synthetic ILS cash row.

    Matters because "CASH" is *also* a real listed ticker (Pathward Financial,
    NASDAQ), so anything that quotes tickers blindly will happily reprice your
    shekels as a US bank stock.
    """
    if (ticker or "").upper() == "CASH":
        return True
    if isinstance(meta, dict) and str(meta.get("asset_class") or "").lower() == "cash":
        return True
    return False


def repair_cash_row(position) -> bool:
    """Force a cash row back to its ILS-native invariant (1 unit = ₪1).

    Returns True when something was actually wrong. Used to self-heal rows that
    a price refresh corrupted before cash was excluded from quoting.
    """
    changed = False
    if float(position.current_price or 0) != 1.0:
        position.current_price = Decimal("1")
        changed = True
    if float(position.cost_basis or 0) != 1.0:
        position.cost_basis = Decimal("1")
        changed = True
    meta = {k: v for k, v in (position.meta or {}).items()
            if k not in ("price_source", "price_as_of")}
    meta.update(CASH_META)
    if meta != (position.meta or {}):
        position.meta = meta
        changed = True
    return changed


async def credit_cash(session: AsyncSession, user: User, amount_ils: float) -> float:
    """Add ILS proceeds as visible liquidity: grow (or create) a 'CASH' holding.

    Cash is stored ILS-native (current_price = 1.0, price_currency = ILS) so its
    value flows straight through FX normalization and NAV as `quantity` shekels.
    Returns the amount credited (0 if non-positive).
    """
    amount_ils = float(amount_ils or 0.0)
    if amount_ils <= 0:
        return 0.0
    rows = await list_positions(session, user)
    cash = next((p for p in rows if (p.ticker or "").upper() == "CASH"), None)
    if cash is not None:
        cash.quantity = Decimal(str(round(float(cash.quantity) + amount_ils, 2)))
        cash.cost_basis = Decimal("1")          # repairs rows written before this fix
        cash.current_price = Decimal("1")
        cash.meta = dict(CASH_META)
        await session.commit()
        return amount_ils
    account_id = rows[0].account_id if rows else None
    if account_id is None:
        entity = await ensure_entity(session, user, "Personal", "Personal")
        account = await ensure_account(session, entity, "Main")
        account_id = account.id
    session.add(Position(
        account_id=account_id, ticker="CASH", market="TASE",
        # cost_basis is PER SHARE. Cash holds `amount` units priced at 1.0, so the
        # basis is 1.0 -- storing the full amount here made invested = qty x basis
        # report the balance squared (₪2,500 cash -> "₪6.25M invested").
        quantity=Decimal(str(round(amount_ils, 2))), cost_basis=Decimal("1"),
        current_price=Decimal("1"), lifecycle_stage="ACTIVE",
        meta=CASH_META,
    ))
    await session.commit()
    return amount_ils


async def set_cash(session: AsyncSession, user: User, amount_ils: float) -> float:
    """Set the cash balance to an absolute figure (vs ``credit_cash``, which adds).

    Cash you already hold outside the app was previously untrackable \u2014 a CASH
    position only ever appeared as a side effect of accepting a sell, so the
    allocation donut read 100% equities for a book that held real liquidity.
    Returns the new balance. Passing 0 removes the position entirely.
    """
    amount_ils = max(0.0, float(amount_ils or 0.0))
    rows = await list_positions(session, user)
    cash = next((p for p in rows if (p.ticker or "").upper() == "CASH"), None)
    if cash is not None:
        if amount_ils <= 0:
            await session.delete(cash)
            await session.commit()
            return 0.0
        cash.quantity = Decimal(str(round(amount_ils, 2)))
        cash.cost_basis = Decimal("1")          # per-share; cash units are worth 1.0
        cash.current_price = Decimal("1")
        cash.meta = dict(CASH_META)
        await session.commit()
        return amount_ils
    if amount_ils <= 0:
        return 0.0
    return await credit_cash(session, user, amount_ils)


async def get_cash(session: AsyncSession, user: User) -> float:
    """Current ILS cash balance (0 when no CASH position exists)."""
    rows = await list_positions(session, user)
    cash = next((p for p in rows if (p.ticker or "").upper() == "CASH"), None)
    return float(cash.quantity) if cash is not None else 0.0


async def get_entities(session: AsyncSession, user: User) -> list[dict]:
    rows = (await session.execute(
        select(Entity).where(Entity.user_id == user.id)
    )).scalars().all()
    out = []
    for e in rows:
        cnt = (await session.execute(
            select(func.count(Position.id)).join(Account, Position.account_id == Account.id)
            .where(Account.entity_id == e.id)
        )).scalar_one()
        out.append({"id": str(e.id), "name": e.name, "type": e.entity_type, "positions": cnt})
    return out


def position_to_observation(p: Position) -> LagObservation | None:
    m = p.meta or {}
    spot = m.get("spot_price")
    listing = m.get("listing_price")
    if not spot or not listing:
        return None
    return LagObservation(
        ticker=p.ticker, market=p.market, depth=int(m.get("depth", 1)),
        spot_price=float(spot), listing_price=float(listing),
        action_type=m.get("action_type", "Buy"),
        expected_return_pct=m.get("expected_return_pct"),
        volatility_pct=m.get("volatility_pct"),
    )


# --- Contributions: the only thing that changes "what you put in" -----------

async def record_contribution(session: AsyncSession, user: User, amount_ils: float,
                              *, note: str = "") -> float:
    """Log external money in (positive) or out (negative). Returns the new total.

    Trading only rearranges money that is already inside the account, so nothing
    in the sell/trim/swap path may call this.
    """
    amount_ils = round(float(amount_ils or 0.0), 2)
    if amount_ils == 0:
        return await total_contributed(session, user)
    session.add(Contribution(
        subject=user.email, amount_ils=Decimal(str(amount_ils)),
        kind="deposit" if amount_ils > 0 else "withdrawal", note=(note or "")[:200]))
    await session.commit()
    return await total_contributed(session, user)


async def total_contributed(session: AsyncSession, user: User) -> float | None:
    """Net external money contributed, or None when the user has never logged any.

    None is not zero: a user who has never recorded a deposit should keep seeing
    the legacy cost-basis estimate rather than a confident, wrong "₪0 put in".
    """
    res = await session.execute(
        select(func.count(Contribution.id), func.sum(Contribution.amount_ils))
        .where(Contribution.subject == user.email))
    count, total = res.one()
    return None if not count else round(float(total or 0.0), 2)


# W3. A rate is total money over elapsed time, and both halves can be absent or
# useless. These are the reasons a rate CANNOT be measured, each one a different
# fact about the ledger, none of them collapsed into "0".
RATE_NO_LEDGER = "NO_LEDGER"          # total_contributed() is None: never logged one
RATE_SINGLE_ENTRY = "SINGLE_ENTRY"    # one row is an opening balance, not a rate
RATE_TOO_SHORT = "TOO_SHORT"          # a span this short annualises to nonsense
RATE_NOT_POSITIVE = "NOT_POSITIVE"    # net <= 0; withdrawals, not contributions

# Below this the annualisation is arithmetic, not measurement: three weeks of
# ledger turns a 20,000 deposit into 350,000/yr. Half a year, stated here rather
# than buried in a conditional, because it is a judgement and not a fact.
MIN_SPAN_DAYS = 180


async def contribution_rate(session: AsyncSession, user: User, *, now=None) -> dict:
    """Deposits per year MEASURED from the ledger, or a reason there is none.

    Never invents a rate. The projection that consumes this shows a
    with-contributions future only when this returns `ok`, and names the reason
    when it does not -- an assumed savings rate on someone else's card is a
    forecast dressed as a measurement.

    **`None` is not zero, and that survives here.** A user who has never logged a
    deposit gets `NO_LEDGER`; a user whose deposits and withdrawals cancel to
    exactly 0 gets `NOT_POSITIVE` with `total_ils = 0.0`. Those are different
    facts and `total_contributed()` already refuses to conflate them.

    The span runs from the FIRST entry to NOW, not to the last entry: "you have
    averaged X/yr since you started" degrades sensibly when you stop depositing,
    whereas first-to-last would report a stale rate forever and would be
    undefined on a single row.

    TWO RATES ARE RETURNED AND ONLY ONE IS THE DEFAULT. `per_year` divides the
    whole ledger by the span, which is what the plan specified; but the first row
    is almost always opening capital rather than a contribution, so
    `per_year_excluding_first` is usually the honest forward-looking figure and
    is always the smaller one. Picking between them is the user's call and the
    payload says so rather than choosing silently.
    """
    from datetime import datetime, timezone

    total = await total_contributed(session, user)
    res = await session.execute(
        select(Contribution.amount_ils, Contribution.occurred_at)
        .where(Contribution.subject == user.email)
        .order_by(Contribution.occurred_at.asc()))
    rows = res.all()

    out: dict = {"ok": False, "total_ils": total, "rows": len(rows),
                 "per_year": None, "per_year_excluding_first": None,
                 "first_at": None, "last_at": None, "elapsed_years": None}
    if total is None or not rows:
        return {**out, "reason": RATE_NO_LEDGER,
                "detail": "no deposits have ever been logged, so there is no rate to "
                          "measure. That is not the same as a rate of zero."}
    first_at, last_at = rows[0][1], rows[-1][1]
    out["first_at"] = first_at.isoformat() if first_at else None
    out["last_at"] = last_at.isoformat() if last_at else None
    if len(rows) < 2:
        return {**out, "reason": RATE_SINGLE_ENTRY,
                "detail": "one entry is an opening balance. Annualising it would "
                          "report your starting capital as a recurring deposit."}
    if total <= 0:
        return {**out, "reason": RATE_NOT_POSITIVE,
                "detail": f"the ledger nets to {total:,.2f}, so there is no positive "
                          f"contribution rate to project. Pass one explicitly if you "
                          f"want to see a future with deposits in it."}
    if first_at is None:
        return {**out, "reason": RATE_TOO_SHORT,
                "detail": "the first entry has no date, so no span can be measured."}
    ref = now or datetime.now(timezone.utc)
    if first_at.tzinfo is None:
        first_at = first_at.replace(tzinfo=timezone.utc)
    days = (ref - first_at).total_seconds() / 86400.0
    out["elapsed_years"] = round(days / 365.25, 3)
    if days < MIN_SPAN_DAYS:
        return {**out, "reason": RATE_TOO_SHORT,
                "detail": f"the ledger spans {days:.0f} days. Under {MIN_SPAN_DAYS} the "
                          f"annualised figure is arithmetic rather than measurement."}
    years = days / 365.25
    first_amt = float(rows[0][0] or 0.0)
    ex_first = max(0.0, total - first_amt) / years
    return {**out, "ok": True, "reason": None,
            "per_year": round(total / years, 2),
            "per_year_excluding_first": round(ex_first, 2),
            "detail": (f"{total:,.0f} over {years:.1f} years since your first entry. "
                       f"Excluding that first entry ({first_amt:,.0f}), which is usually "
                       f"opening capital rather than a contribution, the rate is "
                       f"{ex_first:,.0f}/yr.")}


async def list_contributions(session: AsyncSession, user: User) -> list[dict]:
    res = await session.execute(
        select(Contribution).where(Contribution.subject == user.email)
        .order_by(Contribution.occurred_at.desc()))
    return [{"id": str(c.id), "amount_ils": float(c.amount_ils), "kind": c.kind,
             "occurred_at": c.occurred_at.isoformat() if c.occurred_at else None,
             "note": c.note} for c in res.scalars().all()]


async def set_contributed(session: AsyncSession, user: User, amount_ils: float) -> float:
    """Replace the ledger with a single opening balance.

    For the common case: you know you have put in ₪20,000 in total and do not
    want to reconstruct every historical transfer.
    """
    amount_ils = round(float(amount_ils or 0.0), 2)
    existing = await session.execute(
        select(Contribution).where(Contribution.subject == user.email))
    for row in existing.scalars().all():
        await session.delete(row)
    session.add(Contribution(
        subject=user.email, amount_ils=Decimal(str(amount_ils)),
        kind="deposit" if amount_ils >= 0 else "withdrawal", note="opening balance"))
    await session.commit()
    return amount_ils
