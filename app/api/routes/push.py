"""Web Push notification endpoints (PWA)."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.auth import Principal, Role, require_role
from app.core.database import get_session
from app.models.tables import User
from app.services import push_service

router = APIRouter(prefix="/api/v1/push", tags=["push"])


@router.get("/public-key")
async def public_key(session: AsyncSession = Depends(get_session)) -> dict:
    """VAPID applicationServerKey for the browser to subscribe with."""
    return {"public_key": await push_service.public_key(session)}


class SubscribeRequest(BaseModel):
    endpoint: str
    keys: dict


@router.post("/subscribe")
async def subscribe(req: SubscribeRequest, request: Request,
                    session: AsyncSession = Depends(get_session),
                    user: User = Depends(acting_user)) -> dict:
    ua = request.headers.get("user-agent", "")[:255]
    await push_service.save_subscription(session, user.email, req.model_dump(), ua)
    return {"ok": True}


class UnsubscribeRequest(BaseModel):
    endpoint: str


@router.post("/unsubscribe")
async def unsubscribe(req: UnsubscribeRequest,
                      session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    await push_service.delete_subscription(session, req.endpoint)
    return {"ok": True}


@router.post("/test")
async def test(session: AsyncSession = Depends(get_session),
               user: User = Depends(acting_user)) -> dict:
    sent = await push_service.send_test(session, user.email)
    return {"ok": True, "sent": sent}


@router.post("/check")
async def check(session: AsyncSession = Depends(get_session),
                user: User = Depends(acting_user)) -> dict:
    """Evaluate triggers for the current user now (recs, alerts, price moves)."""
    return await push_service.evaluate_and_notify(session, user)


@router.post("/run-all")
async def run_all(kind: str = "evaluate",
                  session: AsyncSession = Depends(get_session),
                  principal: Principal = Depends(require_role(Role.SUPERADMIN))) -> dict:
    """Fan out to every subscriber. For an external cron/scheduled task.
    kind=evaluate (alerts/recs/price moves) or kind=digest."""
    from app.services.feed_service import ensure_user
    from sqlalchemy import select
    from app.models.tables import PushSubscription

    subjects = list((await session.scalars(select(PushSubscription.subject).distinct())).all())
    total = 0
    for subj in subjects:
        user = await ensure_user(session, subj)
        await session.flush()
        if kind == "digest":
            total += (await push_service.send_digest(session, user)).get("sent", 0)
        else:
            total += (await push_service.evaluate_and_notify(session, user)).get("sent", 0)
    return {"subscribers": len(subjects), "sent": total, "kind": kind}
