"""Test fixtures for Azure DICOM Service Emulator."""

import pytest
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi.testclient import TestClient
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.database import Base, get_db
from app.routers import dicomweb, changefeed, extended_query_tags, operations, debug, ups
from tests.fixtures.factories import DicomFactory


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

    # Add health check endpoint
    @test_app.get("/health", tags=["Health"])
    async def health_check():
        return {"status": "healthy", "service": "azure-healthcare-workspace-emulator"}

    # Override database dependency
    test_app.dependency_overrides[get_db] = override_get_db

    # Create test client
    with TestClient(test_app) as test_client:
        yield test_client


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
    return DicomFactory.create_invalid_dicom(
        missing_tags=["SOPInstanceUID"]
    )


@pytest.fixture
def invalid_dicom_missing_study():
    """Returns invalid DICOM missing StudyInstanceUID."""
    return DicomFactory.create_invalid_dicom(
        missing_tags=["StudyInstanceUID"]
    )


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
