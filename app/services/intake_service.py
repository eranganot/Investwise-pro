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

from app.models.tables import Account, Entity, Position, User
from app.schemas.intake import IntakePosition
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
        if row is None:
            session.add(Position(
                account_id=account.id, ticker=ip.ticker, market=ip.market.value,
                quantity=Decimal(str(ip.quantity)), cost_basis=Decimal(str(ip.cost_basis)),
                current_price=Decimal(str(ip.listing_price)), lifecycle_stage="DETECTED",
                meta=meta,
            ))
        else:
            row.market = ip.market.value
            row.quantity = Decimal(str(ip.quantity))
            row.cost_basis = Decimal(str(ip.cost_basis))
            row.current_price = Decimal(str(ip.listing_price))
            row.meta = meta
    await session.flush()
    return len(positions)


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
    if ticker:
        row.ticker = ticker
    if market:
        row.market = market
    if quantity is not None:
        row.quantity = Decimal(str(quantity))
    if cost_basis is not None:
        row.cost_basis = Decimal(str(cost_basis))
    if current_price is not None:
        row.current_price = Decimal(str(current_price))
    if asset_class is not None:
        meta = dict(row.meta or {})
        meta["asset_class"] = asset_class
        row.meta = meta
    await session.commit()
    return row


# Cash is the most liquid thing you can hold; without an explicit score it fell
# back to the generic 70 and dragged the liquidity health score down.
CASH_META = {"asset_class": "Cash", "price_currency": "ILS", "liquidity_score": 100,
             "volatility_pct": 0.0}


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
