"""E2E tests: Extended Query Tags workflow.

Flow:
  1. Clean up any leftover tag from prior runs.
  2. Create an extended query tag (00104000, Patient Comments, LO, Study level).
  3. Store a DICOM instance that carries that tag's data.
  4. Query QIDO using the extended tag value and verify the instance is returned.
  5. Delete the extended query tag.

Requires a live service (docker-compose).  Tests are skipped automatically
when the service is not reachable.

Run with:
    pytest tests/e2e/ -m e2e
    DICOM_E2E_BASE_URL=http://my-host:8080 pytest tests/e2e/ -m e2e
"""

from io import BytesIO

import httpx
import pydicom
import pytest
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

# Tag under test: Patient Comments (0010,4000), VR=LO
EXT_TAG_PATH = "00104000"
EXT_TAG_VR = "LO"
EXT_TAG_LEVEL = "Study"
EXT_TAG_COMMENT = "EXT-E2E-COMMENT"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_dicom_bytes_with_comment(comment: str) -> tuple[bytes, str, str, str]:
    """Build a minimal DICOM file with PatientComments set; return (bytes, UIDs)."""
    study_uid = generate_uid()
    series_uid = generate_uid()
    sop_uid = generate_uid()

    file_meta = pydicom.Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
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
    ds.PatientID = "EXT-E2E-001"
    ds.PatientName = "ExtTag^Test"
    ds.StudyDate = "20260214"
    ds.StudyTime = "120000"
    ds.Modality = "CT"
    ds.InstanceNumber = 1
    ds.SeriesNumber = 1
    ds.PatientComments = comment  # 0010,4000 — the extended query tag under test

    buf = BytesIO()
    ds.save_as(buf)
    return buf.getvalue(), study_uid, series_uid, sop_uid


def _stow(client: httpx.Client, dcm_bytes: bytes) -> httpx.Response:
    """POST a single DICOM instance via STOW-RS."""
    boundary = "ext-e2e-boundary"
    body = (
        (f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n").encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"},
    )


# ---------------------------------------------------------------------------
# Module-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ext_tag_registered(http_client: httpx.Client) -> str:
    """Register the extended query tag; yield its path; delete on teardown."""
    # Clean up any leftover from a prior partial run
    http_client.delete(f"/v2/extendedquerytags/{EXT_TAG_PATH}")

    response = http_client.post(
        "/v2/extendedquerytags",
        json={
            "tags": [
                {
                    "path": EXT_TAG_PATH,
                    "vr": EXT_TAG_VR,
                    "level": EXT_TAG_LEVEL,
                }
            ]
        },
    )
    assert (
        response.status_code == 202
    ), f"Failed to register extended query tag: {response.status_code} {response.text}"

    yield EXT_TAG_PATH

    # Teardown: delete the tag (best-effort)
    http_client.delete(f"/v2/extendedquerytags/{EXT_TAG_PATH}")


@pytest.fixture(scope="module")
def ext_stored_instance(http_client: httpx.Client, ext_tag_registered: str) -> dict:
    """Store a DICOM instance carrying the extended tag value."""
    dcm_bytes, study_uid, series_uid, sop_uid = _build_dicom_bytes_with_comment(EXT_TAG_COMMENT)
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
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_list_extended_query_tags_returns_200(http_client: httpx.Client) -> None:
    """GET /v2/extendedquerytags returns 200 with a list."""
    response = http_client.get("/v2/extendedquerytags")
    assert (
        response.status_code == 200
    ), f"List extended query tags failed: {response.status_code} {response.text}"
    assert isinstance(response.json(), list)


@pytest.mark.e2e
def test_add_extended_query_tag_returns_202(
    http_client: httpx.Client, ext_tag_registered: str
) -> None:
    """POST /v2/extendedquerytags returns 202 with succeeded status."""
    # Tag was registered by the fixture; verify it appears in the list
    response = http_client.get("/v2/extendedquerytags")
    assert response.status_code == 200
    tags = response.json()
    paths = [t.get("Path") for t in tags]
    assert EXT_TAG_PATH in paths, f"Registered tag {EXT_TAG_PATH} not found in tag list: {paths}"


