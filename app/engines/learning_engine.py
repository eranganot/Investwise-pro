"""LEARNING LOOP (Section 9).

Reads accepted/ignored history from user_actions and derives a lightweight
personalization profile: acceptance rate overall and per action type. The
profile nudges future Impact Scores (boost what the user accepts, demote what
they ignore) once there is enough signal.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import DecisionItem, UserAction

ACCEPT_BOOST = 1.10
IGNORE_PENALTY = 0.90
MIN_SAMPLES = 3


def _style(by_rate: dict[str, float], total: int) -> str:
    if total < MIN_SAMPLES:
        return "Learning - not enough actions yet to personalize."
    liked = [k for k, r in by_rate.items() if r >= 0.6]
    disliked = [k for k, r in by_rate.items() if r <= 0.4]
    bits = []
    if liked:
        bits.append("favors " + ", ".join(liked))
    if disliked:
        bits.append("tends to skip " + ", ".join(disliked))
    return "; ".join(bits) or "balanced across action types."


async def compute_profile(session: AsyncSession, user_id) -> dict:
    rows = (await session.execute(
        select(UserAction.action, DecisionItem.action_type)
        .join(DecisionItem, UserAction.decision_item_id == DecisionItem.id)
        .where(UserAction.user_id == user_id)
    )).all()
    total = len(rows)
    accepted = sum(1 for a, _ in rows if a == "accepted")
    agg: dict[str, dict[str, int]] = {}
    for action, atype in rows:
        d = agg.setdefault(atype, {"a": 0, "t": 0})
        d["t"] += 1
        d["a"] += 1 if action == "accepted" else 0
    by_rate = {k: round(v["a"] / v["t"], 3) for k, v in agg.items()}
    return {
        "total_actions": total,
        "accepted": accepted,
        "ignored": total - accepted,
        "acceptance_rate": round(accepted / total, 3) if total else None,
        "by_action_type": by_rate,
        "preferred_action_types": [k for k, r in by_rate.items() if r >= 0.6],
        "style": _style(by_rate, total),
    }


def impact_boost(profile: dict, action_type: str) -> float:
    if (profile.get("total_actions") or 0) < MIN_SAMPLES:
        return 1.0
    rate = profile.get("by_action_type", {}).get(action_type)
    if rate is None:
        return 1.0
    if rate >= 0.6:
        return ACCEPT_BOOST
    if rate <= 0.4:
        return IGNORE_PENALTY
    return 1.0
