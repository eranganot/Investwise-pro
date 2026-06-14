"""Phase 2.2 - interactive 'What-If' re-evaluation.

Three sliders feed directly into the agents' initial state and the pipeline is
re-run with overridden guardrails - no persistence, pure recompute:

  * Risk Tolerance       -> volatility / ruin / concentration caps (the Risk Agent
                            vetoes more or fewer signals).
  * Expected Drawdown    -> the max-drawdown cap used for Probability-of-Ruin and a
                            deterministic scenario loss on the current NAV.
  * Tax-Loss Harvesting  -> the minimum annual tax saving that counts as a
    Target               qualifying harvest opportunity.

Returns the freshly re-evaluated risk profile so the UI can show, live, how the
recommendation set and risk picture move as the user drags a slider.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.engines.decision_engine import DecisionEngine
from app.engines.lag_engine import LagEngine
from app.engines.risk_engine import RiskEngine
from app.engines.state_machine import StateMachine
from app.engines.tax_engine import TaxEngine
from app.models.tables import User
from app.schemas.state_machine import DisplayedItem, VetoedSignal
from app.services.demo_data import DEFAULT_OBSERVATIONS
from app.services.intake_service import list_positions, position_to_observation
from app.services.plan_service import RISK_CAPS
from app.services.portfolio_analytics import compute_snapshot, tax_opportunities

RISK_LEVELS = tuple(RISK_CAPS)  # ("Low", "Medium", "High")


def overridden_settings(risk_tolerance: str, expected_drawdown_pct: float | None) -> Settings:
    caps = RISK_CAPS.get(risk_tolerance, RISK_CAPS["Medium"])
    upd = {
        "volatility_cap": caps["volatility_cap"],
        "ruin_probability_cap": caps["ruin_probability_cap"],
        "concentration_cap": caps["concentration_cap"],
    }
    if expected_drawdown_pct is not None:
        upd["max_drawdown_cap"] = max(0.01, min(0.95, expected_drawdown_pct / 100.0))
    return get_settings().model_copy(update=upd)


async def run_whatif(session: AsyncSession, user: User, *, risk_tolerance: str = "Medium",
                     tlh_target_ils: float = 0.0, expected_drawdown_pct: float = 20.0) -> dict:
    s = overridden_settings(risk_tolerance, expected_drawdown_pct)

    positions = await list_positions(session, user)
    obs = [o for p in positions if (o := position_to_observation(p)) is not None] or DEFAULT_OBSERVATIONS

    sm = StateMachine(risk=RiskEngine(s, seed=7), tax=TaxEngine(s), decision=DecisionEngine(s), settings=s)
    recommended: list[str] = []
    vetoed: list[str] = []
    for det in LagEngine(s).scan(obs):
        result = sm.run(det)
        if isinstance(result, DisplayedItem):
            recommended.append(result.title)
        elif isinstance(result, VetoedSignal):
            vetoed.append(det.ticker)

    pdicts = [{"ticker": p.ticker, "market": p.market, "quantity": float(p.quantity),
               "cost_basis": float(p.cost_basis), "current_price": float(p.current_price or 0),
               "volatility_pct": (p.meta or {}).get("volatility_pct"),
               "liquidity_score": (p.meta or {}).get("liquidity_score")} for p in positions]
    nav = compute_snapshot(pdicts)["nav"] if pdicts else 0.0
    drawdown = s.max_drawdown_cap
    projected_loss = round(nav * drawdown, 2)

    harvest = []
    if pdicts:
        harvest = [o for o in tax_opportunities(pdicts)["opportunities"]
                   if o["trigger"] == "CAPITAL_LOSS_HARVESTING"]
    qualifying = [o for o in harvest if o["estimated_annual_tax_savings_currency"] >= tlh_target_ils]

    return {
        "inputs": {"risk_tolerance": risk_tolerance, "tlh_target_ils": round(tlh_target_ils, 2),
                   "expected_drawdown_pct": round(drawdown * 100, 1)},
        "risk_profile": {
            "volatility_cap_pct": round(s.volatility_cap * 100, 1),
            "ruin_probability_cap_pct": round(s.ruin_probability_cap * 100, 1),
            "max_drawdown_cap_pct": round(s.max_drawdown_cap * 100, 1),
            "concentration_cap_pct": round(s.concentration_cap * 100, 1),
            "evaluated": len(recommended) + len(vetoed),
            "recommended": len(recommended), "vetoed": len(vetoed),
            "vetoed_tickers": vetoed, "recommended_titles": recommended,
        },
        "scenario": {"nav": round(nav, 2), "drawdown_pct": round(drawdown * 100, 1),
                     "projected_loss_ils": projected_loss},
        "tax_loss_harvesting": {
            "target_savings_ils": round(tlh_target_ils, 2),
            "qualifying_count": len(qualifying),
            "qualifying_savings_ils": round(sum(o["estimated_annual_tax_savings_currency"] for o in qualifying), 2),
        },
    }
