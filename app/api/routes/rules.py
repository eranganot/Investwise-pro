"""Trading rules endpoints (stop-loss, take-profit, trailing stop, alerts)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.database import get_session
from app.models.tables import User
from app.services import rules_service

router = APIRouter(prefix="/api/v1/rules", tags=["rules"])


class RuleIn(BaseModel):
    ticker: str
    rule_type: str       # stop_loss|take_profit|trailing_stop|price_above|price_below|buy_dip|max_weight
    mode: str = "pct"    # pct|price
    level: float
    note: str | None = None


@router.get("")
async def list_rules(session: AsyncSession = Depends(get_session),
                     user: User = Depends(acting_user)) -> dict:
    return {"rules": await rules_service.list_rules(session, user),
            "types": sorted(rules_service.RULE_TYPES)}


@router.get("/suggestions")
async def suggest_rules(session: AsyncSession = Depends(get_session),
                        user: User = Depends(acting_user)) -> dict:
    return {"suggestions": await rules_service.suggest_rules_for_holdings(session, user)}


@router.post("")
async def create_rule(body: RuleIn, session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    try:
        r = await rules_service.create_rule(
            session, user, ticker=body.ticker, rule_type=body.rule_type,
            mode=body.mode, level=body.level, note=body.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "id": str(r.id)}


@router.delete("/{rule_id}")
async def delete_rule(rule_id: str, session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    return {"ok": await rules_service.delete_rule(session, user, rule_id)}


@router.post("/{rule_id}/toggle")
async def toggle_rule(rule_id: str, session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    return {"ok": await rules_service.toggle_rule(session, user, rule_id)}


@router.post("/evaluate")
async def evaluate_now(session: AsyncSession = Depends(get_session),
                       user: User = Depends(acting_user)) -> dict:
    return {"triggered": await rules_service.evaluate_user(session, user, notify=True)}
