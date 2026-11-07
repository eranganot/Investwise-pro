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
               "target_roi_pct", "target_roi_period", "target_yield_pct", "target_yield_period", "preferred_depth",
               "cash_target_pct", "cash_target_ils", "cash_redeploy_at_ils",
               # PR 91 / migration 0026: the frontier's dials.
               "kelly_fraction", "return_confidence",
               # PR 104c / migration 0027: the goal's two limits.
               "halve_limit_pct", "on_track_pct"}
    data = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if fields.get("preferred_depth") == 0:
        data["preferred_depth"] = None
    # ONE CASH TARGET AT A TIME: whichever unit was set last wins, and setting it
    # clears the other. Two live targets would leave every reader to guess.
    if "cash_redeploy_at_ils" in data:
        from decimal import Decimal
        data["cash_redeploy_at_ils"] = Decimal(str(round(float(data["cash_redeploy_at_ils"]), 2)))
    if "cash_target_ils" in data:
        from decimal import Decimal
        data["cash_target_ils"] = Decimal(str(round(float(data["cash_target_ils"]), 2)))
        data["cash_target_pct"] = None
    elif "cash_target_pct" in data:
        data["cash_target_ils"] = None
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
    row = dict(RISK_CAPS.get(plan.risk_tolerance, RISK_CAPS["Medium"]))
    # PR 115 (D-d): the largest single holding is a setting; the risk-tolerance
    # row is its default.
    from app.services import thresholds
    over = thresholds.overrides_of(plan).get("concentration_cap_pct")
    if over is not None:
        row["concentration_cap"] = float(over) / 100.0
    return row


def plan_settings(plan):
    """Settings flexed by the plan, so every engine/agent optimizes the goals."""
    base = get_settings()
    caps = effective_caps(plan)
    update = {"concentration_cap": caps["concentration_cap"],
              "volatility_cap": caps["volatility_cap"],
              "ruin_probability_cap": caps["ruin_probability_cap"]}
    if plan is not None and getattr(plan, "preferred_depth", None):
        update["preferred_depth"] = plan.preferred_depth
    # `update["objective"] = plan.objective` used to be here, and
    # `Settings.objective` carried the comment "the plan objective the agents
    # optimize toward". No agent ever read it. Every reader goes to
    # `plan.objective` directly -- recommendations.py:991/1178/3730,
    # strategy_service.py:515/630 -- and the only thing that touched the
    # settings copy was a test asserting it round-tripped. A field whose
    # comment describes behaviour it does not have is worse than no field: it
    # is a wiring diagram for a wire that was never run. PR 58.
    return base.model_copy(update=update)
