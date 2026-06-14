"""Interactive What-If endpoint (Phase 2.2): sliders -> live re-evaluation."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.database import get_session
from app.models.tables import User
from app.services.whatif import run_whatif

router = APIRouter(prefix="/api/v1", tags=["whatif"])


class WhatIfRequest(BaseModel):
    risk_tolerance: str = "Medium"             # Low | Medium | High
    tlh_target_ils: float = Field(default=0.0, ge=0)
    expected_drawdown_pct: float = Field(default=20.0, ge=1, le=95)


@router.post("/whatif")
async def whatif(req: WhatIfRequest | None = None,
                 session: AsyncSession = Depends(get_session),
                 user: User = Depends(acting_user)) -> dict:
    req = req or WhatIfRequest()
    return await run_whatif(session, user, risk_tolerance=req.risk_tolerance,
                            tlh_target_ils=req.tlh_target_ils,
                            expected_drawdown_pct=req.expected_drawdown_pct)
