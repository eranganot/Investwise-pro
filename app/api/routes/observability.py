"""Observability endpoints (review M3)."""
from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, Role, require_role
from app.core.database import get_session
from app.models.tables import AuditLog

router = APIRouter(tags=["observability"])


@router.get("/metrics")
async def metrics(principal: Principal = Depends(require_role(Role.READ_ONLY))) -> Response:
    """IW-5 (2026-11-05): was public; it exposes process and request data (RSS,
    route timings). Any signed-in role or the read-only smoke key can read it."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/api/v1/audit")
async def audit_entries(limit: int = 50, session: AsyncSession = Depends(get_session),
                        principal: Principal = Depends(require_role(Role.SUPERADMIN))) -> dict:
    rows = (await session.execute(
        select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit)
    )).scalars().all()
    return {"count": len(rows), "entries": [{
        "ts": r.created_at.isoformat() if r.created_at else None,
        "method": r.method, "route": r.route, "origin_ip": r.origin_ip,
        "role": r.role, "payload_sha256": r.payload_sha256,
    } for r in rows]}
