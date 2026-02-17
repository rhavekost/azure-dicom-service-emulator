"""Integration tests for event publishing with correct sequence numbers."""

import json
from io import BytesIO

import pydicom
import pytest
from httpx import ASGITransport, AsyncClient
from pydicom.dataset import Dataset
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.services.events import EventManager
from app.services.events.providers import InMemoryEventProvider

pytestmark = pytest.mark.integration


def create_test_dicom_bytes(
    study_uid: str = "1.2.999",
    series_uid: str = "1.2.999.1",
    sop_uid: str = "1.2.999.1.1",
) -> tuple[bytes, str, str, str]:
    """Create minimal DICOM bytes for testing."""
    ds = Dataset()

    # File meta information
    file_meta = Dataset()
    file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT Image Storage
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    ds.file_meta = file_meta

    # Required DICOM attributes
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"

    # Patient module
    ds.PatientName = "Test^Patient"
    ds.PatientID = "TEST001"

    # Study module
    ds.StudyDate = "20240101"
    ds.StudyTime = "120000"
    ds.AccessionNumber = "ACC001"

    # Series module
    ds.Modality = "CT"
    ds.SeriesNumber = 1

    # Instance module
    ds.InstanceNumber = 1

    # Save to bytes
    bio = BytesIO()
    ds.save_as(bio, write_like_original=False)
    return bio.getvalue(), study_uid, series_uid, sop_uid


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


@pytest.fixture
def in_memory_event_provider():
    """Create in-memory event provider for testing."""
    return InMemoryEventProvider()


@pytest.fixture
def storage_dir(tmp_path):
    """Create temporary storage directory."""
    storage = tmp_path / "dicom_storage"
    storage.mkdir()
    return storage


@pytest.mark.asyncio
async def test_stow_publishes_event_with_integer_sequence(
    db_session, in_memory_event_provider, storage_dir, monkeypatch
):
    """STOW-RS should publish events with integer sequence numbers."""
    # Set up storage directory
    monkeypatch.setenv("DICOM_STORAGE_DIR", str(storage_dir))
    import app.services.dicom_engine as dicom_engine

    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))

    # Set up event manager
    import main

    event_manager = EventManager([in_memory_event_provider])
    monkeypatch.setattr(main, "event_manager", event_manager)

    # Create FastAPI app with test dependencies
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):
        yield

    from app.routers import dicomweb

    app = FastAPI(lifespan=test_lifespan)
    app.include_router(dicomweb.router, prefix="/v2", tags=["DICOMweb"])

    # Override database dependency
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # Create test DICOM
    dcm_bytes, study_uid, series_uid, sop_uid = create_test_dicom_bytes()

    # Upload via STOW-RS
    boundary = "test-boundary"
    body = (
        (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v2/studies",
            content=body,
            headers={
                "Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"
            },
        )

    assert response.status_code in (200, 202)

    # Verify event was published
    events = in_memory_event_provider.get_events()
    assert len(events) == 1

    event = events[0]
    assert event.event_type == "Microsoft.HealthcareApis.DicomImageCreated"

    # Critical assertion: sequence number must be an integer
    assert isinstance(event.data["sequenceNumber"], int)
    assert event.data["sequenceNumber"] > 0

    # Verify it's JSON serializable
    json_str = json.dumps(event.to_dict())
    assert json_str  # Should not raise


@pytest.mark.asyncio
async def test_put_stow_publishes_event_with_integer_sequence(
    db_session, in_memory_event_provider, storage_dir, monkeypatch
):
    """PUT STOW-RS should publish events with integer sequence numbers."""
    # Set up storage directory
    monkeypatch.setenv("DICOM_STORAGE_DIR", str(storage_dir))
    import app.services.dicom_engine as dicom_engine

    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))

    # Set up event manager
    import main

    event_manager = EventManager([in_memory_event_provider])
    monkeypatch.setattr(main, "event_manager", event_manager)

    # Create FastAPI app with test dependencies
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):
        yield

    from app.routers import dicomweb

    app = FastAPI(lifespan=test_lifespan)
    app.include_router(dicomweb.router, prefix="/v2", tags=["DICOMweb"])

    # Override database dependency
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # Create test DICOM
    dcm_bytes, study_uid, series_uid, sop_uid = create_test_dicom_bytes()

    # Upload via PUT STOW-RS
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            f"/v2/studies/{study_uid}",
            content=dcm_bytes,
            headers={"Content-Type": "application/dicom"},
        )

    assert response.status_code in (200, 201, 202)

    # Verify event was published with integer sequence
    events = in_memory_event_provider.get_events()
    assert len(events) >= 1

    event = events[-1]  # Get last event
    assert isinstance(event.data["sequenceNumber"], int)
    assert event.data["sequenceNumber"] > 0


@pytest.mark.asyncio
async def test_delete_publishes_event_with_integer_sequence(
    db_session, in_memory_event_provider, storage_dir, monkeypatch
):
    """DELETE should publish events with integer sequence numbers."""
    # Set up storage directory
    monkeypatch.setenv("DICOM_STORAGE_DIR", str(storage_dir))
    import app.services.dicom_engine as dicom_engine

    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))

    # Set up event manager
    import main

    event_manager = EventManager([in_memory_event_provider])
    monkeypatch.setattr(main, "event_manager", event_manager)

    # Create FastAPI app with test dependencies
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):
        yield

    from app.routers import dicomweb

    app = FastAPI(lifespan=test_lifespan)
    app.include_router(dicomweb.router, prefix="/v2", tags=["DICOMweb"])

    # Override database dependency
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # First upload an instance
    dcm_bytes, study_uid, series_uid, sop_uid = create_test_dicom_bytes()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Upload
        boundary = "test-boundary"
        body = (
            (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
            + dcm_bytes
            + f"\r\n--{boundary}--\r\n".encode()
        )

        await client.post(
            "/v2/studies",
            content=body,
            headers={
                "Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"
            },
        )

        # Clear events from upload
        in_memory_event_provider.clear()

        # Delete the study
        response = await client.delete(f"/v2/studies/{study_uid}")

    assert response.status_code == 204

    # Verify delete event was published with integer sequence
    events = in_memory_event_provider.get_events()
    assert len(events) >= 1

    delete_event = events[0]
    assert delete_event.event_type == "Microsoft.HealthcareApis.DicomImageDeleted"
    assert isinstance(delete_event.data["sequenceNumber"], int)
    assert delete_event.data["sequenceNumber"] > 0
