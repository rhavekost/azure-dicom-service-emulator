"""
Azure Healthcare Workspace Emulator

A Docker-based local emulator for Azure Health Data Services DICOM Service.
Drop-in replacement for development and testing — like Azurite for Storage,
but for Healthcare DICOM Service.

MIT License - KostLabs
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import DICOM_STORAGE_DIR as _DICOM_STORAGE_DIR_STR
from app.config import EXPIRY_INTERVAL_SECONDS
from app.database import AsyncSessionLocal, Base, engine
from app.dependencies import set_event_manager
from app.routers import changefeed, debug, extended_query_tags, operations, qido, stow, ups, wado
from app.routers import delete as delete_router
from app.services.events import EventManager, load_providers_from_config
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables and initialize event manager on startup."""
    # Enable pg_trgm extension for GIN-based fuzzy text search on patient_name
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    # Create database tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Initialize event providers and register with dependency injection
    providers = load_providers_from_config()
    event_manager = EventManager(providers)
    set_event_manager(event_manager)

    # Start background expiry cleanup task
    expiry_task = asyncio.create_task(expiry_cleanup_task())

    yield

    # Cleanup
    expiry_task.cancel()
    try:
        await expiry_task
    except asyncio.CancelledError:
        pass

    await event_manager.close()


app = FastAPI(
    title="Azure Healthcare Workspace Emulator",
    description=(
        "Local emulator for Azure Health Data Services DICOM Service (v2 API). "
        "Provides DICOMweb (STOW-RS, WADO-RS, QIDO-RS) plus Azure-specific APIs "
        "(Change Feed, Extended Query Tags, Operations)."
    ),
    version="0.3.1",
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
