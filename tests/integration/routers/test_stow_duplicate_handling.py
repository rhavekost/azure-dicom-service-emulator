"""Test STOW-RS duplicate instance handling."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from tests.integration.routers.test_event_publishing_sequence import create_test_dicom_bytes

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
async def test_post_duplicate_instance_returns_409(db_session, storage_dir, monkeypatch):
    """POST with duplicate instance should return 409 Conflict."""
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

    # Create test DICOM
    dcm_bytes, study_uid, series_uid, sop_uid = create_test_dicom_bytes()

    boundary = "test-boundary"
    body = (
        (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    headers = {"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First upload - should succeed
        response1 = await client.post("/v2/studies", content=body, headers=headers)
        assert response1.status_code in (200, 202)

        # Second upload of same instance - should return 409
        response2 = await client.post("/v2/studies", content=body, headers=headers)
        assert response2.status_code == 409

        # Verify response contains conflict information
        data = response2.json()
        assert "00081198" in data  # Failed SOP Sequence
        failed = data["00081198"]["Value"][0]
        assert failed["00081155"]["Value"][0] == sop_uid  # Referenced SOP Instance UID
        assert failed["00081197"]["Value"][0] == 45070  # Warning: Instance already exists


@pytest.mark.asyncio
async def test_put_duplicate_instance_returns_200(db_session, storage_dir, monkeypatch):
    """PUT with duplicate instance should return 200 (upsert behavior)."""
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

    # Create test DICOM
    dcm_bytes, study_uid, series_uid, sop_uid = create_test_dicom_bytes()

    boundary = "test-boundary"
    body = (
        (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    headers = {"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First upload
        response1 = await client.put(
            f"/v2/studies/{study_uid}",
            content=body,
            headers=headers,
        )
        assert response1.status_code in (200, 201, 202)

        # Second upload of same instance - should still return 200 (upsert)
        response2 = await client.put(
            f"/v2/studies/{study_uid}",
            content=body,
            headers=headers,
        )
        assert response2.status_code in (200, 202)  # NOT 409 for PUT


@pytest.mark.asyncio
async def test_post_mixed_duplicates_returns_202(db_session, storage_dir, monkeypatch):
    """POST with mix of new and duplicate instances should return 202."""
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

    # Create two test DICOM instances
    dcm_bytes1, study_uid, series_uid1, sop_uid1 = create_test_dicom_bytes(
        study_uid="1.2.999",
        series_uid="1.2.999.1",
        sop_uid="1.2.999.1.1",
    )
    dcm_bytes2, _, series_uid2, sop_uid2 = create_test_dicom_bytes(
        study_uid="1.2.999",
        series_uid="1.2.999.2",
        sop_uid="1.2.999.2.1",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First upload - one instance
        boundary = "test-boundary"
        body1 = (
            (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
            + dcm_bytes1
            + f"\r\n--{boundary}--\r\n".encode()
        )

        response1 = await client.post(
            "/v2/studies",
            content=body1,
            headers={
                "Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"
            },
        )
        assert response1.status_code in (200, 202)

        # Second upload - mix of duplicate (dcm_bytes1) and new (dcm_bytes2)
        body2 = (
            (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
            + dcm_bytes1
            + (f"\r\n--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
            + dcm_bytes2
            + f"\r\n--{boundary}--\r\n".encode()
        )

        response2 = await client.post(
            "/v2/studies",
            content=body2,
            headers={
                "Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"
            },
        )

        # Should return 202 (partial success)
        assert response2.status_code == 202

        # Verify response contains both success and failure
        data = response2.json()
        assert "00081199" in data  # Referenced SOP Sequence (success)
        assert "00081198" in data  # Failed SOP Sequence
        assert len(data["00081199"]["Value"]) == 1  # One success (new instance)
        assert len(data["00081198"]["Value"]) == 1  # One failure (duplicate)
