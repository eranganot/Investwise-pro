"""Portfolio intake persistence (Section 5).

Persists entities -> accounts -> positions and rebuilds Lag observations from
stored positions so the pipeline can run on the real portfolio. Lag/risk inputs
(depth, spot/listing price, expected return, volatility, action) live in the
position's JSON `meta`.
"""
from __future__ import annotations

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
