"""E2E tests: STOW → WADO → QIDO → DELETE workflow.

Ports smoke_test.py to proper pytest.  Requires a live service running via
docker-compose (or any DICOMweb v2-compatible target).

Run with:
    pytest tests/e2e/ -m e2e
    DICOM_E2E_BASE_URL=http://my-host:8080 pytest tests/e2e/ -m e2e

Tests are skipped automatically when the service is not reachable.
"""

import uuid
from io import BytesIO

import httpx
import pydicom
import pytest
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_dicom_bytes(
    *,
    patient_id: str = "E2E-001",
    patient_name: str = "E2E^Patient",
    modality: str = "CT",
) -> tuple[bytes, str, str, str]:
    """Create a minimal, valid DICOM file and return (bytes, study_uid, series_uid, sop_uid)."""
    study_uid = generate_uid()
    series_uid = generate_uid()
    sop_uid = generate_uid()

    file_meta = pydicom.Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT Image Storage
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(
        filename_or_obj=BytesIO(),
        dataset=Dataset(),
        file_meta=file_meta,
        preamble=b"\x00" * 128,
    )
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.PatientID = patient_id
    ds.PatientName = patient_name
    ds.StudyDate = "20260214"
    ds.StudyTime = "120000"
    ds.Modality = modality
    ds.AccessionNumber = f"ACC-{uuid.uuid4().hex[:8]}"
    ds.StudyDescription = "E2E Workflow Test"
    ds.SeriesDescription = "Test Series"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1

    buffer = BytesIO()
    ds.save_as(buffer)
    return buffer.getvalue(), study_uid, series_uid, sop_uid


def _stow(client: httpx.Client, dcm_bytes: bytes) -> httpx.Response:
    """POST a single DICOM instance via STOW-RS."""
    boundary = "e2e-boundary"
    body = (
        (f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n").encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return client.post(
        "/v2/studies",
        content=body,
        headers={
            "Content-Type": (f"multipart/related; type=application/dicom; boundary={boundary}")
        },
    )


# ---------------------------------------------------------------------------
# Session-scoped DICOM fixture — stored once, reused across related tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stored_instance(http_client: httpx.Client) -> dict:
    """Store one DICOM instance and return its UIDs.

    Scoped to module so all tests in this file share the same instance.
    The DELETE test cleans it up at the end.
    """
    dcm_bytes, study_uid, series_uid, sop_uid = _build_dicom_bytes()
    response = _stow(http_client, dcm_bytes)
    assert response.status_code in (
        200,
        202,
    ), f"STOW failed during fixture setup: {response.status_code} {response.text}"
    return {
        "study_uid": study_uid,
        "series_uid": series_uid,
        "sop_uid": sop_uid,
    }


# ---------------------------------------------------------------------------
# Individual tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_health(http_client: httpx.Client) -> None:
    """Service health endpoint returns 200 with status=healthy."""
    response = http_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "healthy"


@pytest.mark.e2e
def test_stow_rs(http_client: httpx.Client) -> None:
    """STOW-RS stores a DICOM instance and returns 200 or 202."""
    dcm_bytes, _, _, _ = _build_dicom_bytes(patient_id="E2E-STOW-ONLY")
    response = _stow(http_client, dcm_bytes)
    assert response.status_code in (
        200,
        202,
    ), f"STOW-RS failed: {response.status_code} {response.text}"


@pytest.mark.e2e
def test_wado_metadata(http_client: httpx.Client, stored_instance: dict) -> None:
    """WADO-RS metadata returns the stored instance."""
    study_uid = stored_instance["study_uid"]
    response = http_client.get(f"/v2/studies/{study_uid}/metadata")
    assert (
        response.status_code == 200
    ), f"WADO metadata failed: {response.status_code} {response.text}"
    data = response.json()
    assert len(data) >= 1, "Expected at least one instance in metadata response"
    # Confirm StudyInstanceUID tag (0020000D) is present in first result
    assert "0020000D" in data[0], "StudyInstanceUID tag missing from metadata"
    assert data[0]["0020000D"]["Value"][0] == study_uid


@pytest.mark.e2e
def test_wado_instance_retrieval(http_client: httpx.Client, stored_instance: dict) -> None:
    """WADO-RS instance retrieval returns multipart/related DICOM."""
    study_uid = stored_instance["study_uid"]
    series_uid = stored_instance["series_uid"]
    sop_uid = stored_instance["sop_uid"]
    response = http_client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )
    assert (
        response.status_code == 200
    ), f"WADO instance retrieval failed: {response.status_code} {response.text}"
    content_type = response.headers.get("content-type", "")
    assert (
        "multipart/related" in content_type
    ), f"Expected multipart/related content-type, got: {content_type}"


