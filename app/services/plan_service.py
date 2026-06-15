"""Planning / goals (tailors advice + health to the user's plan)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.tables import Plan, User

# Risk tolerance flexes the guardrails used by the health + advice layer.
RISK_CAPS = {
    "Low": {"concentration_cap": 0.15, "volatility_cap": 0.10, "ruin_probability_cap": 0.10},
    "Medium": {"concentration_cap": 0.25, "volatility_cap": 0.15, "ruin_probability_cap": 0.20},
    "High": {"concentration_cap": 0.40, "volatility_cap": 0.25, "ruin_probability_cap": 0.35},
}


async def get_plan(session: AsyncSession, user: User) -> Plan | None:
    return (await session.execute(select(Plan).where(Plan.user_id == user.id))).scalar_one_or_none()


async def upsert_plan(session: AsyncSession, user: User, **fields) -> Plan:
    plan = await get_plan(session, user)
    allowed = {"objective", "risk_tolerance", "horizon_years", "target_amount", "target_date", "currency",
               "target_roi_pct", "target_roi_period", "target_yield_pct", "target_yield_period", "preferred_depth", "strategy"}
    data = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if plan is None:
        plan = Plan(user_id=user.id, **data)
        session.add(plan)
    else:
        for k, v in data.items():
            setattr(plan, k, v)
    await session.flush()
    return plan


def effective_caps(plan: Plan | None) -> dict:
    if plan is None:
        s = get_settings()
        return {"concentration_cap": s.concentration_cap, "volatility_cap": s.volatility_cap,
                "ruin_probability_cap": s.ruin_probability_cap}
    return RISK_CAPS.get(plan.risk_tolerance, RISK_CAPS["Medium"])


def plan_settings(plan):
    """Settings flexed by the plan, so every engine/agent optimizes the goals."""
    base = get_settings()
    caps = effective_caps(plan)
    update = {"concentration_cap": caps["concentration_cap"],
              "volatility_cap": caps["volatility_cap"],
              "ruin_probability_cap": caps["ruin_probability_cap"]}
    if plan is not None and getattr(plan, "preferred_depth", None):
        update["preferred_depth"] = plan.preferred_depth
    if plan is not None and getattr(plan, "objective", None):
        update["objective"] = plan.objective
    return base.model_copy(update=update)
