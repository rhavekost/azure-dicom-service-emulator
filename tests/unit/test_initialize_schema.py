"""
Unit tests for ``initialize_schema`` in main.py.

The function exists to make multi-worker uvicorn boots safe: each worker
runs ``lifespan()`` independently, and ``CREATE EXTENSION IF NOT EXISTS`` /
``CREATE TABLE IF NOT EXISTS`` are not concurrent-safe in PostgreSQL.
The fix takes a transaction-scoped advisory lock to serialize the DDL.

These tests verify the dialect-aware contract:

* On PostgreSQL we acquire ``pg_advisory_xact_lock`` and create
  ``pg_trgm`` before ``Base.metadata.create_all`` — and all of it
  happens inside a single ``engine.begin()`` transaction so the lock
  is held until commit.
* On other dialects (SQLite under tests) we skip the lock and the
  extension entirely and just create tables.

The race itself only manifests against a real Postgres backend, so this
suite only asserts the SQL contract; the e2e/integration suites cover
the live behavior.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _import_initialize_schema():
    """Import initialize_schema fresh to pick up any module-level patches."""
    from main import initialize_schema

    return initialize_schema


def _make_mock_engine(dialect_name: str):
    """Build a fake engine + connection that records ``execute`` / ``run_sync`` calls.

    Returns ``(engine, conn, executed_sql)`` where ``executed_sql`` is a
    list captured in call order so tests can assert sequencing.
    """
    executed_sql: list[str] = []
    run_sync_calls: list = []

    conn = AsyncMock()

    async def fake_execute(stmt):
        # SQLAlchemy ``text()`` clauses expose the literal string via ``.text``.
        executed_sql.append(getattr(stmt, "text", str(stmt)))

    async def fake_run_sync(fn):
        run_sync_calls.append(fn)

    conn.execute.side_effect = fake_execute
    conn.run_sync.side_effect = fake_run_sync

    begin_ctx = MagicMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=conn)
    begin_ctx.__aexit__ = AsyncMock(return_value=False)

    engine = MagicMock()
    engine.dialect.name = dialect_name
    engine.begin = MagicMock(return_value=begin_ctx)

    return engine, conn, executed_sql, run_sync_calls


@pytest.mark.asyncio
async def test_postgres_takes_advisory_lock_then_creates_extension_then_tables():
    """On PostgreSQL we lock first, then create the extension, then run create_all."""
    initialize_schema = _import_initialize_schema()
    fake_engine, conn, executed_sql, run_sync_calls = _make_mock_engine("postgresql")

    with patch("main.engine", fake_engine):
        await initialize_schema()

    # Exactly one transaction wraps the whole schema setup so the lock
    # survives until commit (rather than being dropped between statements).
    assert fake_engine.begin.call_count == 1

    # Lock first, extension second.
    assert len(executed_sql) == 2
    assert executed_sql[0].startswith("SELECT pg_advisory_xact_lock(")
    assert executed_sql[1] == "CREATE EXTENSION IF NOT EXISTS pg_trgm"

    # Then create_all runs inside the same transaction.  ``create_all`` is a
    # bound method, so attribute access returns a fresh wrapper each time —
    # compare the underlying function and bound instance instead of identity.
    assert len(run_sync_calls) == 1
    from app.database import Base

    assert run_sync_calls[0].__func__ is Base.metadata.create_all.__func__
    assert run_sync_calls[0].__self__ is Base.metadata


@pytest.mark.asyncio
async def test_sqlite_skips_advisory_lock_and_extension():
    """On SQLite (test path) we skip Postgres-only DDL and only create tables."""
    initialize_schema = _import_initialize_schema()
    fake_engine, conn, executed_sql, run_sync_calls = _make_mock_engine("sqlite")

    with patch("main.engine", fake_engine):
        await initialize_schema()

    assert fake_engine.begin.call_count == 1
    assert executed_sql == []
    assert len(run_sync_calls) == 1

    from app.database import Base

    assert run_sync_calls[0].__func__ is Base.metadata.create_all.__func__
    assert run_sync_calls[0].__self__ is Base.metadata


@pytest.mark.asyncio
async def test_postgres_lock_key_is_stable_across_calls():
    """The lock key must be a constant — concurrent boots have to converge on the same key."""
    initialize_schema = _import_initialize_schema()

    fake_engine_1, *_, executed_1, _ = _make_mock_engine("postgresql")
    with patch("main.engine", fake_engine_1):
        await initialize_schema()

    fake_engine_2, *_, executed_2, _ = _make_mock_engine("postgresql")
    with patch("main.engine", fake_engine_2):
        await initialize_schema()

    assert executed_1[0] == executed_2[0]
