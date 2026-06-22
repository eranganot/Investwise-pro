"""Decision-feed orchestration (review M5) - keeps the route thin.

Runs the full Lag -> Risk -> Tax -> Decision pipeline with the Allocation Agent
veto, Safety Layer, Adversary critique, XAI, learning personalization, expiry,
and persistence. Returns the JSON response payload.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import adversary
from app.agents.allocation_agent import AllocationAgent, VetoException
from app.core.config import get_settings
from app.engines.allocation_engine import AllocationEngine
from app.engines.decision_engine import DecisionEngine
from app.engines.lag_engine import LagEngine
from app.engines.learning_engine import compute_profile, impact_boost
from app.engines.risk_engine import RiskEngine
from app.engines.safety_engine import SafetyEngine
from app.engines.state_machine import StateMachine
from app.engines.tax_engine import TaxEngine
from app.engines.xai_engine import XaiEngine
from app.models.tables import DecisionFeed, DecisionItem, User
from app.schemas.feed import GenerateRequest
from app.schemas.state_machine import ActionType, DisplayedItem, VetoedSignal
from app.services.demo_data import DEFAULT_OBSERVATIONS
from app.services.feed_service import build_recommendation
from app.services.plan_service import get_plan, plan_settings
from app.services.intake_service import list_positions, position_to_observation

_TTL = {"Now": "rec_ttl_now_days", "This Week": "rec_ttl_week_days", "Monitor": "rec_ttl_monitor_days"}


def expiry_for(time_sensitivity: str) -> str:
    days = getattr(get_settings(), _TTL.get(time_sensitivity, "rec_ttl_week_days"))
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


class PipelineOrchestrator:
    def __init__(self) -> None:
        self.lag = LagEngine()
        self.sm = StateMachine(risk=RiskEngine(seed=7))
        self.safety = SafetyEngine()

    async def _resolve_observations(self, session: AsyncSession, user: User, req: GenerateRequest):
        if req.from_portfolio:
            positions = await list_positions(session, user, req.entity_name)
            obs = [o for p in positions if (o := position_to_observation(p)) is not None]
            return obs or DEFAULT_OBSERVATIONS
        return req.observations or DEFAULT_OBSERVATIONS

    def _veto_item(self, feed_id, signal, title, reason, extra=None) -> tuple[DecisionItem, dict]:
        payload = {"decision": "VETOED", "reason": reason}
        if extra:
            payload.update(extra)
        item = DecisionItem(
            feed_id=feed_id, title=title, action_type=signal.action_type.value,
            trigger=signal.trigger, impact_score=0.0, confidence=0.0, veto_flag=True,
            time_sensitivity="Monitor", risk_critique=reason, payload=payload)
        out = {"ticker": signal.ticker, "decision": "VETOED", "reason": reason,
               "adversary_critique": reason, "personalized_impact": -1}
        if extra and "safety_flags" in extra:
            out["safety_flags"] = extra["safety_flags"]
        return item, out

    async def generate(self, session: AsyncSession, user: User, req: GenerateRequest) -> dict:
        observations = await self._resolve_observations(session, user, req)
        ps = plan_settings(await get_plan(session, user))
        self.lag = LagEngine(ps)
        self.sm = StateMachine(risk=RiskEngine(ps, seed=7), tax=TaxEngine(ps),
                               decision=DecisionEngine(ps), settings=ps)
        self.safety = SafetyEngine(ps)
        profile = await compute_profile(session, user.id)
        feed = DecisionFeed(user_id=user.id, horizon="month", status="OPEN")
        session.add(feed)
        await session.flush()

        alloc_report = None
        alloc_agent = None
        if req.allocation:
            alloc_report = AllocationEngine().compute(
                target_allocation=req.allocation.target_allocation,
                current_allocation=req.allocation.current_allocation, nav=req.allocation.nav)
            alloc_agent = AllocationAgent()

        out: list[dict] = []
        for s in self.lag.scan(observations):
            exam = self.sm.cross_examine(s)
            result = exam.outcome
            adversary_notes = [n.model_dump() for n in exam.notes]

            if isinstance(result, DisplayedItem):
                ranked = result.source
                vetted = ranked.source.source

                if alloc_report is not None and req.asset_class_map and s.action_type == ActionType.BUY:
                    ac = req.asset_class_map.get(s.ticker)
                    if ac:
                        try:
                            alloc_agent.review_buy(ac, alloc_report)
                        except VetoException as ve:
                            item, o = self._veto_item(feed.id, s, result.title, ve.reason)
                            session.add(item)
                            out.append(o)
                            continue

                safety = None
                if req.portfolio:
                    safety = self.safety.check(
                        holdings=req.portfolio.holdings, liquidity_ratio=req.portfolio.liquidity_ratio,
                        proposals=[{"ticker": s.ticker, "action": s.action_type.value,
                                    "weight_delta": 0.05, "risk_score": ranked.scores.risk}])

                if adversary.should_veto(safety):
                    reason = "VETO (safety): " + "; ".join(f.detail for f in safety.flags)
                    item, o = self._veto_item(feed.id, s, result.title, reason,
                                              {"safety_flags": [f.model_dump() for f in safety.flags]})
                    session.add(item)
                    out.append(o)
                    continue

                crit = adversary.critique(path=result.path, risk_critique=vetted.risk_critique,
                                          confidence=ranked.confidence, impact=ranked.impact_score,
                                          safety=safety)
                personalized = round(ranked.impact_score * impact_boost(profile, s.action_type.value), 2)
                rec = build_recommendation(result)
                payload = rec.model_dump()
                payload["adversary_critique"] = crit
                payload["explanation"] = XaiEngine().build(result).model_dump()
                payload["adversary_examination"] = adversary_notes
                payload["adversary_narrative"] = self.sm.adversary.narrate(exam.notes, context=rec.title)
                payload["expires_at"] = expiry_for(rec.time_sensitivity)
                if safety:
                    payload["safety_flags"] = [f.model_dump() for f in safety.flags]
                session.add(DecisionItem(
                    feed_id=feed.id, title=rec.title, action_type=rec.action_type,
                    trigger=rec.trigger, execution_plan=rec.execution_plan,
                    impact_score=rec.impact_score, confidence=rec.confidence,
                    urgency=rec.urgency, complexity=rec.complexity,
                    time_sensitivity=rec.time_sensitivity, veto_flag=False,
                    risk_critique=crit, payload=payload))
                out.append({"ticker": s.ticker, "title": rec.title, "path": result.path,
                            "impact_score": rec.impact_score, "personalized_impact": personalized,
                            "time_sensitivity": rec.time_sensitivity, "adversary_critique": crit,
                            "safety_flags": [f.model_dump() for f in safety.flags] if safety else []})

            elif isinstance(result, VetoedSignal):
                item, o = self._veto_item(feed.id, s, f"{s.action_type.value} {s.ticker}", result.reason)
                session.add(item)
                out.append(o)

        await session.commit()
        out.sort(key=lambda x: x.get("personalized_impact", -1), reverse=True)
        return {
            "feed_id": str(feed.id), "user": user.email, "entity": req.entity_name,
            "persisted_items": len(out),
            "personalization": {"applied": (profile.get("total_actions") or 0) >= 3, "profile": profile},
            "allocation": alloc_report.model_dump() if alloc_report else None,
            "items": out,
        }
