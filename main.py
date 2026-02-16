"""
Azure Healthcare Workspace Emulator

A Docker-based local emulator for Azure Health Data Services DICOM Service.
Drop-in replacement for development and testing — like Azurite for Storage,
but for Healthcare DICOM Service.

MIT License - KostLabs
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import AsyncSessionLocal, Base, engine
from app.routers import changefeed, debug, dicomweb, extended_query_tags, operations, ups
from app.services.events import EventManager, load_providers_from_config
from app.services.expiry import delete_expired_studies

logger = logging.getLogger(__name__)
DICOM_STORAGE_DIR = Path(os.getenv("DICOM_STORAGE_DIR", "/data/dicom"))


# Global event manager instance
event_manager: EventManager | None = None


def get_event_manager() -> EventManager:
    """Get the global event manager instance."""
    global event_manager
    if event_manager is None:
        raise RuntimeError("Event manager not initialized")
    return event_manager


async def expiry_cleanup_task():
    """Background task that runs every hour to delete expired studies."""
    while True:
        try:
            await asyncio.sleep(3600)  # 1 hour
            async with AsyncSessionLocal() as db:
                deleted_count = await delete_expired_studies(db, DICOM_STORAGE_DIR)
                if deleted_count > 0:
                    logger.info(f"Expiry cleanup: deleted {deleted_count} studies")
        except Exception as e:
            logger.error(f"Expiry cleanup task error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables and initialize event manager on startup."""
    global event_manager

    # Create database tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Initialize event providers
    providers = load_providers_from_config()
    event_manager = EventManager(providers)

    # Start background expiry cleanup task
    expiry_task = asyncio.create_task(expiry_cleanup_task())

    yield

    # Cleanup
    expiry_task.cancel()
    try:
        await expiry_task
    except asyncio.CancelledError:
        pass

    if event_manager:
        await event_manager.close()


app = FastAPI(
    title="Azure Healthcare Workspace Emulator",
    description=(
        "Local emulator for Azure Health Data Services DICOM Service (v2 API). "
        "Provides DICOMweb (STOW-RS, WADO-RS, QIDO-RS) plus Azure-specific APIs "
        "(Change Feed, Extended Query Tags, Operations)."
    ),
    version="0.1.0",
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
app.include_router(dicomweb.router, prefix="/v2", tags=["DICOMweb"])

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
