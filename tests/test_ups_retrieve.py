"""Tests for UPS-RS retrieve workitem endpoint (Phase 5, Task 4)."""

import pytest
from fastapi.testclient import TestClient


def test_retrieve_workitem(client: TestClient):
    """Retrieve existing workitem."""
    workitem_uid = "1.2.3.4.retrieve.1"

    # Create workitem first
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Doe^John"}]},
        "00100020": {"vr": "LO", "Value": ["PAT123"]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
        "00081195": {"vr": "UI", "Value": ["1.2.3.txn.secret"]},  # Will be added internally
    }

    # Remove transaction UID before creating (it's added internally during state changes)
    payload_for_create = {k: v for k, v in payload.items() if k != "00081195"}

    response = client.post(
        f"/v2/workitems?{workitem_uid}",
        json=payload_for_create,
        headers={"Content-Type": "application/dicom+json"}
    )
    assert response.status_code == 201

    # Retrieve workitem
    response = client.get(f"/v2/workitems/{workitem_uid}")

    assert response.status_code == 200
    data = response.json()

    # Verify expected fields
    assert data["00080018"]["Value"][0] == workitem_uid
    assert data["00100010"]["Value"][0]["Alphabetic"] == "Doe^John"
    assert data["00100020"]["Value"][0] == "PAT123"
    assert data["00741000"]["Value"][0] == "SCHEDULED"

    # CRITICAL: Transaction UID must NOT be in response (security)
    assert "00081195" not in data, "Transaction UID must never be exposed in response"


def test_retrieve_nonexistent_workitem(client: TestClient):
    """Retrieve nonexistent workitem returns 404."""
    response = client.get("/v2/workitems/9.9.9.9.9.nonexistent")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()