@pytest.mark.e2e
def test_qido_search_all_studies(http_client: httpx.Client, stored_instance: dict) -> None:
    """QIDO-RS study list returns at least the stored study."""
    response = http_client.get("/v2/studies")
    assert (
        response.status_code == 200
    ), f"QIDO all studies failed: {response.status_code} {response.text}"
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1, "Expected at least one study in QIDO response"


@pytest.mark.e2e
def test_qido_search_by_patient_id(http_client: httpx.Client, stored_instance: dict) -> None:
    """QIDO-RS finds the study when searching by PatientID."""
    response = http_client.get("/v2/studies", params={"PatientID": "E2E-001"})
    assert (
        response.status_code == 200
    ), f"QIDO PatientID search failed: {response.status_code} {response.text}"
    data = response.json()
    assert isinstance(data, list)
    # At least one result should match our study
    assert len(data) >= 1, "Expected at least one study matching PatientID=E2E-001"
    study_uids = [entry.get("0020000D", {}).get("Value", [None])[0] for entry in data]
    assert (
        stored_instance["study_uid"] in study_uids
    ), f"Stored study {stored_instance['study_uid']} not found in QIDO results"


@pytest.mark.e2e
def test_qido_wildcard_search(http_client: httpx.Client) -> None:
    """QIDO-RS wildcard search returns 200 without error."""
    response = http_client.get("/v2/studies", params={"PatientID": "E2E-*"})
    assert (
        response.status_code == 200
    ), f"QIDO wildcard search failed: {response.status_code} {response.text}"


@pytest.mark.e2e
def test_change_feed_after_store(http_client: httpx.Client, stored_instance: dict) -> None:
    """Change feed contains at least one create entry after STOW."""
    response = http_client.get("/v2/changefeed")
    assert (
        response.status_code == 200
    ), f"Change feed failed: {response.status_code} {response.text}"
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1, "Expected at least one change feed entry"
    entry = data[0]
    assert "Sequence" in entry, "Change feed entry missing Sequence field"
    assert "Action" in entry, "Change feed entry missing Action field"
    assert entry["Action"] in ("create", "update", "delete")


@pytest.mark.e2e
def test_change_feed_latest(http_client: httpx.Client, stored_instance: dict) -> None:
    """Change feed /latest endpoint returns the latest sequence entry."""
    response = http_client.get("/v2/changefeed/latest")
    assert (
        response.status_code == 200
    ), f"Change feed latest failed: {response.status_code} {response.text}"
    data = response.json()
    assert data is not None
    assert "Sequence" in data, "Change feed latest entry missing Sequence field"


@pytest.mark.e2e
def test_delete_study(http_client: httpx.Client, stored_instance: dict) -> None:
    """DELETE study returns 204 and subsequent metadata returns 404."""
    study_uid = stored_instance["study_uid"]

    delete_response = http_client.delete(f"/v2/studies/{study_uid}")
    assert (
        delete_response.status_code == 204
    ), f"DELETE failed: {delete_response.status_code} {delete_response.text}"

    # Verify the study is gone
    get_response = http_client.get(f"/v2/studies/{study_uid}/metadata")
    assert (
        get_response.status_code == 404
    ), f"Expected 404 after deletion, got {get_response.status_code}"


@pytest.mark.e2e
def test_delete_reflected_in_change_feed(http_client: httpx.Client, stored_instance: dict) -> None:
    """Change feed /latest shows a delete action after study deletion.

    NOTE: This test depends on test_delete_study running first.
    Both share the same module-scoped stored_instance fixture.
    """
    response = http_client.get("/v2/changefeed/latest")
    assert response.status_code == 200
    data = response.json()
    assert (
        data["Action"] == "delete"
    ), f"Expected latest change feed action to be 'delete', got '{data['Action']}'"


# ---------------------------------------------------------------------------
# Extended Query Tags CRUD (self-contained, no stored_instance dependency)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_extended_query_tags_crud(http_client: httpx.Client) -> None:
    """Extended query tags: list, add, get, delete."""
    tag_path = "00101002"

    # Clean up any leftover tag from a previous partial run
    http_client.delete(f"/v2/extendedquerytags/{tag_path}")

    # List
    response = http_client.get("/v2/extendedquerytags")
    assert response.status_code == 200

    # Add
    response = http_client.post(
        "/v2/extendedquerytags",
        json={"tags": [{"path": tag_path, "vr": "SQ", "level": "Study"}]},
    )
    assert (
        response.status_code == 202
    ), f"Add extended query tag failed: {response.status_code} {response.text}"
    assert response.json().get("status") == "succeeded"

    # Get
    response = http_client.get(f"/v2/extendedquerytags/{tag_path}")
    assert response.status_code == 200

    # Delete
    response = http_client.delete(f"/v2/extendedquerytags/{tag_path}")
    assert response.status_code == 204
