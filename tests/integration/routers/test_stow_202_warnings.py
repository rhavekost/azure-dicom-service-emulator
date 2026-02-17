"""Test STOW-RS 202 warning responses.

This test suite verifies that STOW-RS returns HTTP 202 for warning scenarios
as per Azure DICOM Service v2 specification.
"""

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


@pytest.mark.skip(reason="Waiting for Task 5: searchable attribute validation")
@pytest.mark.asyncio
async def test_post_with_searchable_attribute_warnings_returns_202(
    db_session, storage_dir, monkeypatch
):
    """POST with searchable attribute validation warnings should return 202.

    This test is a placeholder for Task 5. When searchable attribute validation
    is implemented, it will:
    1. Create DICOM with invalid searchable attribute (e.g., empty PatientID)
    2. Verify instance is stored successfully
    3. Verify response returns 202 (not 200 or 409)
    4. Verify response contains WarningReason tag (0x00081196)
    """
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

    # TODO (Task 5): Create DICOM with invalid PatientID or other searchable attribute
    # This requires adding a test helper to create DICOM with specific attribute issues
    # For example:
    # dcm_bytes = create_test_dicom_bytes_with_invalid_searchable_attr(
    #     patient_id=""  # Empty string should trigger warning but allow storage
    # )

    # Create test DICOM (placeholder - will be modified in Task 5)
    dcm_bytes, study_uid, series_uid, sop_uid = create_test_dicom_bytes()

    boundary = "test-boundary"
    body = (
        (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    headers = {"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # When Task 5 is implemented, this should return 202 with warnings
        response = await client.post("/v2/studies", content=body, headers=headers)

        # TODO (Task 5): Update assertions when searchable attribute validation exists
        # assert response.status_code == 202
        # data = response.json()
        # assert "00081196" in data  # WarningReason tag
        # assert "00081199" in data  # Instance was stored successfully

        # For now, just verify current behavior (200 for valid DICOM)
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_post_all_success_returns_200(db_session, storage_dir, monkeypatch):
    """POST with all instances succeeding should return 200 OK."""
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
        # Upload should succeed with 200
        response = await client.post("/v2/studies", content=body, headers=headers)
        assert response.status_code == 200

        # Verify response contains success
        data = response.json()
        assert "00081199" in data  # Referenced SOP Sequence (success)
        assert len(data["00081199"]["Value"]) == 1
        assert data["00081199"]["Value"][0]["00081155"]["Value"][0] == sop_uid


@pytest.mark.asyncio
async def test_post_with_missing_required_attributes_returns_409(
    db_session, storage_dir, monkeypatch
):
    """POST with missing required attributes should return 409 when all fail."""
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

    # Create invalid DICOM data that will fail required attribute validation
    # Invalid data will be caught by required attribute validation or parse exception
    boundary = "test-boundary"
    invalid_dicom = b"INVALID_DICOM_DATA"
    body = (
        (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
        + invalid_dicom
        + f"\r\n--{boundary}--\r\n".encode()
    )

    headers = {"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Should return 409 when all instances fail (no storage occurred)
        response = await client.post("/v2/studies", content=body, headers=headers)
        assert response.status_code == 409

        # Verify response contains failure information
        data = response.json()
        assert "00081198" in data  # Failed SOP Sequence
        failed = data["00081198"]["Value"][0]
        # Either failure reason (validation) or warning (exception)
        assert "00081197" in failed or "00081196" in failed


@pytest.mark.asyncio
async def test_post_partial_success_returns_202(db_session, storage_dir, monkeypatch):
    """POST with partial success (some stored, some failed) should return 202."""
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

    # Create one valid and one invalid DICOM
    valid_dcm, study_uid, series_uid, sop_uid = create_test_dicom_bytes()
    invalid_dicom = b"INVALID"

    boundary = "test-boundary"
    body = (
        # Valid DICOM
        (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
        + valid_dcm
        # Invalid DICOM
        + (f"\r\n--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
        + invalid_dicom
        + f"\r\n--{boundary}--\r\n".encode()
    )

    headers = {"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Should return 202 (partial success)
        response = await client.post("/v2/studies", content=body, headers=headers)
        assert response.status_code == 202

        # Verify response contains both success and failure
        data = response.json()
        assert "00081199" in data  # Referenced SOP Sequence (success)
        assert "00081198" in data  # Failed SOP Sequence (failure)
        assert len(data["00081199"]["Value"]) == 1  # One success
        assert len(data["00081198"]["Value"]) == 1  # One failure


@pytest.mark.asyncio
async def test_post_all_failures_returns_409(db_session, storage_dir, monkeypatch):
    """POST with all instances failing should return 409 Conflict."""
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

    # Create two invalid DICOM instances
    invalid_dicom1 = b"INVALID1"
    invalid_dicom2 = b"INVALID2"

    boundary = "test-boundary"
    body = (
        (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
        + invalid_dicom1
        + (f"\r\n--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
        + invalid_dicom2
        + f"\r\n--{boundary}--\r\n".encode()
    )

    headers = {"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Should return 409 (all failed)
        response = await client.post("/v2/studies", content=body, headers=headers)
        assert response.status_code == 409

        # Verify response contains only failures
        data = response.json()
        assert "00081198" in data  # Failed SOP Sequence
        assert len(data["00081198"]["Value"]) == 2  # Two failures
        assert "00081199" not in data  # No successes


@pytest.mark.asyncio
async def test_status_code_logic_completeness(db_session, storage_dir, monkeypatch):
    """
    Comprehensive test to verify all status code scenarios.

    This test verifies the status code logic at lines 249-256 in dicomweb.py:
    - 409 if failures and not stored (all fail)
    - 202 if has_warnings or warnings or failures (any warnings/partial)
    - 200 if all succeed (default)
    """
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

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        boundary = "test-boundary"

        # Scenario 1: All succeed → 200
        dcm1, _, _, _ = create_test_dicom_bytes(sop_uid="1.2.3.4.1")
        body1 = (
            (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
            + dcm1
            + f"\r\n--{boundary}--\r\n".encode()
        )
        response1 = await client.post(
            "/v2/studies",
            content=body1,
            headers={
                "Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"
            },
        )
        assert response1.status_code == 200, "All success should return 200"

        # Scenario 2: All fail (duplicate) → 409
        response2 = await client.post(
            "/v2/studies",
            content=body1,
            headers={
                "Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"
            },
        )
        assert response2.status_code == 409, "All failures (duplicate) should return 409"

        # Scenario 3: Partial success (valid + duplicate) → 202
        dcm3, _, _, _ = create_test_dicom_bytes(sop_uid="1.2.3.4.3")
        body3 = (
            (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
            + dcm1  # duplicate
            + (f"\r\n--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
            + dcm3  # new
            + f"\r\n--{boundary}--\r\n".encode()
        )
        response3 = await client.post(
            "/v2/studies",
            content=body3,
            headers={
                "Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"
            },
        )
        assert response3.status_code == 202, "Partial success should return 202"

        # Scenario 4: All fail (invalid data) → 409
        invalid_dicom = b"INVALID"
        body4 = (
            (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
            + invalid_dicom
            + f"\r\n--{boundary}--\r\n".encode()
        )
        response4 = await client.post(
            "/v2/studies",
            content=body4,
            headers={
                "Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"
            },
        )
        assert response4.status_code == 409, "All failures should return 409"
