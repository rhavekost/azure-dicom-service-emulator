"""Database configuration and session management."""

from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert as _pg_insert
from sqlalchemy.dialects.sqlite import insert as _sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.config import (
    DATABASE_URL,
    DB_MAX_OVERFLOW,
    DB_POOL_RECYCLE_SECONDS,
    DB_POOL_SIZE,
    DB_POOL_TIMEOUT_SECONDS,
)


def _make_engine():
    """Build the production async engine.

    Wrapped in a tiny factory so tests can assert the constructor contract
    (e.g., ``pool_pre_ping=True``) by monkeypatching ``create_async_engine``
    in this module rather than reaching into SQLAlchemy-internal pool
    attributes.

    Pool-size knobs are surfaced via env vars (see :mod:`app.config`).  The
    asyncpg pool defaults to ``pool_size=5, max_overflow=10`` which is the
    bottleneck the moment a STOW request and a parallel QIDO collide; we
    bump them so a single uvicorn worker can comfortably handle dozens of
    concurrent request slots without serialising on the pool.
    """
    return create_async_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=DB_POOL_SIZE,
        max_overflow=DB_MAX_OVERFLOW,
        pool_timeout=DB_POOL_TIMEOUT_SECONDS,
        pool_recycle=DB_POOL_RECYCLE_SECONDS,
    )


engine = _make_engine()
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


def dialect_insert(db: AsyncSession, table: Table):
    """Return the dialect-specific INSERT statement for the session's bind.

    Both PostgreSQL and SQLite (>= 3.24) support ``ON CONFLICT DO NOTHING``
    and ``ON CONFLICT DO UPDATE``; we pick the right dialect's insert so
    bulk paths (STOW) work transparently on production Postgres and on the
    aiosqlite test suite.
    """
    bind = db.get_bind()
    dialect_name = getattr(bind.dialect, "name", "postgresql") if bind is not None else "postgresql"
    if dialect_name == "sqlite":
        return _sqlite_insert(table)
    return _pg_insert(table)


# ── Bulk-INSERT chunking ───────────────────────────────────────────
# PostgreSQL caps a single statement at 32767 bind parameters (16-bit
# count in the wire protocol).  asyncpg surfaces overruns as
# ``InterfaceError: the number of query arguments cannot exceed 32767``.
# Multi-row INSERT/UPSERT call sites in this codebase (STOW POST/PUT,
# bulk-update change_feed) fan out one row's worth of bind parameters
# per ``VALUES (...)`` tuple, so a single statement with too many rows
# overflows the cap.  The helper below computes a chunk size that keeps
# every multi-row statement comfortably under the limit regardless of
# how many columns the row dict has.  ``PG_BIND_HEADROOM`` leaves a
# margin for any per-statement bind params SQLAlchemy may add (e.g.
# extra params attached by ``ON CONFLICT`` clauses on some dialects).
PG_MAX_BIND_PARAMS = 32767
PG_BIND_HEADROOM = 200


def bulk_chunk_size(rows: list) -> int:
    """Return a chunk size that keeps a multi-row INSERT under PG's bind cap.

    Parameters per chunk = ``len(rows[0]) * chunk_size``.  We compute the
    largest chunk that fits inside ``PG_MAX_BIND_PARAMS - PG_BIND_HEADROOM``
    so all bulk-INSERT call sites share the same calculation regardless of
    row width (e.g. 22 cols for ``dicom_instances``, 7 cols for
    ``change_feed``).

    Returns ``1`` for an empty input — the caller should already short-
    circuit on emptiness, but we never want to divide by zero here.
    """
    if not rows:
        return 1
    cols_per_row = max(1, len(rows[0]))
    return max(1, (PG_MAX_BIND_PARAMS - PG_BIND_HEADROOM) // cols_per_row)


def outbox_session_factory() -> AsyncSession:
    """Return a fresh ``AsyncSession`` for tasks that outlive a request.

    The request's session is closed by FastAPI's dependency-injection
    cleanup before background tasks run, so anything that needs to write
    to the DB after the response has been sent (e.g. the outbox publisher
    stamping ``change_feed.event_published_at``) must use a session it
    creates itself.  Always pair with ``async with`` so the connection is
    released when the task finishes.
    """
    return AsyncSessionLocal()


async def get_db():
    """Dependency for FastAPI routes to get database session.

    Rolls back the session on any unhandled exception (including asyncio.CancelledError
    from client disconnects) so the underlying connection is returned to the pool
    clean — avoids PendingRollbackError poisoning for subsequent requests that pick
    up the same pooled connection.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
