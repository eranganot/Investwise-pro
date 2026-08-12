"""One failing agent must not take the whole endpoint down.

Production returned 500 from /recommendations with:

    asyncpg.exceptions.InFailedSQLTransactionError: current transaction is
    aborted, commands ignored until end of transaction block

...while every local test was green. The defensive handlers in
build_recommendations catch an agent's failure, log it, mark the pipeline
degraded and carry on with the SAME session. SQLite tolerates that; Postgres
does not -- the first failed statement aborts the transaction and every later
query raises, so a non-critical agent failing becomes a 500.

These tests reproduce the shape on SQLite by asserting the handlers reset the
transaction, rather than relying on a Postgres-only symptom.
"""
import inspect
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi.testclient import TestClient

import app.main as m
from app.services import recommendations as recs


def test_every_db_touching_agent_runs_inside_a_savepoint():
    """The invariant, checked structurally.

    An agent that queries the database and is allowed to fail must be wrapped,
    or its failure aborts the transaction for everything after it. Checked on
    the source rather than by symptom, because the symptom only appears on
    Postgres and every SQLite test stays green.
    """
    src = inspect.getsource(recs.build_recommendations)
    for agent in ("_war_room_recs(session", "triggered_rule_recs(session",
                  "pending_signal_recs(session", "_perf_fn(session"):
        assert agent in src, f"{agent} vanished - has the pipeline changed shape?"
        before = src[:src.index(agent)]
        # The nearest enclosing try must open a savepoint.
        last_try = before.rindex("    try:")
        assert "_agent_tx(session)" in before[last_try:], (
            f"{agent} runs outside a savepoint: if it raises, every later query "
            f"in the request fails on Postgres with InFailedSQLTransactionError")


def test_a_savepoint_rolls_back_only_the_failed_agent():
    """The reason this is a savepoint and not session.rollback(): rollback
    expires every loaded ORM object, so the positions this endpoint keeps using
    would each trigger a lazy reload and raise MissingGreenlet instead."""
    import ast as _ast
    import textwrap

    fn = _ast.parse(textwrap.dedent(inspect.getsource(recs._agent_tx))).body[0]
    stmts = [n for n in fn.body if not isinstance(n, _ast.Expr)
             or not isinstance(n.value, _ast.Constant)]        # drop the docstring
    calls = [_ast.unparse(n) for n in stmts]
    assert any("begin_nested" in c for c in calls), calls
    assert not any("rollback" in c for c in calls), (
        "session.rollback() expires every loaded ORM object, so the positions "
        "this endpoint keeps using would raise MissingGreenlet on next access")


def test_recommendations_survives_an_agent_that_raises(monkeypatch):
    """End to end: break one agent and the endpoint must still answer."""
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "V", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
             "spot_price": 365, "listing_price": 365, "quantity": 10, "cost_basis": 300,
             "expected_return_pct": 8, "volatility_pct": 20}]})

        import app.services.strategy_signal_service as sigs

        async def _explode(*a, **k):
            raise RuntimeError("simulated agent failure")

        monkeypatch.setattr(sigs, "pending_signal_recs", _explode)
        r = c.get("/api/v1/recommendations")
        assert r.status_code == 200, "one broken agent must not 500 the endpoint"
        body = r.json()
        assert "strategy_signals" in (body.get("degraded") or [])
        # The rest of the pipeline still produced output and, crucially, the
        # queries that run AFTER the failure still worked.
        assert "count" in body and body.get("rule_banner") is not None
