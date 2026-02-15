"""
Azure Healthcare Workspace Emulator

A Docker-based local emulator for Azure Health Data Services DICOM Service.
Drop-in replacement for development and testing — like Azurite for Storage,
but for Healthcare DICOM Service.

MIT License - KostLabs
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app.routers import dicomweb, changefeed, extended_query_tags, operations, debug
from app.services.events import EventManager, load_providers_from_config


# Global event manager instance
event_manager: EventManager | None = None


def get_event_manager() -> EventManager:
    """Get the global event manager instance."""
    global event_manager
    if event_manager is None:
        raise RuntimeError("Event manager not initialized")
    return event_manager


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

    yield

    # Cleanup
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

# ── Debug Endpoints ────────────────────────────────────────────────
app.include_router(debug.router, tags=["Debug"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy", "service": "azure-healthcare-workspace-emulator"}
