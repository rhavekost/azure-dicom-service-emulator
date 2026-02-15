"""Test fixtures for Azure DICOM Service Emulator."""

import pytest
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi.testclient import TestClient
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.database import Base, get_db
from app.routers import dicomweb, changefeed, extended_query_tags, operations, debug, ups


@pytest.fixture
def client(tmp_path):
    """Create a TestClient with in-memory test database."""

    # Create test database engine
    db_path = tmp_path / "test.db"
    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False}
    )
    TestSessionLocal = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    # Override get_db dependency
    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    # Create a lifespan context for the test app
    @asynccontextmanager
    async def test_lifespan(app: FastAPI):
        # Create tables
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield
        # Cleanup
        await test_engine.dispose()

    # Create test app
    test_app = FastAPI(lifespan=test_lifespan)

    # Register routers
    test_app.include_router(dicomweb.router, prefix="/v2", tags=["DICOMweb"])
    test_app.include_router(changefeed.router, prefix="/v2", tags=["Change Feed"])
    test_app.include_router(extended_query_tags.router, prefix="/v2", tags=["Extended Query Tags"])
    test_app.include_router(operations.router, prefix="/v2", tags=["Operations"])
    test_app.include_router(ups.router, prefix="/v2", tags=["UPS-RS"])
    test_app.include_router(debug.router, tags=["Debug"])

    # Override database dependency
    test_app.dependency_overrides[get_db] = override_get_db

    # Create test client
    with TestClient(test_app) as test_client:
        yield test_client
