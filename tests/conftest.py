"""Test fixtures for Azure DICOM Service Emulator."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Set default test storage dir before importing app modules
# This ensures STORAGE_DIR in dicom_engine.py gets the test value
os.environ.setdefault("DICOM_STORAGE_DIR", "/tmp/test_dicom_storage")

from app.database import Base, get_db
from app.routers import changefeed, debug, dicomweb, extended_query_tags, operations, ups
from tests.fixtures.factories import DicomFactory


@pytest.fixture
def test_engine_and_session(tmp_path, monkeypatch):
    """Create a shared async engine and session factory for testing.

    Yields a tuple of (engine, session_factory) so that both the HTTP
    client and direct DB access fixtures can share the same database.
    """
    # Set DICOM storage directory to temporary path
    storage_dir = tmp_path / "dicom_storage"
    storage_dir.mkdir(exist_ok=True)

    import app.services.dicom_engine as dicom_engine

    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("DICOM_STORAGE_DIR", str(storage_dir))

    db_path = tmp_path / "test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, session_factory


@pytest.fixture
def client(tmp_path, monkeypatch, test_engine_and_session):
    """Create a TestClient with in-memory test database."""
    test_engine, TestSessionLocal = test_engine_and_session

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

    # Add health check endpoint
    @test_app.get("/health", tags=["Health"])
    async def health_check():
        return {"status": "healthy", "service": "azure-healthcare-workspace-emulator"}

    # Override database dependency
    test_app.dependency_overrides[get_db] = override_get_db

    # Create test client
    with TestClient(test_app) as test_client:
        yield test_client


@pytest.fixture
def db_session(test_engine_and_session):
    """Provide direct async DB session access for tests that need to query the DB.

    Usage in async test::

        async def test_foo(client, db_session):
            async with db_session() as session:
                result = await session.execute(select(MyModel).where(...))
    """
    _, session_factory = test_engine_and_session
    return session_factory


# ── DICOM Fixture Helpers ──────────────────────────────────────


@pytest.fixture
def sample_ct_dicom():
    """Returns CT DICOM bytes (minimal, no pixels)."""
    return DicomFactory.create_ct_image(
        patient_id="FIXTURE-CT-001",
        patient_name="Fixture^CT",
        with_pixel_data=False,
    )


@pytest.fixture
def sample_ct_with_pixels():
    """Returns CT DICOM bytes with pixel data."""
    return DicomFactory.create_ct_image(
        patient_id="FIXTURE-CT-002",
        patient_name="Fixture^CTPixels",
        with_pixel_data=True,
    )


@pytest.fixture
def sample_mri_dicom():
    """Returns MRI DICOM bytes."""
    return DicomFactory.create_mri_image(
        patient_id="FIXTURE-MRI-001",
        patient_name="Fixture^MRI",
        with_pixel_data=True,
    )


@pytest.fixture
def sample_multiframe_dicom():
    """Returns multiframe CT DICOM bytes."""
    return DicomFactory.create_multiframe_ct(
        patient_id="FIXTURE-MULTI-001",
        num_frames=10,
        with_pixel_data=True,
    )


@pytest.fixture
def invalid_dicom_missing_sop():
    """Returns invalid DICOM missing SOPInstanceUID."""
    return DicomFactory.create_invalid_dicom(missing_tags=["SOPInstanceUID"])


@pytest.fixture
def invalid_dicom_missing_study():
    """Returns invalid DICOM missing StudyInstanceUID."""
    return DicomFactory.create_invalid_dicom(missing_tags=["StudyInstanceUID"])


@pytest.fixture
def fixtures_dir():
    """Returns path to fixtures directory."""
    return Path(__file__).parent / "fixtures" / "dicom"


@pytest.fixture
def valid_dicom_files(fixtures_dir):
    """Returns list of paths to valid DICOM files."""
    return list((fixtures_dir / "valid").glob("*.dcm"))


@pytest.fixture
def edge_case_dicom_files(fixtures_dir):
    """Returns list of paths to edge case DICOM files."""
    return list((fixtures_dir / "edge_cases").glob("*.dcm"))


@pytest.fixture
def invalid_dicom_files(fixtures_dir):
    """Returns list of paths to invalid DICOM files."""
    return list((fixtures_dir / "invalid").glob("*.dcm"))