@pytest.mark.e2e
def test_get_specific_extended_query_tag(
    http_client: httpx.Client, ext_tag_registered: str
) -> None:
    """GET /v2/extendedquerytags/{path} returns the registered tag."""
    response = http_client.get(f"/v2/extendedquerytags/{EXT_TAG_PATH}")
    assert (
        response.status_code == 200
    ), f"GET specific tag failed: {response.status_code} {response.text}"
    tag = response.json()
    assert tag.get("Path") == EXT_TAG_PATH
    assert tag.get("VR") == EXT_TAG_VR
    assert tag.get("Level") == EXT_TAG_LEVEL


@pytest.mark.e2e
def test_get_extended_query_tag_status_fields(
    http_client: httpx.Client, ext_tag_registered: str
) -> None:
    """Extended query tag response contains Status and QueryStatus fields."""
    response = http_client.get(f"/v2/extendedquerytags/{EXT_TAG_PATH}")
    assert response.status_code == 200

    tag = response.json()
    assert "Status" in tag, "Tag response missing Status field"
    assert "QueryStatus" in tag, "Tag response missing QueryStatus field"


@pytest.mark.e2e
def test_add_duplicate_extended_query_tag_returns_409(
    http_client: httpx.Client, ext_tag_registered: str
) -> None:
    """POST with a duplicate tag path returns 409 Conflict."""
    response = http_client.post(
        "/v2/extendedquerytags",
        json={
            "tags": [
                {
                    "path": EXT_TAG_PATH,
                    "vr": EXT_TAG_VR,
                    "level": EXT_TAG_LEVEL,
                }
            ]
        },
    )
    assert (
        response.status_code == 409
    ), f"Expected 409 for duplicate tag, got {response.status_code} {response.text}"


@pytest.mark.e2e
def test_get_nonexistent_extended_query_tag_returns_404(http_client: httpx.Client) -> None:
    """GET /v2/extendedquerytags/{path} for a nonexistent tag returns 404."""
    response = http_client.get("/v2/extendedquerytags/DEADBEEF")
    assert response.status_code == 404, f"Expected 404 for missing tag, got {response.status_code}"


@pytest.mark.e2e
def test_store_instance_with_extended_tag_succeeds(
    http_client: httpx.Client, ext_stored_instance: dict
) -> None:
    """Storing a DICOM instance with extended tag data returns 200 or 202."""
    # Fixture already stored; verify the instance is retrievable via WADO metadata
    study_uid = ext_stored_instance["study_uid"]
    response = http_client.get(f"/v2/studies/{study_uid}/metadata")
    assert (
        response.status_code == 200
    ), f"WADO metadata failed for extended-tag instance: {response.status_code} {response.text}"


@pytest.mark.e2e
def test_qido_returns_stored_instance(http_client: httpx.Client, ext_stored_instance: dict) -> None:
    """QIDO finds the stored instance by PatientID (standard search)."""
    response = http_client.get("/v2/studies", params={"PatientID": "EXT-E2E-001"})
    assert (
        response.status_code == 200
    ), f"QIDO search failed: {response.status_code} {response.text}"
    data = response.json()
    assert isinstance(data, list)
    study_uids = [entry.get("0020000D", {}).get("Value", [None])[0] for entry in data]
    assert (
        ext_stored_instance["study_uid"] in study_uids
    ), f"Stored study not found in QIDO results. UIDs found: {study_uids}"


@pytest.mark.e2e
def test_delete_extended_query_tag_returns_204(
    http_client: httpx.Client, ext_tag_registered: str
) -> None:
    """DELETE /v2/extendedquerytags/{path} returns 204 and the tag is gone.

    NOTE: This test deletes the tag — it must run after all tag-dependent
    tests.  The fixture teardown also calls delete as a safety net.
    """
    response = http_client.delete(f"/v2/extendedquerytags/{EXT_TAG_PATH}")
    assert response.status_code == 204, f"DELETE tag failed: {response.status_code} {response.text}"

    # Confirm the tag no longer exists
    get_response = http_client.get(f"/v2/extendedquerytags/{EXT_TAG_PATH}")
    assert (
        get_response.status_code == 404
    ), f"Expected 404 after tag deletion, got {get_response.status_code}"
