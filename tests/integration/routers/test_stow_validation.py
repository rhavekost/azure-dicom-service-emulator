"""Test STOW-RS validation of searchable attributes."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from tests.fixtures.factories import DicomFactory

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


@pytest.fixture
def storage_dir(tmp_path):
    """Create temporary storage directory."""
    storage = tmp_path / "dicom_storage"
    storage.mkdir()
    return storage


@pytest.mark.asyncio
async def test_stow_missing_patient_id_returns_202_with_warning(
    db_session, storage_dir, monkeypatch
):
    """STOW with missing PatientID should return 202 with warning."""
    # Set up storage directory
    monkeypatch.setenv("DICOM_STORAGE_DIR", str(storage_dir))
    import app.services.dicom_engine as dicom_engine

    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))

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

    # Create DICOM with missing PatientID
    dcm_bytes = DicomFactory.create_dicom_with_attrs(patient_id=None)

    boundary = "test-boundary"
    body = (
        (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    headers = {"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v2/studies", content=body, headers=headers)

    # Azure v2: Store succeeds with warning
    assert response.status_code == 202

    data = response.json()
    # Should have warning in response
    assert "00081196" in data  # WarningReason tag
    # Instance should still be stored successfully
    assert "00081199" in data  # Referenced SOP Sequence (success)


@pytest.mark.asyncio
async def test_stow_missing_modality_returns_202_with_warning(db_session, storage_dir, monkeypatch):
    """STOW with missing Modality should return 202 with warning."""
    # Set up storage directory
    monkeypatch.setenv("DICOM_STORAGE_DIR", str(storage_dir))
    import app.services.dicom_engine as dicom_engine

    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))

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

    # Create DICOM with missing Modality
    dcm_bytes = DicomFactory.create_dicom_with_attrs(modality=None)

    boundary = "test-boundary"
    body = (
        (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    headers = {"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v2/studies", content=body, headers=headers)

    # Azure v2: Store succeeds with warning
    assert response.status_code == 202

    data = response.json()
    # Should have warning in response
    assert "00081196" in data  # WarningReason tag
    # Instance should still be stored successfully
    assert "00081199" in data  # Referenced SOP Sequence (success)


@pytest.mark.asyncio
async def test_stow_missing_study_date_returns_202_with_warning(
    db_session, storage_dir, monkeypatch
):
    """STOW with missing StudyDate should return 202 with warning."""
    # Set up storage directory
    monkeypatch.setenv("DICOM_STORAGE_DIR", str(storage_dir))
    import app.services.dicom_engine as dicom_engine

    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))

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

    # Create DICOM with missing StudyDate
    dcm_bytes = DicomFactory.create_dicom_with_attrs(study_date=None)

    boundary = "test-boundary"
    body = (
        (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    headers = {"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v2/studies", content=body, headers=headers)

    # Azure v2: Store succeeds with warning
    assert response.status_code == 202

    data = response.json()
    # Should have warning in response
    assert "00081196" in data  # WarningReason tag
    # Instance should still be stored successfully
    assert "00081199" in data  # Referenced SOP Sequence (success)


@pytest.mark.asyncio
async def test_stow_empty_patient_id_returns_202_with_warning(db_session, storage_dir, monkeypatch):
    """STOW with empty PatientID should return 202 with warning."""
    # Set up storage directory
    monkeypatch.setenv("DICOM_STORAGE_DIR", str(storage_dir))
    import app.services.dicom_engine as dicom_engine

    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))

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

    # Create DICOM with empty PatientID
    dcm_bytes = DicomFactory.create_dicom_with_attrs(patient_id="")

    boundary = "test-boundary"
    body = (
        (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    headers = {"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v2/studies", content=body, headers=headers)

    # Azure v2: Store succeeds with warning
    assert response.status_code == 202

    data = response.json()
    # Should have warning in response
    assert "00081196" in data  # WarningReason tag
    # Instance should still be stored successfully
    assert "00081199" in data  # Referenced SOP Sequence (success)


@pytest.mark.asyncio
async def test_stow_valid_searchable_attributes_returns_200(db_session, storage_dir, monkeypatch):
    """STOW with all valid searchable attributes should return 200."""
    # Set up storage directory
    monkeypatch.setenv("DICOM_STORAGE_DIR", str(storage_dir))
    import app.services.dicom_engine as dicom_engine

    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))

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

    # Create DICOM with all valid searchable attributes
    dcm_bytes = DicomFactory.create_dicom_with_attrs(
        patient_id="VALID-001", modality="CT", study_date="20260217"
    )

    boundary = "test-boundary"
    body = (
        (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    headers = {"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v2/studies", content=body, headers=headers)

    # Should succeed without warnings
    assert response.status_code == 200

    data = response.json()
    # Should NOT have warning
    assert "00081196" not in data  # No WarningReason tag
    # Instance should be stored successfully
    assert "00081199" in data  # Referenced SOP Sequence (success)
