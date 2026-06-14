"""Phase 3.1 - connect a brokerage/aggregator and sync holdings.

Read-only: pulls accounts + positions from the configured aggregator (mock by
default) and reconciles them into the user's portfolio via the existing intake
path, so synced holdings flow through the very same pipeline as manual entry.
Nothing here moves money; credentials are never persisted (only a reference).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.registry import get_aggregator
from app.core.config import get_settings
from app.models.tables import BrokerConnection, User
from app.schemas.intake import IntakePosition
from app.schemas.state_machine import Market
from app.services.intake_service import (
    ensure_account,
    ensure_entity,
    list_positions,
    upsert_positions,
)

_MARKETS = {m.value for m in Market}


def _to_intake(bp) -> IntakePosition:
    market = bp.market if bp.market in _MARKETS else "OTHER"
    price = bp.current_price or bp.cost_basis or 1.0
    return IntakePosition(
        ticker=bp.ticker, market=Market(market), depth=1,
        spot_price=max(price, 0.0001), listing_price=max(price, 0.0001),
        quantity=bp.quantity, cost_basis=bp.cost_basis,
        asset_class=bp.asset_class,
    )


async def connect(session: AsyncSession, user: User, *, provider: str | None = None,
                  access_ref: str = "sandbox-token") -> dict:
    s = get_settings()
    provider = (provider or s.aggregator_provider or "mock").lower()
    agg = get_aggregator(s if provider == s.aggregator_provider else s.model_copy(update={"aggregator_provider": provider}))
    accounts = agg.get_accounts(access_ref)
    acct = accounts[0] if accounts else None
    conn = BrokerConnection(
        user_id=user.id, provider=provider,
        external_account_id=acct.account_id if acct else None,
        institution=acct.institution if acct else None,
        status="CONNECTED",
        credential_ref=f"vault://{provider}/{user.id}",  # reference only - never the secret
    )
    session.add(conn)
    await session.flush()
    await session.commit()
    return {"connection_id": str(conn.id), "provider": provider,
            "institution": conn.institution, "accounts": [a.model_dump() for a in accounts],
            "status": conn.status}


async def sync(session: AsyncSession, user: User, *, connection_id: str | None = None) -> dict:
    s = get_settings()
    conn = None
    if connection_id:
        try:
            cid = uuid.UUID(str(connection_id))
        except (ValueError, TypeError):
            return {"ok": False, "error": "Invalid connection_id."}
        conn = (await session.execute(
            select(BrokerConnection).where(BrokerConnection.id == cid,
                                           BrokerConnection.user_id == user.id))).scalar_one_or_none()
    else:
        conn = (await session.execute(
            select(BrokerConnection).where(BrokerConnection.user_id == user.id)
            .order_by(BrokerConnection.created_at.desc()))).scalars().first()
    if conn is None:
        return {"ok": False, "error": "No brokerage connection; call /broker/connect first."}

    agg = get_aggregator(s if conn.provider == s.aggregator_provider
                         else s.model_copy(update={"aggregator_provider": conn.provider}))
    access_ref = conn.credential_ref or "sandbox-token"
    account_id = conn.external_account_id or (agg.get_accounts(access_ref)[0].account_id)
    broker_positions = agg.get_positions(access_ref, account_id)

    before = {p.ticker for p in await list_positions(session, user)}
    intake = [_to_intake(bp) for bp in broker_positions]
    entity = await ensure_entity(session, user, "Personal", "Personal")
    account = await ensure_account(session, entity, f"{conn.provider.title()} (synced)")
    await upsert_positions(session, account, intake)

    after = {p.ticker for p in await list_positions(session, user)}
    conn.last_synced_at = datetime.now(timezone.utc)
    conn.status = "CONNECTED"
    await session.flush()
    await session.commit()
    added = sorted(after - before)
    return {"ok": True, "provider": conn.provider, "account_id": account_id,
            "synced_positions": len(intake), "added": added,
            "updated": sorted({i.ticker for i in intake} - set(added)),
            "last_synced_at": conn.last_synced_at.isoformat()}
