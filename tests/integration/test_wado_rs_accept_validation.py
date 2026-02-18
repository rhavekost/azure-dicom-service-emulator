"""Integration tests for WADO-RS Accept header validation (Task 9).

Tests HTTP 406 Not Acceptable enforcement for unsupported Accept headers
on WADO-RS retrieve endpoints.

Per DICOMweb standard, WADO-RS endpoints must validate Accept headers and
return 406 for unsupported media types.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.factories import DicomFactory

pytestmark = pytest.mark.integration


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Fixtures
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.fixture
def patch_dicomweb_storage(client, monkeypatch, tmp_path):
    """Patch dicomweb module's DICOM_STORAGE_DIR and frame_cache to use test storage.

    The dicomweb module reads DICOM_STORAGE_DIR at import time, so the
    module-level constant and frame_cache need to be monkeypatched to
    point at the test storage directory.

    Returns:
        Tuple of (client, storage_dir_path)
    """
    import app.routers.dicomweb as dicomweb_module
    from app.services.frame_cache import FrameCache

    # The test client fixture already sets up storage in tmp_path/dicom_storage
    # We need to find that directory
    storage_dir = tmp_path / "dicom_storage"
    storage_path = dicomweb_module.Path(str(storage_dir))

    monkeypatch.setattr(dicomweb_module, "DICOM_STORAGE_DIR", storage_path)
    monkeypatch.setattr(dicomweb_module, "frame_cache", FrameCache(storage_path))

    return client, storage_dir


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helper Functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def build_multipart_request(
    dicom_files: list[bytes], boundary: str = "boundary123"
) -> tuple[bytes, str]:
    """Build multipart/related request body for STOW-RS."""
    parts = []
    for dcm_data in dicom_files:
        part = (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n" f"\r\n").encode()
        part += dcm_data + b"\r\n"
        parts.append(part)

    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    content_type = f'multipart/related; type="application/dicom"; boundary={boundary}'

    return body, content_type


def store_dicom(client: TestClient, dicom_bytes: bytes) -> dict:
    """Store a DICOM instance and return the STOW-RS response JSON."""
    body, content_type = build_multipart_request([dicom_bytes])
    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert response.status_code == 200, f"Store failed: {response.status_code} {response.text}"
    return response.json()


def store_dicom_for_rendered(
    client: TestClient, dicom_bytes: bytes, storage_dir: Path
) -> tuple[str, str, str]:
    """Store a DICOM instance and create the file layout expected by rendered endpoints.

    The dicom_engine stores files as: {storage}/{study}/{series}/{sop}.dcm
    The rendered endpoints expect: {storage}/{study}/{series}/{sop}/instance.dcm

    This helper stores via STOW-RS then copies the file to the expected path.

    Returns:
        Tuple of (study_uid, series_uid, sop_uid)
    """
    store_response = store_dicom(client, dicom_bytes)
    url = store_response["00081199"]["Value"][0]["00081190"]["Value"][0]
    # URL format: /v2/studies/{study}/series/{series}/instances/{instance}
    url_parts = url.split("/")
    study_uid = url_parts[3]
    series_uid = url_parts[5]
    sop_uid = url_parts[7]

    return study_uid, series_uid, sop_uid


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. WADO-RS Retrieve - Supported Accept Headers (4 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_retrieve_accepts_multipart_related(client: TestClient):
    """WADO-RS retrieve accepts multipart/related (DICOMweb standard)."""
    dcm = DicomFactory.create_ct_image(patient_id="ACCEPT-001", with_pixel_data=False)
    response_json = store_dicom(client, dcm)
    url = response_json["00081199"]["Value"][0]["00081190"]["Value"][0]

    response = client.get(url, headers={"Accept": "multipart/related"})
    assert response.status_code == 200


def test_retrieve_accepts_application_dicom(client: TestClient):
    """WADO-RS retrieve accepts application/dicom."""
    dcm = DicomFactory.create_ct_image(patient_id="ACCEPT-002", with_pixel_data=False)
    response_json = store_dicom(client, dcm)
    url = response_json["00081199"]["Value"][0]["00081190"]["Value"][0]

    response = client.get(url, headers={"Accept": "application/dicom"})
    assert response.status_code == 200


def test_retrieve_accepts_wildcard(client: TestClient):
    """WADO-RS retrieve accepts */* wildcard."""
    dcm = DicomFactory.create_ct_image(patient_id="ACCEPT-003", with_pixel_data=False)
    response_json = store_dicom(client, dcm)
    url = response_json["00081199"]["Value"][0]["00081190"]["Value"][0]

    response = client.get(url, headers={"Accept": "*/*"})
    assert response.status_code == 200


