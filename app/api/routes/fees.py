"""Fee & expense-ratio optimizer endpoint (Phase 3.2)."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.fee_agent import FeeAgent
from app.api.deps import acting_user
from app.core.database import get_session
from app.models.tables import User
from app.services.intake_service import list_positions

router = APIRouter(prefix="/api/v1", tags=["fees"])


@router.get("/fees")
async def fees(session: AsyncSession = Depends(get_session),
               user: User = Depends(acting_user)) -> dict:
    rows = await list_positions(session, user)
    pdicts = [{"ticker": p.ticker, "asset_class": (p.meta or {}).get("asset_class"),
               "quantity": float(p.quantity), "current_price": float(p.current_price or 0),
               "expense_ratio_pct": (p.meta or {}).get("expense_ratio_pct")} for p in rows]
    report = FeeAgent().report(pdicts)
    return report.model_dump()
