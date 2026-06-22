"""Async jobs (Section AD) - heavy compute kept off the HTTP request thread."""
from __future__ import annotations

from app.engines.risk_engine import RiskEngine
from app.engines.simulation_engine import SimulationEngine
from app.engines.whs_engine import WhsEngine
from app.worker.celery_app import celery_app


@celery_app.task(name="monte_carlo")
def monte_carlo_task(expected_return: float, volatility: float, seed: int | None = None) -> dict:
    return RiskEngine(seed=seed).monte_carlo(expected_return / 100.0, volatility / 100.0).model_dump()


@celery_app.task(name="simulation")
def simulation_task(initial_value: float, expected_return: float, volatility: float,
                    horizon: str = "year", seed: int | None = None) -> dict:
    return SimulationEngine(seed=seed).run(
        initial_value=initial_value, expected_return_pct=expected_return,
        volatility_pct=volatility, horizon=horizon).model_dump()


@celery_app.task(name="whs")
def whs_task(risk: float, tax: float, alloc: float, liq: float, thematic: float) -> dict:
    return WhsEngine().compute(risk=risk, tax=tax, alloc=alloc, liq=liq, thematic=thematic)
