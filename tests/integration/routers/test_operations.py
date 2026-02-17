"""Test operations status endpoint."""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session(tmp_path):
    """Create a temporary test database."""
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create session
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session

    # Cleanup
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_operation_status_returns_json_serializable(db_session):
    """Operation status response should be JSON serializable."""
    # Create FastAPI app with test dependencies
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    from app.routers import extended_query_tags, operations

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):
        yield

    app = FastAPI(lifespan=test_lifespan)
    app.include_router(extended_query_tags.router, prefix="/v2", tags=["Extended Query Tags"])
    app.include_router(operations.router, prefix="/v2", tags=["Operations"])

    # Override database dependency
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # Create an operation (via extended query tag creation)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post(
            "/v2/extendedquerytags",
            json={"tags": [{"path": "00101002", "vr": "SQ", "level": "Study"}]},
        )

        assert create_response.status_code == 202

        # Extract operation ID from response body
        create_data = create_response.json()
        operation_id = create_data.get("id")
        assert operation_id  # Should have an operation ID

        # Get operation status
        response = await client.get(f"/v2/operations/{operation_id}")

        assert response.status_code == 200

        # Verify response is valid JSON (no UUID objects)
        data = response.json()

        # Should be able to re-serialize without errors
        json_str = json.dumps(data)
        assert json_str  # Should not raise TypeError

        # Verify operation ID is a string
        assert isinstance(data.get("id"), str)
