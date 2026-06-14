"""Brokerage connect/sync endpoints (Phase 3.1 scaffold)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.brokers.base import NotConfiguredError
from app.core.database import get_session
from app.models.tables import User
from app.services.broker_sync import connect as connect_svc
from app.services.broker_sync import sync as sync_svc

router = APIRouter(prefix="/api/v1/broker", tags=["broker"])


class ConnectRequest(BaseModel):
    provider: str | None = None          # mock | plaid | yodlee (defaults to config)
    access_ref: str = "sandbox-token"


class SyncRequest(BaseModel):
    connection_id: str | None = None


@router.post("/connect")
async def broker_connect(req: ConnectRequest | None = None,
                         session: AsyncSession = Depends(get_session),
                         user: User = Depends(acting_user)) -> dict:
    req = req or ConnectRequest()
    try:
        return await connect_svc(session, user, provider=req.provider, access_ref=req.access_ref)
    except NotConfiguredError as e:
        return {"ok": False, "error": str(e), "hint": "Set BROKER_ENABLED=true and provider credentials."}


@router.post("/sync")
async def broker_sync(req: SyncRequest | None = None,
                      session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    req = req or SyncRequest()
    try:
        return await sync_svc(session, user, connection_id=req.connection_id)
    except NotConfiguredError as e:
        return {"ok": False, "error": str(e)}
