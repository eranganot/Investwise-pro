"""Async task tests (Section AD) - run in eager mode (no Redis needed)."""
from app.worker import tasks
from app.worker.celery_app import celery_app


def test_celery_is_eager_without_redis():
    assert celery_app.conf.task_always_eager is True


def test_monte_carlo_task_runs_eager():
    r = tasks.monte_carlo_task.delay(8, 15, 11)
    assert r.ready()
    assert 0.0 <= r.result["probability_of_ruin"] <= 1.0
    assert r.result["runs"] == 10000


def test_simulation_task_runs_eager():
    r = tasks.simulation_task.delay(1_000_000, 8, 15, "year", 11)
    assert r.result["nominal"]["p50"] > 0


def test_whs_task_runs_eager():
    r = tasks.whs_task.delay(70, 80, 60, 90, 50)
    assert r.result["score"] == 70.5
