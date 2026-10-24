"""Trading rules endpoints (stop-loss, take-profit, trailing stop, alerts)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.auth import Role, require_role
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
    params: dict | None = None     # PR 81: e.g. {"activation_pct": 20} on a profit-protect


@router.get("")
async def list_rules(session: AsyncSession = Depends(get_session),
                     user: User = Depends(acting_user)) -> dict:
    rules = await rules_service.list_rules(session, user)
    # PR 123 (D85): each rule's effect on the goal's chance, from the latest solve.
    from app.services.frontier_service import rule_impacts
    imp = await rule_impacts(session, user)
    for r in rules:
        r["goal_impact"] = (imp.get("rules") or {}).get(str(r.get("id")))
    return {"rules": rules, "types": sorted(rules_service.RULE_TYPES),
            "goal_impact": {k: imp.get(k) for k in ("computed_at", "fresh", "base_goal_pct")} if imp else None}


@router.get("/suggestions")
async def suggest_rules(session: AsyncSession = Depends(get_session),
                        user: User = Depends(acting_user)) -> dict:
    from app.services.protection_manager import filter_suggestions
    return {"suggestions": filter_suggestions(await rules_service.suggest_rules_for_holdings(session, user))}


@router.get("/events")
async def rule_events(limit: int = 50, session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    """Audit trail: every rule firing and what was done about it."""
    return {"events": await rules_service.list_events(session, user, limit=min(limit, 200))}


@router.post("", dependencies=[Depends(require_role(Role.ANALYST))])
async def create_rule(body: RuleIn, session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    try:
        r = await rules_service.create_rule(
            session, user, ticker=body.ticker, rule_type=body.rule_type,
            mode=body.mode, level=body.level, note=body.note, params=body.params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    warn = await rules_service.cap_below_plan(session, user, body.ticker, body.rule_type,
                                              body.level)
    return {"ok": True, "id": str(r.id), **(warn or {})}


class RuleEdit(BaseModel):
    level: float | None = None
    note: str | None = None
    params: dict | None = None
    force: bool = False      # save a cap below the plan target after the warning


@router.patch("/{rule_id}", dependencies=[Depends(require_role(Role.ANALYST))])
async def edit_rule(rule_id: str, body: RuleEdit, session: AsyncSession = Depends(get_session),
                    user: User = Depends(acting_user)) -> dict:
    """PR 79: change a rule's level and note in place. A max-weight cap below the
    plan's target returns `needs_confirm` with a suggested level; resend with
    `force: true` to save it anyway."""
    out = await rules_service.update_rule(session, user, rule_id, level=body.level,
                                          note=body.note, force=body.force,
                                          params=body.params)
    if not out.get("ok") and not out.get("needs_confirm"):
        raise HTTPException(status_code=404 if out.get("error") == "rule not found" else 400,
                            detail=out.get("error"))
    return out


@router.delete("/{rule_id}", dependencies=[Depends(require_role(Role.ANALYST))])
async def delete_rule(rule_id: str, session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    return {"ok": await rules_service.delete_rule(session, user, rule_id)}


@router.post("/{rule_id}/toggle", dependencies=[Depends(require_role(Role.ANALYST))])
async def toggle_rule(rule_id: str, session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    return {"ok": await rules_service.toggle_rule(session, user, rule_id)}


@router.post("/evaluate", dependencies=[Depends(require_role(Role.ANALYST))])
async def evaluate_now(session: AsyncSession = Depends(get_session),
                       user: User = Depends(acting_user)) -> dict:
    return {"triggered": await rules_service.evaluate_user(session, user, notify=True)}
