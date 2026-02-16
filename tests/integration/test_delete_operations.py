"""Integration tests for DELETE endpoint operations.

Tests DICOM deletion at study, series, and instance levels.
Covers cascading deletion, filesystem cleanup, database cleanup,
and error handling (404 Not Found).
"""

import os

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.factories import DicomFactory

pytestmark = pytest.mark.integration


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helper Functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def build_multipart_request(
    dicom_files: list[bytes], boundary: str = "boundary123"
) -> tuple[bytes, str]:
    """Build multipart/related request body for STOW-RS.

    Args:
        dicom_files: List of DICOM file bytes to include
        boundary: Boundary delimiter string

    Returns:
        Tuple of (request_body, content_type_header)
    """
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
    """Store a DICOM instance and return the STOW-RS response JSON.

    Args:
        client: Test client
        dicom_bytes: DICOM file as bytes

    Returns:
        STOW-RS response JSON dict
    """
    body, content_type = build_multipart_request([dicom_bytes])
    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert response.status_code == 200, f"Store failed: {response.status_code} {response.text}"
    return response.json()


def get_storage_dir(client: TestClient) -> str:
    """Get the DICOM storage directory from the test client's environment."""
    return os.environ.get("DICOM_STORAGE_DIR", "/tmp/test_dicom_storage")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. Study Deletion (4 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_delete_study_removes_all_instances(client: TestClient):
    """DELETE /v2/studies/{study} removes all instances in the study."""
    study_uid = "1.2.826.0.1.3680043.8.498.50000000000000000000000000000000001"
    series1 = "1.2.826.0.1.3680043.8.498.50000000000000000000000000000000002"
    series2 = "1.2.826.0.1.3680043.8.498.50000000000000000000000000000000003"

    # Store 2 instances in series 1
    for _ in range(2):
        dcm = DicomFactory.create_ct_image(
            patient_id="DEL-STUDY-001",
            study_uid=study_uid,
            series_uid=series1,
            with_pixel_data=False,
        )
        store_dicom(client, dcm)

    # Store 1 instance in series 2
    dcm = DicomFactory.create_ct_image(
        patient_id="DEL-STUDY-001",
        study_uid=study_uid,
        series_uid=series2,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    # Verify study exists via QIDO-RS
    search_response = client.get(f"/v2/studies?StudyInstanceUID={study_uid}")
    assert search_response.status_code == 200
    studies = search_response.json()
    assert len(studies) == 1

    # Delete entire study
    delete_response = client.delete(f"/v2/studies/{study_uid}")
    assert delete_response.status_code == 204

    # Verify study is no longer findable via QIDO-RS
    search_response = client.get(f"/v2/studies?StudyInstanceUID={study_uid}")
    assert search_response.status_code == 200
    studies = search_response.json()
    assert len(studies) == 0

    # Verify WADO-RS retrieve returns 404
    retrieve_response = client.get(
        f"/v2/studies/{study_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )
    assert retrieve_response.status_code == 404


def test_delete_study_removes_filesystem_files(client: TestClient):
    """DELETE /v2/studies/{study} removes DCM files from disk."""
    study_uid = "1.2.826.0.1.3680043.8.498.50000000000000000000000000000000004"
    series_uid = "1.2.826.0.1.3680043.8.498.50000000000000000000000000000000005"
    sop_uid = "1.2.826.0.1.3680043.8.498.50000000000000000000000000000000006"

    dcm = DicomFactory.create_ct_image(
        patient_id="DEL-FS-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    # Verify file exists on disk
    storage_dir = get_storage_dir(client)
    file_path = os.path.join(storage_dir, study_uid, series_uid, f"{sop_uid}.dcm")
    assert os.path.exists(file_path), f"File should exist before deletion at {file_path}"

    # Delete study
    delete_response = client.delete(f"/v2/studies/{study_uid}")
    assert delete_response.status_code == 204

    # Verify file is removed from disk
    assert not os.path.exists(file_path), "File should be removed after deletion"


def test_delete_study_removes_database_entries(client: TestClient):
    """DELETE /v2/studies/{study} removes all DB records for instances."""
    study_uid = "1.2.826.0.1.3680043.8.498.50000000000000000000000000000000007"
    series_uid = "1.2.826.0.1.3680043.8.498.50000000000000000000000000000000008"

    # Store 3 instances
    sop_uids = []
    for i in range(3):
        sop_uid = f"1.2.826.0.1.3680043.8.498.5000000000000000000000000000000000{i + 1}0"
        sop_uids.append(sop_uid)
        dcm = DicomFactory.create_ct_image(
            patient_id="DEL-DB-001",
            study_uid=study_uid,
            series_uid=series_uid,
            sop_uid=sop_uid,
            with_pixel_data=False,
        )
        store_dicom(client, dcm)

    # Verify instances exist via metadata endpoint
    meta_response = client.get(
        f"/v2/studies/{study_uid}/metadata",
        headers={"Accept": "application/dicom+json"},
    )
    assert meta_response.status_code == 200
    assert len(meta_response.json()) == 3

    # Delete study
    delete_response = client.delete(f"/v2/studies/{study_uid}")
    assert delete_response.status_code == 204

    # Verify all instances are gone from the database
    meta_response = client.get(
        f"/v2/studies/{study_uid}/metadata",
        headers={"Accept": "application/dicom+json"},
    )
    assert meta_response.status_code == 404


def test_delete_nonexistent_study_returns_404(client: TestClient):
    """DELETE for a non-existent study returns 404 Not Found."""
    nonexistent_uid = "1.2.826.0.1.3680043.8.498.59999999999999999999999999999999999"

    response = client.delete(f"/v2/studies/{nonexistent_uid}")
    assert response.status_code == 404


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. Series Deletion (4 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_delete_series_preserves_other_series(client: TestClient):
    """DELETE /v2/studies/{study}/series/{series} only removes targeted series."""
    study_uid = "1.2.826.0.1.3680043.8.498.60000000000000000000000000000000001"
    series_to_delete = "1.2.826.0.1.3680043.8.498.60000000000000000000000000000000002"
    series_to_keep = "1.2.826.0.1.3680043.8.498.60000000000000000000000000000000003"

    # Store 2 instances in the series to delete
    for _ in range(2):
        dcm = DicomFactory.create_ct_image(
            patient_id="DEL-SER-001",
            study_uid=study_uid,
            series_uid=series_to_delete,
            with_pixel_data=False,
        )
        store_dicom(client, dcm)

    # Store 1 instance in the series to keep
    sop_uid_kept = "1.2.826.0.1.3680043.8.498.60000000000000000000000000000000004"
    dcm = DicomFactory.create_ct_image(
        patient_id="DEL-SER-001",
        study_uid=study_uid,
        series_uid=series_to_keep,
        sop_uid=sop_uid_kept,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    # Delete the targeted series
    delete_response = client.delete(f"/v2/studies/{study_uid}/series/{series_to_delete}")
    assert delete_response.status_code == 204

    # Verify the deleted series is gone
    retrieve_response = client.get(
        f"/v2/studies/{study_uid}/series/{series_to_delete}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )
    assert retrieve_response.status_code == 404

    # Verify the kept series still exists
    retrieve_response = client.get(
        f"/v2/studies/{study_uid}/series/{series_to_keep}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )
    assert retrieve_response.status_code == 200
    assert retrieve_response.content.count(b"DICM") == 1

    # Verify study still appears in QIDO-RS (because the kept series remains)
    search_response = client.get(f"/v2/studies?StudyInstanceUID={study_uid}")
    assert search_response.status_code == 200
    studies = search_response.json()
    assert len(studies) == 1


def test_delete_series_removes_files(client: TestClient):
    """DELETE /v2/studies/{study}/series/{series} removes files from disk."""
    study_uid = "1.2.826.0.1.3680043.8.498.60000000000000000000000000000000005"
    series_uid = "1.2.826.0.1.3680043.8.498.60000000000000000000000000000000006"
    sop_uid = "1.2.826.0.1.3680043.8.498.60000000000000000000000000000000007"

    dcm = DicomFactory.create_ct_image(
        patient_id="DEL-SER-FS-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    # Verify file exists on disk
    storage_dir = get_storage_dir(client)
    file_path = os.path.join(storage_dir, study_uid, series_uid, f"{sop_uid}.dcm")
    assert os.path.exists(file_path), "File should exist before deletion"

    # Delete series
    delete_response = client.delete(f"/v2/studies/{study_uid}/series/{series_uid}")
    assert delete_response.status_code == 204

    # Verify file is removed
    assert not os.path.exists(file_path), "File should be removed after series deletion"


def test_delete_series_updates_database(client: TestClient):
    """DELETE /v2/studies/{study}/series/{series} removes DB entries for that series."""
    study_uid = "1.2.826.0.1.3680043.8.498.60000000000000000000000000000000008"
    series_uid = "1.2.826.0.1.3680043.8.498.60000000000000000000000000000000009"

    # Store 2 instances in the series
    for i in range(2):
        sop_uid = f"1.2.826.0.1.3680043.8.498.6000000000000000000000000000000001{i}"
        dcm = DicomFactory.create_ct_image(
            patient_id="DEL-SER-DB-001",
            study_uid=study_uid,
            series_uid=series_uid,
            sop_uid=sop_uid,
            with_pixel_data=False,
        )
        store_dicom(client, dcm)

    # Verify instances exist
    meta_response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances",
    )
    assert meta_response.status_code == 200
    assert len(meta_response.json()) == 2

    # Delete series
    delete_response = client.delete(f"/v2/studies/{study_uid}/series/{series_uid}")
    assert delete_response.status_code == 204

    # Verify all instances for that series are gone from the database
    meta_response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/metadata",
        headers={"Accept": "application/dicom+json"},
    )
    assert meta_response.status_code == 404


def test_delete_nonexistent_series_returns_404(client: TestClient):
    """DELETE for a non-existent series returns 404 Not Found."""
    study_uid = "1.2.826.0.1.3680043.8.498.60000000000000000000000000000000011"
    nonexistent_series = "1.2.826.0.1.3680043.8.498.69999999999999999999999999999999999"

    response = client.delete(f"/v2/studies/{study_uid}/series/{nonexistent_series}")
    assert response.status_code == 404


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. Instance Deletion (4 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_delete_instance_preserves_series(client: TestClient):
    """DELETE /v2/.../instances/{instance} only removes one instance from the series."""
    study_uid = "1.2.826.0.1.3680043.8.498.40000000000000000000000000000000001"
    series_uid = "1.2.826.0.1.3680043.8.498.40000000000000000000000000000000002"
    sop_to_delete = "1.2.826.0.1.3680043.8.498.40000000000000000000000000000000003"
    sop_to_keep = "1.2.826.0.1.3680043.8.498.40000000000000000000000000000000004"

    # Store 2 instances in the same series
    dcm_delete = DicomFactory.create_ct_image(
        patient_id="DEL-INST-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_to_delete,
        with_pixel_data=False,
    )
    store_dicom(client, dcm_delete)

    dcm_keep = DicomFactory.create_ct_image(
        patient_id="DEL-INST-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_to_keep,
        with_pixel_data=False,
    )
    store_dicom(client, dcm_keep)

    # Delete only the first instance
    delete_response = client.delete(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_to_delete}"
    )
    assert delete_response.status_code == 204

    # Verify the deleted instance returns 404
    retrieve_response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_to_delete}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )
    assert retrieve_response.status_code == 404

    # Verify the kept instance still exists
    retrieve_response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_to_keep}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )
    assert retrieve_response.status_code == 200
    assert retrieve_response.content.count(b"DICM") == 1

    # Verify the series still shows 1 instance in QIDO-RS
    series_response = client.get(f"/v2/studies/{study_uid}/series/{series_uid}/instances")
    assert series_response.status_code == 200
    instances = series_response.json()
    assert len(instances) == 1


def test_delete_instance_removes_file(client: TestClient):
    """DELETE /v2/.../instances/{instance} removes the DCM file from disk."""
    study_uid = "1.2.826.0.1.3680043.8.498.40000000000000000000000000000000005"
    series_uid = "1.2.826.0.1.3680043.8.498.40000000000000000000000000000000006"
    sop_uid = "1.2.826.0.1.3680043.8.498.40000000000000000000000000000000007"

    dcm = DicomFactory.create_ct_image(
        patient_id="DEL-INST-FS-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    # Verify file exists on disk
    storage_dir = get_storage_dir(client)
    file_path = os.path.join(storage_dir, study_uid, series_uid, f"{sop_uid}.dcm")
    assert os.path.exists(file_path), "File should exist before deletion"

    # Delete instance
    delete_response = client.delete(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}"
    )
    assert delete_response.status_code == 204

    # Verify file is removed from disk
    assert not os.path.exists(file_path), "File should be removed after instance deletion"


def test_delete_instance_removes_db_entry(client: TestClient):
    """DELETE /v2/.../instances/{instance} removes the DB record."""
    study_uid = "1.2.826.0.1.3680043.8.498.40000000000000000000000000000000008"
    series_uid = "1.2.826.0.1.3680043.8.498.40000000000000000000000000000000009"
    sop_uid = "1.2.826.0.1.3680043.8.498.40000000000000000000000000000000010"

    dcm = DicomFactory.create_ct_image(
        patient_id="DEL-INST-DB-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    # Verify instance is in the database (via metadata endpoint)
    meta_response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/metadata",
        headers={"Accept": "application/dicom+json"},
    )
    assert meta_response.status_code == 200
    metadata = meta_response.json()
    assert len(metadata) == 1
    assert metadata[0]["00080018"]["Value"][0] == sop_uid

    # Delete instance
    delete_response = client.delete(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}"
    )
    assert delete_response.status_code == 204

    # Verify instance is removed from the database
    meta_response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/metadata",
        headers={"Accept": "application/dicom+json"},
    )
    assert meta_response.status_code == 404


def test_delete_nonexistent_instance_returns_404(client: TestClient):
    """DELETE for a non-existent instance returns 404 Not Found."""
    study_uid = "1.2.826.0.1.3680043.8.498.40000000000000000000000000000000011"
    series_uid = "1.2.826.0.1.3680043.8.498.40000000000000000000000000000000012"
    nonexistent_sop = "1.2.826.0.1.3680043.8.498.49999999999999999999999999999999999"

    response = client.delete(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{nonexistent_sop}"
    )
    assert response.status_code == 404
