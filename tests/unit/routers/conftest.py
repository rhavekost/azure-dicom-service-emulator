"""Shared fixtures for router unit tests."""

import pytest
from pydicom.uid import generate_uid

from tests.fixtures.factories import DicomFactory

BOUNDARY = "test_boundary"
CONTENT_TYPE = f"multipart/related; type=application/dicom; boundary={BOUNDARY}"


def make_multipart(dicom_bytes: bytes, boundary: str = BOUNDARY) -> bytes:
    """Wrap DICOM bytes in a minimal multipart/related body."""
    return (
        (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n" f"\r\n").encode()
        + dicom_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )


@pytest.fixture
def stored_instance(client, tmp_path, monkeypatch):
    """Store a CT DICOM instance and return its UIDs.

    Yields a dict with study_uid, series_uid, sop_uid.
    Uses the existing `client` fixture from tests/conftest.py.
    """
    import app.services.dicom_engine as dicom_engine

    storage_dir = tmp_path / "dicom_storage"
    storage_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("DICOM_STORAGE_DIR", str(storage_dir))

    study_uid = generate_uid()
    series_uid = generate_uid()
    sop_uid = generate_uid()

    dicom_bytes = DicomFactory.create_ct_image(
        patient_id="STORE-001",
        patient_name="Store^Test",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=True,
    )

    response = client.post(
        "/v2/studies",
        content=make_multipart(dicom_bytes),
        headers={"Content-Type": CONTENT_TYPE},
    )
    assert response.status_code in (200, 202), f"Setup failed: {response.text}"

    yield {"study_uid": study_uid, "series_uid": series_uid, "sop_uid": sop_uid}
