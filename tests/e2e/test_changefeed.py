"""E2E tests: Change Feed workflow.

Flow:
  1. Store a DICOM instance via STOW-RS.
  2. Verify a change feed entry appears with action=create.
  3. Delete the instance.
  4. Verify the change feed reflects the delete state.

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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_dicom_bytes() -> tuple[bytes, str, str, str]:
    """Create a minimal DICOM file; return (bytes, study_uid, series_uid, sop_uid)."""
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
    ds.PatientID = "CF-E2E-001"
    ds.PatientName = "ChangeFeed^Test"
    ds.StudyDate = "20260214"
    ds.StudyTime = "120000"
    ds.Modality = "CT"
    ds.InstanceNumber = 1
    ds.SeriesNumber = 1

    buf = BytesIO()
    ds.save_as(buf)
    return buf.getvalue(), study_uid, series_uid, sop_uid


def _stow(client: httpx.Client, dcm_bytes: bytes) -> httpx.Response:
    """POST a single DICOM instance via STOW-RS."""
    boundary = "cf-e2e-boundary"
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
# Module-scoped fixture — one DICOM instance shared across the test module
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cf_instance(http_client: httpx.Client) -> dict:
    """Store a DICOM instance and return its UIDs.

    Scoped to module so the store, feed-check, delete, and post-delete
    assertions all operate on the same instance.  The delete test cleans up.
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
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_changefeed_returns_list(http_client: httpx.Client, cf_instance: dict) -> None:
    """GET /v2/changefeed returns a non-empty JSON array after a store."""
    response = http_client.get("/v2/changefeed")
    assert (
        response.status_code == 200
    ), f"GET /v2/changefeed failed: {response.status_code} {response.text}"
    data = response.json()
    assert isinstance(data, list), "Expected change feed response to be a list"
    assert len(data) >= 1, "Expected at least one change feed entry after STOW"


@pytest.mark.e2e
def test_changefeed_entry_structure(http_client: httpx.Client, cf_instance: dict) -> None:
    """Change feed entries contain required Azure API fields."""
    response = http_client.get("/v2/changefeed")
    assert response.status_code == 200

    entries = response.json()
    assert len(entries) >= 1

    entry = entries[0]
    required_fields = ["Sequence", "StudyInstanceUID", "Action", "State", "Timestamp"]
    for field in required_fields:
        assert field in entry, f"Change feed entry missing required field: {field}"


@pytest.mark.e2e
def test_changefeed_sequence_is_positive(http_client: httpx.Client, cf_instance: dict) -> None:
    """Change feed sequence numbers are positive integers."""
    response = http_client.get("/v2/changefeed")
    assert response.status_code == 200

    for entry in response.json():
        assert isinstance(entry["Sequence"], int), "Sequence must be an integer"
        assert entry["Sequence"] > 0, "Sequence must be positive"


@pytest.mark.e2e
def test_changefeed_create_entry_exists(http_client: httpx.Client, cf_instance: dict) -> None:
    """Change feed contains a create entry for the stored instance."""
    study_uid = cf_instance["study_uid"]

    response = http_client.get("/v2/changefeed")
    assert response.status_code == 200

    entries = response.json()
    matching = [
        e for e in entries if e.get("StudyInstanceUID") == study_uid and e.get("Action") == "create"
    ]
    assert len(matching) >= 1, (
        f"No create entry found for study {study_uid} in change feed. "
        f"Entries: {[e.get('StudyInstanceUID') for e in entries]}"
    )


@pytest.mark.e2e
def test_changefeed_action_values_valid(http_client: httpx.Client, cf_instance: dict) -> None:
    """All change feed action values are from the allowed set."""
    response = http_client.get("/v2/changefeed")
    assert response.status_code == 200

    valid_actions = {"create", "update", "delete"}
    for entry in response.json():
        assert (
            entry.get("Action") in valid_actions
        ), f"Unexpected Action value: {entry.get('Action')}"


@pytest.mark.e2e
def test_changefeed_state_values_valid(http_client: httpx.Client, cf_instance: dict) -> None:
    """All change feed state values are from the allowed set."""
    response = http_client.get("/v2/changefeed")
    assert response.status_code == 200

    valid_states = {"current", "replaced", "deleted"}
    for entry in response.json():
        assert entry.get("State") in valid_states, f"Unexpected State value: {entry.get('State')}"


@pytest.mark.e2e
def test_changefeed_latest_returns_entry(http_client: httpx.Client, cf_instance: dict) -> None:
    """GET /v2/changefeed/latest returns the most recent entry."""
    response = http_client.get("/v2/changefeed/latest")
    assert (
        response.status_code == 200
    ), f"GET /v2/changefeed/latest failed: {response.status_code} {response.text}"
    data = response.json()
    assert data is not None
    assert "Sequence" in data, "Latest change feed entry missing Sequence field"


@pytest.mark.e2e
def test_changefeed_latest_sequence_matches_max(
    http_client: httpx.Client, cf_instance: dict
) -> None:
    """Sequence in /latest matches the highest sequence in the full feed."""
    feed_response = http_client.get("/v2/changefeed")
    assert feed_response.status_code == 200
    entries = feed_response.json()

    latest_response = http_client.get("/v2/changefeed/latest")
    assert latest_response.status_code == 200
    latest = latest_response.json()

    if entries:
        max_seq = max(e["Sequence"] for e in entries)
        assert (
            latest["Sequence"] == max_seq
        ), f"Latest sequence {latest['Sequence']} does not match max feed sequence {max_seq}"


@pytest.mark.e2e
def test_changefeed_time_window_filter(http_client: httpx.Client, cf_instance: dict) -> None:
    """startTime/endTime query parameters are accepted without error."""
    response = http_client.get(
        "/v2/changefeed",
        params={
            "startTime": "2020-01-01T00:00:00Z",
            "endTime": "2099-12-31T23:59:59Z",
        },
    )
    assert (
        response.status_code == 200
    ), f"Change feed with time window failed: {response.status_code} {response.text}"
    assert isinstance(response.json(), list)


@pytest.mark.e2e
def test_changefeed_pagination(http_client: httpx.Client, cf_instance: dict) -> None:
    """offset and limit query parameters are accepted without error."""
    response = http_client.get("/v2/changefeed", params={"offset": 0, "limit": 10})
    assert (
        response.status_code == 200
    ), f"Change feed pagination failed: {response.status_code} {response.text}"
    data = response.json()
    assert isinstance(data, list)
    assert len(data) <= 10, "Response should respect the limit parameter"


@pytest.mark.e2e
def test_changefeed_after_delete_reflects_delete(
    http_client: httpx.Client, cf_instance: dict
) -> None:
    """After deleting the study, the change feed latest shows action=delete.

    NOTE: This test deletes the stored instance — it must run last in this
    module because ``cf_instance`` is module-scoped.
    """
    study_uid = cf_instance["study_uid"]

    # Delete the study
    delete_response = http_client.delete(f"/v2/studies/{study_uid}")
    assert (
        delete_response.status_code == 204
    ), f"DELETE failed: {delete_response.status_code} {delete_response.text}"

    # Latest change feed entry should now be a delete action
    latest_response = http_client.get("/v2/changefeed/latest")
    assert latest_response.status_code == 200
    latest = latest_response.json()

    assert latest.get("Action") == "delete", (
        f"Expected latest change feed action to be 'delete' after deletion, "
        f"got '{latest.get('Action')}'"
    )
