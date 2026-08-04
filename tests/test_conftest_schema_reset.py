"""The sqlite test database must not survive a schema change.

`create_all` creates missing TABLES but never adds a column to a table that
already exists. So the moment a model gained a field, every test touching it
died with "no such column" -- on a schema the code was right about and a
leftover file was wrong about. It went red twice for this before the cause was
addressed rather than the symptom.
"""
import os
import sqlite3

from tests.conftest import _drop_stale_sqlite_file


def test_a_planted_sqlite_file_is_removed(tmp_path, monkeypatch):
    db = tmp_path / "iw_stale.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE strategy_backtests (id TEXT PRIMARY KEY)")   # missing columns
    conn.commit()
    conn.close()
    assert db.is_file()

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    _drop_stale_sqlite_file()
    assert not db.is_file(), "a stale schema would survive into the next run"


def test_a_postgres_url_is_never_touched(monkeypatch):
    """CI's test-postgres job gets its schema from migrations; deleting anything
    there would be both wrong and impossible."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
    _drop_stale_sqlite_file()          # must be a silent no-op


def test_an_in_memory_url_is_a_no_op(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    _drop_stale_sqlite_file()


def test_a_missing_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'never.db'}")
    _drop_stale_sqlite_file()
    assert not os.path.isfile(tmp_path / "never.db")
