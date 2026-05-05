"""Integration tests for BulkDataURI shape + GET /bulkdata/{tag} round-trip.

Two guarantees this suite covers:

* The metadata path emits ``BulkDataURI`` (not ``InlineBinary``) for
  binary VRs (OB / OD / OF / OL / OW / UN), and the URI points back into
  the WADO-RS bulkdata endpoint.
* That endpoint resolves the URI back to the raw bytes from the on-disk
  DCM file with ``Content-Type: application/octet-stream``.

Why a dedicated suite: under the legacy ``InlineBinary`` shape the
``dicom_json`` JSONB column paid a ~33% size penalty per binary byte.
The BulkDataURI shape keeps those bytes out of the database entirely,
so a regression on the URI shape OR the resolver endpoint reintroduces
the write amplification — both ends need their own assertions.
"""

from contextlib import asynccontextmanager
from io import BytesIO

import pydicom
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db

pytestmark = pytest.mark.integration


def _build_dicom_with_overlay() -> tuple[bytes, str, str, str, bytes]:
    """Build a minimal CT DICOM with an overlay-data (OB) element.

    Returns ``(dcm_bytes, study_uid, series_uid, sop_uid, overlay_bytes)``.
    The overlay bytes get round-tripped through the BulkDataURI endpoint.
    """
    overlay = b"\x01\x02\x03\x04\x05\x06\x07\x08"

    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = "1.2.840.99.1"
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.1"

    ds = FileDataset(
        filename_or_obj=BytesIO(),
        dataset=Dataset(),
        file_meta=file_meta,
        preamble=b"\x00" * 128,
    )
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    ds.SOPInstanceUID = "1.2.840.99.1"
    ds.StudyInstanceUID = "1.2.840.99"
    ds.SeriesInstanceUID = "1.2.840.99.0"
    ds.PatientID = "BULKDATA-PAT"
    ds.PatientName = "BulkData^Test"
    ds.StudyDate = "20260214"
    ds.Modality = "CT"

    # OverlayData (60xx,3000) is the canonical OB tag; we use a private-
    # range public tag so pydicom's tag classification doesn't drop it
    # as a private element on serialisation.
    ds.add_new(pydicom.tag.Tag(0x0042, 0x0011), "OB", overlay)

    buffer = BytesIO()
    ds.save_as(buffer)
    return (
        buffer.getvalue(),
        ds.StudyInstanceUID,
        ds.SeriesInstanceUID,
        ds.SOPInstanceUID,
        overlay,
    )


@pytest.fixture
async def db_session(tmp_path):
    """Create an isolated SQLite database for each test."""
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    """Point the DICOM engine at a temporary storage directory."""
    storage = tmp_path / "dicom_storage"
    storage.mkdir()
    monkeypatch.setenv("DICOM_STORAGE_DIR", str(storage))
    import app.services.dicom_engine as dicom_engine

    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage))
    return storage


def _make_app(db_session) -> FastAPI:
    """Build a minimal FastAPI app wired against the test DB session."""

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):
        # ASGITransport doesn't run the lifespan; initialise the
        # streaming-STOW app-state contract directly so route handlers
        # don't hit AttributeError on app.state.
        app.state.pending_event_tasks = set()
        app.state.parse_pool = None
        yield

    from app.routers import dicomweb

    app = FastAPI(lifespan=test_lifespan)
    app.include_router(dicomweb.router, prefix="/v2", tags=["DICOMweb"])

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return app


async def _stow_post(client: AsyncClient, dcm_bytes: bytes) -> str:
    """POST a single instance via STOW-RS multipart and return the response status."""
    boundary = "bulkdata-boundary"
    body = (
        f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )
    headers = {
        "Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"
    }
    response = await client.post("/v2/studies", content=body, headers=headers)
    assert response.status_code in (200, 202), (
        f"STOW-RS upload failed: {response.status_code} {response.text[:400]}"
    )
    return response.text


@pytest.mark.asyncio
async def test_metadata_emits_bulkdata_uri_for_binary_vr(db_session, storage_dir):
    """Metadata response for a DICOM with OB data must include BulkDataURI, not InlineBinary."""
    app = _make_app(db_session)
    dcm_bytes, study_uid, series_uid, sop_uid, _ = _build_dicom_with_overlay()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _stow_post(client, dcm_bytes)

        meta = await client.get(
            f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/metadata",
            headers={"Accept": "application/dicom+json"},
        )
        assert meta.status_code == 200, meta.text
        payload = meta.json()
        assert isinstance(payload, list) and payload, payload
        instance_metadata = payload[0]

        ob_entry = instance_metadata.get("00420011")
        assert ob_entry is not None, (
            f"expected an OB entry under tag 00420011, got: {instance_metadata!r}"
        )
        assert ob_entry["vr"] == "OB"
        # Regression: the JSONB column must not carry inline base64.
        assert "InlineBinary" not in ob_entry, (
            f"binary VR must use BulkDataURI, not InlineBinary: {ob_entry!r}"
        )
        bulkdata_uri = ob_entry.get("BulkDataURI")
        assert bulkdata_uri is not None, (
            f"binary VR entry must include BulkDataURI: {ob_entry!r}"
        )
        # Shape: <base>/bulkdata/<tag>
        expected_suffix = (
            f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/bulkdata/00420011"
        )
        assert bulkdata_uri.endswith(expected_suffix), (
            f"BulkDataURI should end with {expected_suffix!r}, got: {bulkdata_uri!r}"
        )


@pytest.mark.asyncio
async def test_get_bulkdata_endpoint_round_trip(db_session, storage_dir):
    """GET /bulkdata/{tag} resolves the URI back to the original raw bytes."""
    app = _make_app(db_session)
    dcm_bytes, study_uid, series_uid, sop_uid, overlay = _build_dicom_with_overlay()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _stow_post(client, dcm_bytes)

        bulk = await client.get(
            f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/bulkdata/00420011"
        )
        assert bulk.status_code == 200, bulk.text
        assert bulk.headers.get("content-type", "").startswith("application/octet-stream")
        assert bulk.content == overlay


@pytest.mark.asyncio
async def test_get_bulkdata_returns_404_for_missing_tag(db_session, storage_dir):
    """A tag that isn't present on the instance must return 404."""
    app = _make_app(db_session)
    dcm_bytes, study_uid, series_uid, sop_uid, _ = _build_dicom_with_overlay()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _stow_post(client, dcm_bytes)

        # Pick a tag that definitely isn't in the dataset.
        response = await client.get(
            f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/bulkdata/00420099"
        )
        assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_get_bulkdata_rejects_malformed_tag(db_session, storage_dir):
    """The endpoint validates the tag is 8 hex digits — non-hex tags get 400."""
    app = _make_app(db_session)
    dcm_bytes, study_uid, series_uid, sop_uid, _ = _build_dicom_with_overlay()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _stow_post(client, dcm_bytes)

        response = await client.get(
            f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/bulkdata/notatag"
        )
        assert response.status_code == 400, response.text
