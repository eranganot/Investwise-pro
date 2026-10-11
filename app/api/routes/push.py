"""Web Push notification endpoints (PWA)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.auth import Principal, Role, require_role
from app.core.database import get_session
from app.models.tables import User
from app.services import push_service

router = APIRouter(prefix="/api/v1/push", tags=["push"])


@router.get("/digest/preview")
async def digest_preview(session: AsyncSession = Depends(get_session),
                         user: User = Depends(acting_user)) -> dict:
    """Build the digest and return it WITHOUT sending anything.

    Read-only: no push, no dedupe mark, no state written. It exists so the
    verification result is observable -- `verified: false` plus the offending
    figures in `unsupported_numbers` -- without waiting for 07:00 and without
    the only way to see the digest being to receive it on a phone.

    A `verified: false` here is the system WORKING: the model quoted a number
    it was not given and the deterministic text was substituted. It is still
    worth reading, because a number that SHOULD have been in the context is a
    gap in the context rather than a fault in the model.
    """
    from app.services.digest_service import build
    return await build(session, user)


@router.get("/public-key")
async def public_key(session: AsyncSession = Depends(get_session)) -> dict:
    """VAPID applicationServerKey for the browser to subscribe with."""
    return {"public_key": await push_service.public_key(session)}


class SubscribeRequest(BaseModel):
    endpoint: str
    keys: dict


@router.post("/subscribe", dependencies=[Depends(require_role(Role.ANALYST))])
async def subscribe(req: SubscribeRequest, request: Request,
                    session: AsyncSession = Depends(get_session),
                    user: User = Depends(acting_user),
                    principal: Principal = Depends(require_role(Role.ANALYST))) -> dict:
    ua = request.headers.get("user-agent", "")[:255]
    try:
        await push_service.save_subscription(session, user.email, req.model_dump(), ua,
                                             legacy_subjects=_legacy_subjects(principal))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"ok": True}


def _legacy_subjects(principal: Principal) -> tuple[str, ...]:
    """The owner may also act on rows saved in open mode under the superadmin NAME."""
    from app.core.config import get_settings
    return (get_settings().superadmin_name,) if principal.role == Role.SUPERADMIN else ()


class UnsubscribeRequest(BaseModel):
    endpoint: str


@router.post("/unsubscribe", dependencies=[Depends(require_role(Role.ANALYST))])
async def unsubscribe(req: UnsubscribeRequest,
                      session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user),
                      principal: Principal = Depends(require_role(Role.ANALYST))) -> dict:
    n = await push_service.delete_subscription(
        session, req.endpoint, (user.email, *_legacy_subjects(principal)))
    return {"ok": True, "removed": n}


@router.post("/test", dependencies=[Depends(require_role(Role.ANALYST))])
async def test(session: AsyncSession = Depends(get_session),
               user: User = Depends(acting_user)) -> dict:
    sent = await push_service.send_test(session, user.email)
    return {"ok": True, "sent": sent}


@router.get("/status")
async def status(session: AsyncSession = Depends(get_session),
                 user: User = Depends(acting_user)) -> dict:
    """Why notifications are (or aren't) arriving — subscriptions, scheduler
    job history, push-library health, and the last fan-out heartbeat."""
    return await push_service.diagnostics(session, user)


@router.post("/check", dependencies=[Depends(require_role(Role.ANALYST))])
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
