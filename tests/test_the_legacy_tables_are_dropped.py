"""PR 114b: migration 0029 drops the legacy tables and `positions.bucket_id` (D76).

Runs the real `alembic` in a subprocess against a scratch SQLite file, the way
`test_the_migration_chain_runs_from_nothing` does: up to head, DOWN one (which
recreates the legacy tables empty, as production had them before 0029), a row
into a legacy table, then up again -- the drop must take the table, the row and
the column, and leave the live schema alone.
"""
import sqlite3

from tests.test_the_migration_chain_runs_from_nothing import _alembic

LEGACY = {"user_actions", "decision_items", "decision_feeds", "broker_connections", "tax_profiles", "buckets"}


def _schema(db):
    con = sqlite3.connect(db)
    try:
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        cols = {r[1] for r in con.execute("pragma table_info(positions)")}
        return tables, cols
    finally:
        con.close()


def test_0029_drops_the_legacy_tables_and_round_trips(tmp_path):
    db = tmp_path / "legacy.db"
    url = f"sqlite+aiosqlite:///{db}"
    assert _alembic(["upgrade", "head"], url).returncode == 0
    tables, cols = _schema(db)
    assert not (LEGACY & tables) and "bucket_id" not in cols      # a fresh chain never has them

    down = _alembic(["downgrade", "-1"], url)
    assert down.returncode == 0, down.stderr[-2000:]
    tables, cols = _schema(db)
    assert LEGACY <= tables and "bucket_id" in cols, (tables, cols)   # production's shape before 0029

    con = sqlite3.connect(db)
    con.execute("insert into decision_feeds (id, horizon, status) values ('00000000000000000000000000000001', 'month', 'OPEN')")
    con.commit()
    con.close()

    up = _alembic(["upgrade", "head"], url)
    assert up.returncode == 0, up.stderr[-2000:]
    tables, cols = _schema(db)
    assert not (LEGACY & tables), LEGACY & tables
    assert "bucket_id" not in cols and {"ticker", "quantity", "account_id"} <= cols
    assert {"positions", "users", "trading_rules", "revoked_tokens"} <= tables
