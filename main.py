"""
Azure Healthcare Workspace Emulator

A Docker-based local emulator for Azure Health Data Services DICOM Service.
Drop-in replacement for development and testing — like Azurite for Storage,
but for Healthcare DICOM Service.

MIT License - KostLabs
"""

import asyncio
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import DICOM_PARSE_WORKERS, EVENT_DRAIN_TIMEOUT_SECONDS, EXPIRY_INTERVAL_SECONDS
from app.config import DICOM_STORAGE_DIR as _DICOM_STORAGE_DIR_STR
from app.database import AsyncSessionLocal, Base, engine
from app.dependencies import set_event_manager
from app.routers import changefeed, debug, extended_query_tags, operations, qido, stow, ups, wado
from app.routers import delete as delete_router
from app.services.events import EventManager, load_providers_from_config
from app.services.events.outbox import sweep_unpublished_events
from app.services.expiry import delete_expired_studies

logger = logging.getLogger(__name__)
DICOM_STORAGE_DIR = Path(_DICOM_STORAGE_DIR_STR)


async def expiry_cleanup_task():
    """Background task that runs every hour to delete expired studies."""
    while True:
        try:
            await asyncio.sleep(EXPIRY_INTERVAL_SECONDS)
            async with AsyncSessionLocal() as db:
                deleted_count = await delete_expired_studies(db, DICOM_STORAGE_DIR)
                if deleted_count > 0:
                    logger.info(f"Expiry cleanup: deleted {deleted_count} studies")
        except Exception as e:
            logger.error(f"Expiry cleanup task error: {e}")


# Arbitrary 64-bit constant identifying the schema-init advisory lock.  Any
# stable value works; concurrent boots just need to converge on the same key.
_SCHEMA_INIT_LOCK_KEY = 7281934650121111111


async def initialize_schema():
    """Bring the database schema up to date, safely under concurrent boots.

    Multi-worker uvicorn starts ``lifespan()`` once per worker against the
    same database, and ``CREATE EXTENSION IF NOT EXISTS`` /
    ``CREATE TABLE IF NOT EXISTS`` are **not** concurrent-safe in
    PostgreSQL: two callers can both pass the existence check, then both
    try to insert into the catalog and the loser raises a unique-constraint
    violation (e.g. ``pg_extension_name_index`` for the extension,
    ``pg_class_relname_nsp_index`` for tables/indexes).

    On PostgreSQL we serialize the DDL with a transaction-scoped advisory
    lock (`pg_advisory_xact_lock`).  The lock is released at ``COMMIT``,
    which keeps this safe under PgBouncer transaction-mode pooling — the
    transaction-scoped variant doesn't depend on the same backend session
    surviving across statements the way `pg_advisory_lock` does.

    Extension creation and ``Base.metadata.create_all`` run in the same
    transaction so the GIN/trigram index on ``patient_name`` (which depends
    on ``pg_trgm``) sees the extension as already installed.

    Other dialects (SQLite under tests) don't have ``pg_trgm`` and don't
    suffer the same race, so we just create the tables.
    """
    async with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            await conn.execute(text(f"SELECT pg_advisory_xact_lock({_SCHEMA_INIT_LOCK_KEY})"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables and initialize app-level singletons on startup."""
    await initialize_schema()

    # Initialize event providers and register with dependency injection
    providers = load_providers_from_config()
    event_manager = EventManager(providers)
    set_event_manager(event_manager)

    # Background-publish task tracking.  Stored on app.state so route
    # handlers (and tests) can register fire-and-forget event tasks
    # without leaking references — task.add_done_callback removes them
    # from the set as they complete.
    pending_event_tasks: set[asyncio.Task] = set()
    app.state.pending_event_tasks = pending_event_tasks

    # Boot the DICOM parse process pool used by stow_rs to keep pydicom
    # work off the event loop.  ``spawn`` is portable across platforms
    # and avoids fork-with-asyncio pitfalls; the per-worker import cost
    # is paid once on first use.  ``DICOM_PARSE_WORKERS=0`` disables the
    # pool (used by unit tests so they don't have to spin up subprocesses).
    if DICOM_PARSE_WORKERS > 0:
        ctx = multiprocessing.get_context("spawn")
        app.state.parse_pool = ProcessPoolExecutor(
            max_workers=DICOM_PARSE_WORKERS,
            mp_context=ctx,
        )
        logger.info("DICOM parse pool started with %d workers", DICOM_PARSE_WORKERS)
    else:
        app.state.parse_pool = None
        logger.info("DICOM parse pool disabled (DICOM_PARSE_WORKERS=0)")

    # Start background expiry cleanup task
    expiry_task = asyncio.create_task(expiry_cleanup_task())

    # Replay any change_feed rows that the previous process didn't get a
    # chance to publish (event_published_at IS NULL).  Runs as a one-shot
    # background task so the lifespan handshake doesn't block on it — if
    # the sweep takes a while on a busy table, the API can already accept
    # traffic while replay is still in flight.
    outbox_task = asyncio.create_task(sweep_unpublished_events(event_manager))
    app.state.outbox_sweeper = outbox_task

    try:
        yield
    finally:
        expiry_task.cancel()
        try:
            await expiry_task
        except asyncio.CancelledError:
            pass

        # Cancel the outbox sweeper if it's still running on shutdown —
        # whatever it didn't get to is picked up by the next boot.
        if not outbox_task.done():
            outbox_task.cancel()
            try:
                await outbox_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning("Outbox sweeper shutdown error: %s", e)

        # Drain pending background event publishes so a graceful container
        # stop has a chance to flush in-flight messages to the configured
        # providers.  Hard kills (SIGKILL / OOM) still drop these — that's
        # the documented trade-off for keeping events off the response path.
        pending = app.state.pending_event_tasks
        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=EVENT_DRAIN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Timed out after %.0fs draining %d pending event tasks",
                    EVENT_DRAIN_TIMEOUT_SECONDS,
                    len(pending),
                )

        if app.state.parse_pool is not None:
            app.state.parse_pool.shutdown(cancel_futures=True)
            app.state.parse_pool = None

        await event_manager.close()


app = FastAPI(
    title="Azure Healthcare Workspace Emulator",
    description=(
        "Local emulator for Azure Health Data Services DICOM Service (v2 API). "
        "Provides DICOMweb (STOW-RS, WADO-RS, QIDO-RS) plus Azure-specific APIs "
        "(Change Feed, Extended Query Tags, Operations)."
    ),
    version="0.4.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── DICOMweb Standard ──────────────────────────────────────────────
app.include_router(stow.router, prefix="/v2", tags=["STOW-RS"])
app.include_router(wado.router, prefix="/v2", tags=["WADO-RS"])
app.include_router(qido.router, prefix="/v2", tags=["QIDO-RS"])
app.include_router(delete_router.router, prefix="/v2", tags=["DELETE"])

# ── Azure Custom APIs ──────────────────────────────────────────────
app.include_router(changefeed.router, prefix="/v2", tags=["Change Feed"])
app.include_router(extended_query_tags.router, prefix="/v2", tags=["Extended Query Tags"])
app.include_router(operations.router, prefix="/v2", tags=["Operations"])

# ── UPS-RS Worklist Service ────────────────────────────────────────
app.include_router(ups.router, prefix="/v2", tags=["UPS-RS"])

# ── Debug Endpoints ────────────────────────────────────────────────
app.include_router(debug.router, tags=["Debug"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy", "service": "azure-healthcare-workspace-emulator"}
