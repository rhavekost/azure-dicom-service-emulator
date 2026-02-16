"""Tests for UPS-RS update workitem endpoint."""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_update_scheduled_workitem_success(client: TestClient):
    """Update SCHEDULED workitem without transaction UID - should succeed."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.100"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Doe^John"}]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Update workitem
    update = {
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Smith^Jane"}]},
        "00100020": {"vr": "LO", "Value": ["PAT-12345"]},
    }
    response = client.put(f"/v2/workitems/{workitem_uid}", json=update)
    assert response.status_code == 200

    # Verify update
    response = client.get(f"/v2/workitems/{workitem_uid}")
    assert response.status_code == 200
    dataset = response.json()
    assert dataset["00100010"]["Value"][0]["Alphabetic"] == "Smith^Jane"
    assert dataset["00100020"]["Value"][0] == "PAT-12345"


def test_update_in_progress_with_correct_txn_uid(client: TestClient):
    """Update IN PROGRESS workitem with correct transaction UID - should succeed."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.101"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Claim workitem (transition to IN PROGRESS)
    txn_uid = "1.2.3.4.5.9999"
    claim_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    response = client.put(
        f"/v2/workitems/{workitem_uid}/state",
        json=claim_payload,
        headers={"Transaction-UID": txn_uid},
    )
    assert response.status_code == 200

    # Update workitem with matching transaction UID
    update = {
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Updated^Patient"}]},
    }
    response = client.put(
        f"/v2/workitems/{workitem_uid}", json=update, headers={"Transaction-UID": txn_uid}
    )
    assert response.status_code == 200

    # Verify update
    response = client.get(f"/v2/workitems/{workitem_uid}")
    dataset = response.json()
    assert dataset["00100010"]["Value"][0]["Alphabetic"] == "Updated^Patient"
    assert dataset["00741000"]["Value"][0] == "IN PROGRESS"


def test_update_in_progress_without_txn_uid(client: TestClient):
    """Update IN PROGRESS workitem without transaction UID - should fail with 409."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.102"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Claim workitem (transition to IN PROGRESS)
    txn_uid = "1.2.3.4.5.9999"
    claim_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    response = client.put(
        f"/v2/workitems/{workitem_uid}/state",
        json=claim_payload,
        headers={"Transaction-UID": txn_uid},
    )
    assert response.status_code == 200

    # Try to update without transaction UID
    update = {
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Updated^Patient"}]},
    }
    response = client.put(f"/v2/workitems/{workitem_uid}", json=update)
    assert response.status_code == 409
    assert "Transaction UID required" in response.json()["detail"]


def test_update_in_progress_with_wrong_txn_uid(client: TestClient):
    """Update IN PROGRESS workitem with wrong transaction UID - should fail with 409."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.103"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Claim workitem (transition to IN PROGRESS)
    txn_uid = "1.2.3.4.5.9999"
    claim_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    response = client.put(
        f"/v2/workitems/{workitem_uid}/state",
        json=claim_payload,
        headers={"Transaction-UID": txn_uid},
    )
    assert response.status_code == 200

    # Try to update with wrong transaction UID
    wrong_txn_uid = "1.2.3.4.5.8888"
    update = {
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Updated^Patient"}]},
    }
    response = client.put(
        f"/v2/workitems/{workitem_uid}", json=update, headers={"Transaction-UID": wrong_txn_uid}
    )
    assert response.status_code == 409
    assert "does not match" in response.json()["detail"]


def test_cannot_update_completed_workitem(client: TestClient):
    """Cannot update COMPLETED workitem - should fail with 400."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.104"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Claim workitem
    txn_uid = "1.2.3.4.5.9999"
    claim_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    response = client.put(
        f"/v2/workitems/{workitem_uid}/state",
        json=claim_payload,
        headers={"Transaction-UID": txn_uid},
    )
    assert response.status_code == 200

    # Complete workitem
    complete_payload = {
        "00741000": {"vr": "CS", "Value": ["COMPLETED"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    response = client.put(
        f"/v2/workitems/{workitem_uid}/state",
        json=complete_payload,
        headers={"Transaction-UID": txn_uid},
    )
    assert response.status_code == 200

    # Try to update COMPLETED workitem
    update = {
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Updated^Patient"}]},
    }
    response = client.put(f"/v2/workitems/{workitem_uid}", json=update)
    assert response.status_code == 400
    assert "Cannot update workitem in COMPLETED state" in response.json()["detail"]


def test_cannot_change_sop_instance_uid(client: TestClient):
    """Cannot change SOP Instance UID - should fail with 400."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.105"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Try to change SOP Instance UID
    update = {
        "00080018": {"vr": "UI", "Value": ["9.9.9.9.9"]},
    }
    response = client.put(f"/v2/workitems/{workitem_uid}", json=update)
    assert response.status_code == 400
    assert "Cannot change SOP Instance UID" in response.json()["detail"]


def test_cannot_change_state_via_update(client: TestClient):
    """Cannot change state via update endpoint - should fail with 400."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.106"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Try to change state via update
    update = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
    }
    response = client.put(f"/v2/workitems/{workitem_uid}", json=update)
    assert response.status_code == 400
    assert "Use state change endpoint" in response.json()["detail"]


def test_cannot_add_transaction_uid(client: TestClient):
    """Cannot add or change transaction UID - should fail with 400."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.107"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Try to add transaction UID
    update = {
        "00081195": {"vr": "UI", "Value": ["1.2.3.4.5.9999"]},
    }
    response = client.put(f"/v2/workitems/{workitem_uid}", json=update)
    assert response.status_code == 400
    assert "Cannot modify Transaction UID" in response.json()["detail"]


def test_update_nonexistent_workitem(client: TestClient):
    """Update non-existent workitem - should fail with 404."""
    workitem_uid = "1.2.3.4.5.999"
    update = {
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Test^Patient"}]},
    }
    response = client.put(f"/v2/workitems/{workitem_uid}", json=update)
    assert response.status_code == 404
    assert f"Workitem {workitem_uid} not found" in response.json()["detail"]


def test_searchable_attributes_reextracted_after_update(client: TestClient):
    """Verify searchable attributes are re-extracted after update."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.108"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Original^Name"}]},
        "00100020": {"vr": "LO", "Value": ["PAT-111"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Update searchable attributes
    update = {
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Updated^Name"}]},
        "00100020": {"vr": "LO", "Value": ["PAT-222"]},
    }
    response = client.put(f"/v2/workitems/{workitem_uid}", json=update)
    assert response.status_code == 200

    # Verify searchable attributes were updated
    response = client.get(f"/v2/workitems/{workitem_uid}")
    dataset = response.json()
    assert dataset["00100010"]["Value"][0]["Alphabetic"] == "Updated^Name"
    assert dataset["00100020"]["Value"][0] == "PAT-222"

    # TODO: When search endpoint is implemented, verify searchable attributes work
    # For now, we verify the dataset was updated
