"""
Centralized configuration for the Azure Healthcare Workspace Emulator.

All environment variable reads live here. Import from this module rather than
calling os.getenv() directly in application code.
"""

import os

# Path where DICOM DCM files are stored on the filesystem.
DICOM_STORAGE_DIR: str = os.getenv("DICOM_STORAGE_DIR", "/data/dicom")

# PostgreSQL async connection string used by SQLAlchemy.
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://emulator:emulator@postgres:5432/dicom_emulator",
)

# ── SQLAlchemy connection pool sizing ──────────────────────────────
#
# The async engine defaults to ``pool_size=5, max_overflow=10``.  That is
# the bottleneck the moment a STOW request and a parallel QIDO collide —
# every checkout past ten waits.  These knobs let an operator size the
# pool to the host's CPU/memory budget without rebuilding the image.
DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "20"))
DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "20"))
DB_POOL_TIMEOUT_SECONDS: float = float(os.getenv("DB_POOL_TIMEOUT_SECONDS", "30"))
DB_POOL_RECYCLE_SECONDS: int = int(os.getenv("DB_POOL_RECYCLE_SECONDS", "1800"))

# How often (in seconds) the background expiry cleanup task runs.
EXPIRY_INTERVAL_SECONDS: int = int(os.getenv("EXPIRY_INTERVAL_SECONDS", "3600"))


# ── DICOM parse worker pool ────────────────────────────────────────
#
# A ProcessPoolExecutor offloads pydicom parsing + DICOM JSON conversion
# off the asyncio event loop so a worker can keep draining the request
# socket while another core handles parse work.  See
# app/services/dicom_parse_worker.py for the worker function.

# Number of subprocesses in the parse pool.  ``0`` disables the pool
# entirely and falls back to inline parsing on the main thread (used by
# unit tests so they don't have to spin up subprocesses).  When unset
# we use ``os.cpu_count()`` (clamped to at least 1).
def _default_parse_workers() -> int:
    cpu = os.cpu_count() or 1
    return max(1, cpu)


DICOM_PARSE_WORKERS: int = int(os.getenv("DICOM_PARSE_WORKERS", str(_default_parse_workers())))

# Maximum number of parts allowed to be in flight in the parse pool
# concurrently per STOW request.  Bounds peak memory: at this many
# in-flight parts × the size of one DICOM instance.  Defaults to twice
# the worker count so the pool stays saturated without unbounded queue
# growth.
DICOM_PARSE_INFLIGHT: int = int(
    os.getenv("DICOM_PARSE_INFLIGHT", str(max(2, DICOM_PARSE_WORKERS * 2)))
)


# ── Background event publishing ────────────────────────────────────
#
# Event publishing is fire-and-forget: STOW returns to the client as
# soon as the DB transaction commits, then a tracked asyncio.Task pushes
# events to the configured providers in the background.  This drain
# timeout bounds how long lifespan() shutdown waits for in-flight
# publishes before letting the worker exit.
EVENT_DRAIN_TIMEOUT_SECONDS: float = float(os.getenv("EVENT_DRAIN_TIMEOUT_SECONDS", "30"))


# EVENT_PROVIDERS — JSON-encoded event provider configuration (optional).
# This is intentionally read fresh on each call inside
# app/services/events/config.load_providers_from_config() so that tests can
# override it via monkeypatch.setenv without reloading modules.
# See app/services/events/config.py for the expected JSON schema.

# Bound concurrency for AzureStorageQueueProvider.publish_batch.  The Azure
# SDK's send_message is synchronous so we hop through asyncio.to_thread —
# without a semaphore a 5k-event batch would submit 5k tasks into the
# default thread pool and starve every other to_thread caller (aiofiles,
# parse-pool dispatch results, etc.).  16 leaves headroom for the rest of
# the app while still parallelising N TLS handshakes worth of work.
EVENT_AZURE_QUEUE_BATCH_CONCURRENCY: int = int(
    os.getenv("EVENT_AZURE_QUEUE_BATCH_CONCURRENCY", "16")
)


# ── Outbox sweeper (event replay on startup) ────────────────────────
#
# The fire-and-forget event publisher stamps ``change_feed.event_published_at``
# on success.  A hard kill between commit and stamp loses that publish; on
# next startup the sweeper picks up the un-stamped rows and replays them.
# The page size keeps the partial-index scan cheap.  ``max_age`` caps how
# far back the sweeper looks — older rows are assumed to have been backfilled
# via the change-feed API and don't need re-publishing.
OUTBOX_SWEEP_PAGE_SIZE: int = int(os.getenv("OUTBOX_SWEEP_PAGE_SIZE", "500"))
OUTBOX_SWEEP_MAX_AGE_SECONDS: int = int(os.getenv("OUTBOX_SWEEP_MAX_AGE_SECONDS", "86400"))