def test_retrieve_defaults_without_accept_header(client: TestClient):
    """WADO-RS retrieve works without Accept header (default behavior)."""
    dcm = DicomFactory.create_ct_image(patient_id="ACCEPT-004", with_pixel_data=False)
    response_json = store_dicom(client, dcm)
    url = response_json["00081199"]["Value"][0]["00081190"]["Value"][0]

    # No Accept header should succeed (default to multipart/related)
    response = client.get(url)
    assert response.status_code == 200


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. WADO-RS Retrieve - Unsupported Accept Headers Return 406 (4 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_retrieve_rejects_text_plain(client: TestClient):
    """WADO-RS retrieve returns 406 for Accept: text/plain."""
    dcm = DicomFactory.create_ct_image(patient_id="REJECT-001", with_pixel_data=False)
    response_json = store_dicom(client, dcm)
    url = response_json["00081199"]["Value"][0]["00081190"]["Value"][0]

    response = client.get(url, headers={"Accept": "text/plain"})
    assert response.status_code == 406


def test_retrieve_rejects_application_json(client: TestClient):
    """WADO-RS retrieve returns 406 for Accept: application/json."""
    dcm = DicomFactory.create_ct_image(patient_id="REJECT-002", with_pixel_data=False)
    response_json = store_dicom(client, dcm)
    url = response_json["00081199"]["Value"][0]["00081190"]["Value"][0]

    response = client.get(url, headers={"Accept": "application/json"})
    assert response.status_code == 406


def test_retrieve_rejects_image_jpeg(client: TestClient):
    """WADO-RS retrieve returns 406 for Accept: image/jpeg (rendered only)."""
    dcm = DicomFactory.create_ct_image(patient_id="REJECT-003", with_pixel_data=False)
    response_json = store_dicom(client, dcm)
    url = response_json["00081199"]["Value"][0]["00081190"]["Value"][0]

    # image/jpeg is only valid for /rendered endpoints
    response = client.get(url, headers={"Accept": "image/jpeg"})
    assert response.status_code == 406


def test_retrieve_rejects_image_png(client: TestClient):
    """WADO-RS retrieve returns 406 for Accept: image/png (rendered only)."""
    dcm = DicomFactory.create_ct_image(patient_id="REJECT-004", with_pixel_data=False)
    response_json = store_dicom(client, dcm)
    url = response_json["00081199"]["Value"][0]["00081190"]["Value"][0]

    # image/png is only valid for /rendered endpoints
    response = client.get(url, headers={"Accept": "image/png"})
    assert response.status_code == 406


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. WADO-RS Rendered - Supported Accept Headers (4 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_rendered_accepts_image_jpeg(patch_dicomweb_storage):
    """WADO-RS /rendered accepts image/jpeg."""
    client, storage_dir = patch_dicomweb_storage
    dcm = DicomFactory.create_ct_image(patient_id="RENDERED-001", with_pixel_data=True)
    study_uid, series_uid, sop_uid = store_dicom_for_rendered(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered",
        headers={"Accept": "image/jpeg"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


def test_rendered_accepts_image_png(patch_dicomweb_storage):
    """WADO-RS /rendered accepts image/png."""
    client, storage_dir = patch_dicomweb_storage
    dcm = DicomFactory.create_ct_image(patient_id="RENDERED-002", with_pixel_data=True)
    study_uid, series_uid, sop_uid = store_dicom_for_rendered(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered",
        headers={"Accept": "image/png"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_rendered_accepts_wildcard(patch_dicomweb_storage):
    """WADO-RS /rendered accepts */* wildcard."""
    client, storage_dir = patch_dicomweb_storage
    dcm = DicomFactory.create_ct_image(patient_id="RENDERED-003", with_pixel_data=True)
    study_uid, series_uid, sop_uid = store_dicom_for_rendered(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered",
        headers={"Accept": "*/*"},
    )
    assert response.status_code == 200
    # Should default to image/jpeg
    assert response.headers["content-type"] == "image/jpeg"


def test_rendered_defaults_without_accept_header(patch_dicomweb_storage):
    """WADO-RS /rendered works without Accept header (defaults to jpeg)."""
    client, storage_dir = patch_dicomweb_storage
    dcm = DicomFactory.create_ct_image(patient_id="RENDERED-004", with_pixel_data=True)
    study_uid, series_uid, sop_uid = store_dicom_for_rendered(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. WADO-RS Rendered - Unsupported Accept Headers Return 406 (4 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_rendered_rejects_application_dicom(patch_dicomweb_storage):
    """WADO-RS /rendered returns 406 for Accept: application/dicom."""
    client, storage_dir = patch_dicomweb_storage
    dcm = DicomFactory.create_ct_image(patient_id="RENDERED-REJECT-001", with_pixel_data=True)
    study_uid, series_uid, sop_uid = store_dicom_for_rendered(client, dcm, storage_dir)

    # application/dicom is only valid for retrieve, not rendered
    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered",
        headers={"Accept": "application/dicom"},
    )
    assert response.status_code == 406


def test_rendered_rejects_multipart_related(patch_dicomweb_storage):
    """WADO-RS /rendered returns 406 for Accept: multipart/related."""
    client, storage_dir = patch_dicomweb_storage
    dcm = DicomFactory.create_ct_image(patient_id="RENDERED-REJECT-002", with_pixel_data=True)
    study_uid, series_uid, sop_uid = store_dicom_for_rendered(client, dcm, storage_dir)

    # multipart/related is only valid for retrieve, not rendered
    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered",
        headers={"Accept": "multipart/related"},
    )
    assert response.status_code == 406


def test_rendered_rejects_text_plain(patch_dicomweb_storage):
    """WADO-RS /rendered returns 406 for Accept: text/plain."""
    client, storage_dir = patch_dicomweb_storage
    dcm = DicomFactory.create_ct_image(patient_id="RENDERED-REJECT-003", with_pixel_data=True)
    study_uid, series_uid, sop_uid = store_dicom_for_rendered(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered",
        headers={"Accept": "text/plain"},
    )
    assert response.status_code == 406


def test_rendered_rejects_application_json(patch_dicomweb_storage):
    """WADO-RS /rendered returns 406 for Accept: application/json."""
    client, storage_dir = patch_dicomweb_storage
    dcm = DicomFactory.create_ct_image(patient_id="RENDERED-REJECT-004", with_pixel_data=True)
    study_uid, series_uid, sop_uid = store_dicom_for_rendered(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 406


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. Edge Cases and All Endpoints (4 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_study_level_retrieve_validates_accept(client: TestClient):
    """Study-level retrieve also validates Accept header."""
    study_uid = "1.2.826.0.1.3680043.8.498.40000000000000000000000000000000001"
    dcm = DicomFactory.create_ct_image(
        patient_id="STUDY-ACCEPT-001",
        study_uid=study_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    # Supported
    response = client.get(f"/v2/studies/{study_uid}", headers={"Accept": "application/dicom"})
    assert response.status_code == 200

    # Unsupported
    response = client.get(f"/v2/studies/{study_uid}", headers={"Accept": "text/html"})
    assert response.status_code == 406


def test_series_level_retrieve_validates_accept(client: TestClient):
    """Series-level retrieve also validates Accept header."""
    study_uid = "1.2.826.0.1.3680043.8.498.40000000000000000000000000000000002"
    series_uid = "1.2.826.0.1.3680043.8.498.40000000000000000000000000000000003"
    dcm = DicomFactory.create_ct_image(
        patient_id="SERIES-ACCEPT-001",
        study_uid=study_uid,
        series_uid=series_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    # Supported
    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}",
        headers={"Accept": "multipart/related"},
    )
    assert response.status_code == 200

    # Unsupported
    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}",
        headers={"Accept": "application/xml"},
    )
    assert response.status_code == 406


def test_frame_level_rendered_validates_accept(patch_dicomweb_storage):
    """Frame-level /rendered endpoint validates Accept header."""
    client, storage_dir = patch_dicomweb_storage
    dcm = DicomFactory.create_multiframe_ct(
        patient_id="FRAME-ACCEPT-001",
        num_frames=3,
        with_pixel_data=True,
    )
    study_uid, series_uid, sop_uid = store_dicom_for_rendered(client, dcm, storage_dir)

    # Supported
    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/frames/1/rendered",
        headers={"Accept": "image/png"},
    )
    assert response.status_code == 200

    # Unsupported
    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/frames/1/rendered",
        headers={"Accept": "video/mp4"},
    )
    assert response.status_code == 406


def test_accept_header_case_insensitive(client: TestClient):
    """Accept header matching should be case-insensitive."""
    dcm = DicomFactory.create_ct_image(patient_id="CASE-001", with_pixel_data=False)
    response_json = store_dicom(client, dcm)
    url = response_json["00081199"]["Value"][0]["00081190"]["Value"][0]

    # All of these should work (case variations)
    for accept in ["APPLICATION/DICOM", "Application/Dicom", "application/DICOM"]:
        response = client.get(url, headers={"Accept": accept})
        assert response.status_code == 200, f"Failed for Accept: {accept}"
