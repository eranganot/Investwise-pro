"""Async job enqueue + status (Section AD)."""
from celery.result import AsyncResult
from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings
from app.worker import tasks
from app.worker.celery_app import celery_app

router = APIRouter(prefix="/api/v1", tags=["jobs"])


def _resp(result) -> dict:
    if result.ready():
        return {"task_id": result.id, "status": "completed", "result": result.result}
    return {"task_id": result.id, "status": "queued"}


@router.get("/jobs")
async def jobs_info() -> dict:
    return {"async_enabled": bool(get_settings().redis_url),
            "mode": "redis" if get_settings().redis_url else "eager (synchronous)",
            "jobs": ["monte-carlo", "simulation", "whs"]}


class MCJob(BaseModel):
    expected_return: float = 8.0
    volatility: float = 15.0
    seed: int | None = None


@router.post("/jobs/monte-carlo")
async def enqueue_monte_carlo(req: MCJob) -> dict:
    return _resp(tasks.monte_carlo_task.delay(req.expected_return, req.volatility, req.seed))


class SimJob(BaseModel):
    initial_value: float = 1_000_000
    expected_return: float = 8.0
    volatility: float = 15.0
    horizon: str = "year"
    seed: int | None = None


@router.post("/jobs/simulation")
async def enqueue_simulation(req: SimJob) -> dict:
    return _resp(tasks.simulation_task.delay(
        req.initial_value, req.expected_return, req.volatility, req.horizon, req.seed))


@router.get("/jobs/{task_id}")
async def job_status(task_id: str) -> dict:
    r = AsyncResult(task_id, app=celery_app)
    return {"task_id": task_id, "status": r.status,
            "result": r.result if r.ready() else None}
