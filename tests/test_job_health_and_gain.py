"""The app has to be able to say true things about money and time.

Three defects, each of which hid behind something that *looked* like a report:

* ``run_nav_snapshot_blocking`` imported a name that does not exist. The import
  is inside the function, so nothing at import time and no linter touched it,
  and **no test ever called the function**. It failed 7 nights out of 7 from the
  day it shipped. That is the whole lesson of this file: a job nothing invokes
  is a job nobody has tested, however many tests surround it.
* ``/api/v1/push/status`` computed its blockers from a hardcoded two job ids, so
  the dead job was not among the ones it could see, and the endpoint answered
  "Nothing obviously broken".
* ``/api/v1/portfolio`` reported one ``invested_ils`` that meant contributions
  when a ledger existed and cost basis otherwise -- so 7,388 of cost basis with
  no deposit behind it read as +42.48% on a book that had gained 4.0%.
"""
import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "AAA", "market": "TASE", "asset_class": "Equities", "depth": 1,
         "spot_price": 110, "listing_price": 110, "quantity": 100, "cost_basis": 100,
         "expected_return_pct": 7, "volatility_pct": 14},
    ]})


# --------------------------------------------------------------------------- #
# the job that was never called
# --------------------------------------------------------------------------- #
def test_the_nav_snapshot_job_actually_runs():
    """Invoke it. That is the entire point.

    `from app.core.database import async_session` raised ImportError on every
    one of the seven nights this ran in production -- the name is
    `AsyncSessionLocal`. One call would have caught it, and nothing called it.
    """
    from app.services.nav_history import run_nav_snapshot_blocking

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
        result = run_nav_snapshot_blocking()
    finally:
        logging.disable(logging.NOTSET)

    assert isinstance(result, dict), "the job must return its own outcome"
    assert set(result) >= {"nav_rows", "health_rows", "failed"}
    assert result["failed"] == 0, f"snapshot failed for {result['failed']} user(s)"
    assert result["nav_rows"] >= 1, "a book with holdings must produce a NAV row"


def test_the_snapshot_job_does_not_borrow_the_app_engine():
    """It runs in an APScheduler thread via asyncio.run().

    Borrowing `AsyncSessionLocal` there hands asyncpg a connection made in
    another loop, which it refuses -- and SQLite does not, so a rename alone
    would have gone green here and stayed dead in production. Pin the engine
    construction so the NullPool cannot be quietly removed.
    """
    import inspect

    from app.services import nav_history

    src = inspect.getsource(nav_history.run_nav_snapshot_blocking)
    # Code only -- the docstring names the old symbol on purpose, to explain it.
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith(("#", '"""', "*", "``"))
                     and "``" not in ln)
    assert "NullPool" in code, "the job must build its own NullPool engine"
    assert "create_async_engine" in code, "it must build the engine, not borrow one"
    assert "from app.core.database import" not in code, (
        "importing the app's shared engine here is the cross-loop asyncpg bug")


# --------------------------------------------------------------------------- #
# the monitor that could not see the dead job
# --------------------------------------------------------------------------- #
def test_status_names_any_failed_job_not_just_the_push_ones(monkeypatch):
    from app.worker import scheduler as sched_mod

    # push_service imports job_state inside the function, so patch it at source.
    monkeypatch.setattr(sched_mod, "job_state", lambda: {
        "scheduler_running": True,
        "jobs": [],
        "history": {
            "push_evaluate": {"runs": 100, "failures": 0, "last_ok": True},
            "push_digest": {"runs": 7, "failures": 0, "last_ok": True},
            "nav_snapshot": {"runs": 7, "failures": 7, "last_ok": False,
                             "last_error": "ImportError: cannot import name 'async_session'"},
        },
    })
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            body = c.get("/api/v1/push/status").json()
    finally:
        logging.disable(logging.NOTSET)

    joined = " | ".join(body["blockers"])
    assert "nav_snapshot" in joined, (
        f"a job at 7/7 failures must appear in blockers; got {body['blockers']}")
    assert "Nothing obviously broken" not in joined, (
        "the summary cannot claim health while a job is failing")
    assert "7 of 7" in joined, "say how many runs failed, not just that one did"


# --------------------------------------------------------------------------- #
# the gain that meant two different things
# --------------------------------------------------------------------------- #
def test_gain_is_reported_against_cost_basis_and_deposits_separately():
    """Basis 10,000, worth 11,000, but only 4,000 was ever deposited.

    Against basis that is +10%. Against contributions it is +175%. Both are
    true of different questions, and reporting only the second -- which is what
    `invested_ils` did -- turns 6,000 of unexplained basis into performance.
    """
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            c.post("/api/v1/portfolio/contributions",
                   json={"amount_ils": 4000, "kind": "deposit", "note": "opening"})
            p = c.get("/api/v1/portfolio").json()
    finally:
        logging.disable(logging.NOTSET)

    assert p["cost_basis_ils"] == pytest.approx(10000, abs=1)
    assert p["contributed_ils"] == pytest.approx(4000, abs=1)
    assert p["gain_vs_basis_ils"] == pytest.approx(1000, abs=1)
    assert p["gain_vs_basis_pct"] == pytest.approx(10.0, abs=0.1)
    # The gap is named rather than absorbed into the headline.
    assert p["unexplained_basis_ils"] == pytest.approx(6000, abs=1)
    # And the contributions view still exists, for the question it answers.
    assert p["gain_ils"] == pytest.approx(7000, abs=1)


def test_a_book_with_no_recorded_deposits_still_reports_a_basis_gain():
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            p = c.get("/api/v1/portfolio").json()
    finally:
        logging.disable(logging.NOTSET)

    assert p["contributed_ils"] is None
    assert p["unexplained_basis_ils"] is None, "no ledger means no gap to claim"
    assert p["gain_vs_basis_pct"] == pytest.approx(10.0, abs=0.1)


# --------------------------------------------------------------------------- #
# the constant that muted five card families until November
# --------------------------------------------------------------------------- #
def test_done_suppresses_for_a_fortnight_not_a_quarter():
    from app.services.recommendations import _DISMISS_TTL_DAYS, _DONE_TTL_DAYS

    assert _DONE_TTL_DAYS == 14
    assert _DONE_TTL_DAYS >= _DISMISS_TTL_DAYS, (
        "'done' is a stronger statement than 'ignore' and must not expire sooner")
