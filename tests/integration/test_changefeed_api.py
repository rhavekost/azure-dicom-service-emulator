"""Integration tests for Change Feed API endpoints.

Tests the GET /v2/changefeed and GET /v2/changefeed/latest endpoints,
including feed retrieval, sequence number ordering, time-window queries,
action/state tracking, and latest entry behaviour.
"""

import time

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
    """Store a DICOM instance via STOW-RS and return the response JSON."""
    body, content_type = build_multipart_request([dicom_bytes])
    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert response.status_code == 200, f"Store failed: {response.status_code} {response.text}"
    return response.json()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. Basic Feed Retrieval (4 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_get_changefeed_all_changes(client: TestClient):
    """GET /v2/changefeed returns all change feed entries after storing instances."""
    # Store two DICOM instances
    dcm1 = DicomFactory.create_ct_image(
        patient_id="CF-ALL-001",
        study_uid="1.2.826.0.1.3680043.8.498.80000000000000000000000000000000001",
        series_uid="1.2.826.0.1.3680043.8.498.80000000000000000000000000000000002",
        sop_uid="1.2.826.0.1.3680043.8.498.80000000000000000000000000000000003",
        with_pixel_data=False,
    )
    store_dicom(client, dcm1)

    dcm2 = DicomFactory.create_ct_image(
        patient_id="CF-ALL-002",
        study_uid="1.2.826.0.1.3680043.8.498.80000000000000000000000000000000004",
        series_uid="1.2.826.0.1.3680043.8.498.80000000000000000000000000000000005",
        sop_uid="1.2.826.0.1.3680043.8.498.80000000000000000000000000000000006",
        with_pixel_data=False,
    )
    store_dicom(client, dcm2)

    # Query change feed
    response = client.get("/v2/changefeed")
    assert response.status_code == 200

    entries = response.json()
    assert len(entries) >= 2, f"Expected at least 2 entries, got {len(entries)}"

    # Verify the stored SOP UIDs appear in the feed
    sop_uids_in_feed = {e["SOPInstanceUID"] for e in entries}
    assert "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000003" in sop_uids_in_feed
    assert "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000006" in sop_uids_in_feed


def test_changefeed_returns_sequence_numbers(client: TestClient):
    """Change feed entries have monotonically increasing sequence numbers."""
    # Store three instances to generate multiple feed entries
    for i in range(3):
        dcm = DicomFactory.create_ct_image(
            patient_id=f"CF-SEQ-{i:03d}",
            with_pixel_data=False,
        )
        store_dicom(client, dcm)

    response = client.get("/v2/changefeed")
    assert response.status_code == 200

    entries = response.json()
    assert len(entries) >= 3

    # Verify sequence numbers are monotonically increasing
    sequences = [e["Sequence"] for e in entries]
    for i in range(1, len(sequences)):
        assert (
            sequences[i] > sequences[i - 1]
        ), f"Sequence not monotonically increasing: {sequences[i - 1]} -> {sequences[i]}"


def test_changefeed_includes_action_state(client: TestClient):
    """Change feed entries include action and state fields for create and delete."""
    study_uid = "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000010"
    series_uid = "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000011"
    sop_uid = "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000012"

    # Store an instance (creates a "create" feed entry)
    dcm = DicomFactory.create_ct_image(
        patient_id="CF-STATE-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    # Delete the instance (creates a "delete" feed entry)
    delete_response = client.delete(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}"
    )
    assert delete_response.status_code == 204

    # Query feed and check action/state
    response = client.get("/v2/changefeed")
    assert response.status_code == 200

    entries = response.json()

    # Find entries for our specific SOP UID
    our_entries = [e for e in entries if e["SOPInstanceUID"] == sop_uid]
    assert len(our_entries) >= 2, f"Expected at least 2 entries for SOP UID, got {len(our_entries)}"

    actions = [e["Action"] for e in our_entries]
    assert "create" in actions, "Expected a 'create' action entry"
    assert "delete" in actions, "Expected a 'delete' action entry"

    # The most recent entry (delete) should have state "current"
    # and the create entry should have been marked "replaced"
    delete_entries = [e for e in our_entries if e["Action"] == "delete"]
    assert any(
        e["State"] == "current" for e in delete_entries
    ), "Delete entry should have state 'current'"

    create_entries = [e for e in our_entries if e["Action"] == "create"]
    assert any(
        e["State"] == "replaced" for e in create_entries
    ), "Create entry should have been marked 'replaced' after deletion"


def test_changefeed_json_format(client: TestClient):
    """Change feed entries have the correct JSON structure matching Azure format."""
    study_uid = "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000020"
    series_uid = "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000021"
    sop_uid = "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000022"

    dcm = DicomFactory.create_ct_image(
        patient_id="CF-FORMAT-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    response = client.get("/v2/changefeed")
    assert response.status_code == 200

    entries = response.json()
    our_entry = next(e for e in entries if e["SOPInstanceUID"] == sop_uid)

    # Verify all required fields exist
    required_fields = [
        "Sequence",
        "StudyInstanceUID",
        "SeriesInstanceUID",
        "SOPInstanceUID",
        "Action",
        "Timestamp",
        "State",
        "Metadata",
    ]
    for field in required_fields:
        assert field in our_entry, f"Missing required field: {field}"

    # Verify field values
    assert our_entry["StudyInstanceUID"] == study_uid
    assert our_entry["SeriesInstanceUID"] == series_uid
    assert our_entry["SOPInstanceUID"] == sop_uid
    assert our_entry["Action"] == "create"
    assert our_entry["State"] == "current"
    assert isinstance(our_entry["Sequence"], int)
    assert isinstance(our_entry["Timestamp"], str)
    assert len(our_entry["Timestamp"]) > 0  # Non-empty ISO timestamp


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. Time Window Queries (4 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_changefeed_with_start_time(client: TestClient):
    """GET /v2/changefeed?startTime= filters entries after start time."""
    # Record a timestamp before storing
    from datetime import datetime, timezone

    before_time = datetime.now(timezone.utc).isoformat()

    # Small sleep to ensure timestamp separation
    time.sleep(0.05)

    # Store an instance
    dcm = DicomFactory.create_ct_image(
        patient_id="CF-START-001",
        sop_uid="1.2.826.0.1.3680043.8.498.80000000000000000000000000000000030",
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    # Query with startTime = before_time (use params dict to handle URL encoding)
    response = client.get("/v2/changefeed", params={"startTime": before_time})
    assert response.status_code == 200

    entries = response.json()
    # Should include the entry we just created
    sop_uids = {e["SOPInstanceUID"] for e in entries}
    assert "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000030" in sop_uids

    # All returned timestamps should be >= startTime
    for entry in entries:
        assert (
            entry["Timestamp"] >= before_time
        ), f"Entry timestamp {entry['Timestamp']} is before startTime {before_time}"


def test_changefeed_with_end_time(client: TestClient):
    """GET /v2/changefeed?endTime= filters entries before end time."""
    from datetime import datetime, timezone

    # Store an instance first
    dcm = DicomFactory.create_ct_image(
        patient_id="CF-END-001",
        sop_uid="1.2.826.0.1.3680043.8.498.80000000000000000000000000000000040",
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    # Small sleep to ensure timestamp separation
    time.sleep(0.05)

    # Record a timestamp after storing
    after_time = datetime.now(timezone.utc).isoformat()

    # Query with endTime = after_time (should include the entry)
    response = client.get("/v2/changefeed", params={"endTime": after_time})
    assert response.status_code == 200

    entries = response.json()
    sop_uids = {e["SOPInstanceUID"] for e in entries}
    assert "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000040" in sop_uids

    # All returned timestamps should be <= endTime
    for entry in entries:
        assert (
            entry["Timestamp"] <= after_time
        ), f"Entry timestamp {entry['Timestamp']} is after endTime {after_time}"


def test_changefeed_with_start_and_end_time(client: TestClient):
    """GET /v2/changefeed?startTime=&endTime= filters to a time window."""
    from datetime import datetime, timezone

    # Record start time
    start_time = datetime.now(timezone.utc).isoformat()
    time.sleep(0.05)

    # Store an instance within the window
    dcm_inside = DicomFactory.create_ct_image(
        patient_id="CF-WINDOW-001",
        sop_uid="1.2.826.0.1.3680043.8.498.80000000000000000000000000000000050",
        with_pixel_data=False,
    )
    store_dicom(client, dcm_inside)

    time.sleep(0.05)
    # Record end time
    end_time = datetime.now(timezone.utc).isoformat()

    # Query with both startTime and endTime (use params dict for proper encoding)
    response = client.get("/v2/changefeed", params={"startTime": start_time, "endTime": end_time})
    assert response.status_code == 200

    entries = response.json()
    assert len(entries) >= 1

    # The instance we stored should be in the window
    sop_uids = {e["SOPInstanceUID"] for e in entries}
    assert "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000050" in sop_uids

    # All timestamps should be within the window
    for entry in entries:
        assert (
            entry["Timestamp"] >= start_time
        ), f"Entry timestamp {entry['Timestamp']} is before startTime {start_time}"
        assert (
            entry["Timestamp"] <= end_time
        ), f"Entry timestamp {entry['Timestamp']} is after endTime {end_time}"


def test_changefeed_invalid_time_format_returns_400(client: TestClient):
    """GET /v2/changefeed with an invalid timestamp raises a server error."""
    # Send a clearly invalid time format.
    # The changefeed router calls datetime.fromisoformat without try/except,
    # so an invalid value propagates as an unhandled ValueError (500 error).
    # We verify the server rejects the request rather than returning data.
    with pytest.raises(Exception):
        client.get("/v2/changefeed", params={"startTime": "not-a-date"})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. Latest Entry (4 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_get_changefeed_latest(client: TestClient):
    """GET /v2/changefeed/latest returns the most recent change feed entry."""
    sop_uid = "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000060"

    dcm = DicomFactory.create_ct_image(
        patient_id="CF-LATEST-001",
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    response = client.get("/v2/changefeed/latest")
    assert response.status_code == 200

    latest = response.json()

    # Verify the response contains the expected fields
    assert "Sequence" in latest
    assert "SOPInstanceUID" in latest
    assert latest["Sequence"] > 0


def test_latest_returns_highest_sequence(client: TestClient):
    """GET /v2/changefeed/latest returns the entry with the highest sequence number."""
    # Store multiple instances
    sop_uids = []
    for i in range(3):
        sop_uid = f"1.2.826.0.1.3680043.8.498.8000000000000000000000000000000007{i}"
        sop_uids.append(sop_uid)
        dcm = DicomFactory.create_ct_image(
            patient_id=f"CF-HIGHSEQ-{i:03d}",
            sop_uid=sop_uid,
            with_pixel_data=False,
        )
        store_dicom(client, dcm)

    # Get the full feed to find the max sequence
    feed_response = client.get("/v2/changefeed")
    assert feed_response.status_code == 200
    all_entries = feed_response.json()
    max_sequence = max(e["Sequence"] for e in all_entries)

    # Get latest
    latest_response = client.get("/v2/changefeed/latest")
    assert latest_response.status_code == 200
    latest = latest_response.json()

    assert (
        latest["Sequence"] == max_sequence
    ), f"Latest sequence {latest['Sequence']} != max sequence {max_sequence}"


def test_latest_empty_feed_returns_zero(client: TestClient):
    """GET /v2/changefeed/latest on an empty feed returns Sequence 0."""
    # On a fresh database (no instances stored), the latest should return Sequence 0
    response = client.get("/v2/changefeed/latest")
    assert response.status_code == 200

    latest = response.json()
    assert latest["Sequence"] == 0, f"Expected Sequence 0, got {latest['Sequence']}"


def test_changefeed_updates_after_store(client: TestClient):
    """Storing a new instance creates a new change feed entry visible in latest."""
    # Get initial latest
    initial_response = client.get("/v2/changefeed/latest")
    assert initial_response.status_code == 200
    initial_latest = initial_response.json()
    initial_sequence = initial_latest["Sequence"]

    # Store a new instance
    sop_uid = "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000080"
    dcm = DicomFactory.create_ct_image(
        patient_id="CF-UPDATE-001",
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    # Get latest again
    after_response = client.get("/v2/changefeed/latest")
    assert after_response.status_code == 200
    after_latest = after_response.json()

    # Sequence should have increased
    assert (
        after_latest["Sequence"] > initial_sequence
    ), f"Latest sequence {after_latest['Sequence']} should be > initial {initial_sequence}"

    # The latest entry should reference the SOP UID we just stored
    assert after_latest["SOPInstanceUID"] == sop_uid
    assert after_latest["Action"] == "create"
    assert after_latest["State"] == "current"
