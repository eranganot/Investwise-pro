"""Persistence helpers for decision feeds (Section 4 DB schema)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.tables import User
from app.schemas.output_contract import ExpectedImpact, Recommendation, TradeOffs
from app.schemas.state_machine import DisplayedItem

SUPERADMIN_EMAIL = "eran.ganot@gmail.com"

_FACTOR_TO_INT = {1.0: 1, 1.25: 2, 1.5: 3, 1.75: 4, 2.0: 5}


def _complexity_int(factor: float) -> int:
    return _FACTOR_TO_INT.get(round(factor, 2), 3)


async def ensure_user(session: AsyncSession, email: str, *, name: str | None = None,
                      role: str = "SuperAdmin") -> User:
    res = await session.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if user is None:
        user = User(email=email, name=name or email, role=role,
                    tax_year=get_settings().tax_year)
        session.add(user)
        await session.flush()
    return user


async def ensure_superadmin(session: AsyncSession) -> User:
    return await ensure_user(session, SUPERADMIN_EMAIL,
                             name=get_settings().superadmin_name, role="SuperAdmin")


def build_recommendation(item: DisplayedItem) -> Recommendation:
    """Map a DISPLAYED lifecycle item to the Section 7 output contract."""
    ranked = item.source
    optimized = ranked.source
    vetted = optimized.source
    detected = vetted.source

    if ranked.urgency >= 70:
        time_sensitivity = "Now"
    elif ranked.urgency >= 40:
        time_sensitivity = "This Week"
    else:
        time_sensitivity = "Monitor"

    return Recommendation(
        title=item.title,
        action_type=detected.action_type.value,
        trigger=detected.trigger,
        execution_plan=f"{detected.action_type.value} {detected.ticker} on {detected.market.value}",
        expected_impact=ExpectedImpact(
            roi_delta=detected.expected_return_pct,
            risk_reduction=(round(vetted.max_drawdown * 100, 2)
                            if vetted.max_drawdown is not None else None),
            tax_impact=optimized.tax_saved,
        ),
        impact_score=round(ranked.impact_score, 2),
        confidence=round(ranked.confidence, 2),
        confidence_breakdown=ranked.confidence_breakdown,
        urgency=ranked.urgency,
        complexity=_complexity_int(ranked.complexity_factor),
        time_sensitivity=time_sensitivity,
        trade_offs=TradeOffs(
            gains=f"{item.path} path - impact {round(ranked.impact_score, 1)}, "
                  f"R {ranked.scores.ret:.0f} / T {ranked.scores.tax:.0f} / "
                  f"Risk {ranked.scores.risk:.0f} / Liq {ranked.scores.liquidity:.0f} / "
                  f"Conv {ranked.scores.conviction:.0f}",
            risks=vetted.risk_critique,
        ),
        risk_critique=vetted.risk_critique,
    )
