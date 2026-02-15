"""Tests for UPS-RS change state endpoint (Phase 5, Task 6)."""

import pytest
from fastapi.testclient import TestClient


def test_claim_workitem_scheduled_to_in_progress(client: TestClient):
    """Claim SCHEDULED workitem (SCHEDULED → IN PROGRESS)."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.200"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Claim workitem
    state_change = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": ["1.2.3.txn"]}
    }
    response = client.put(f"/v2/workitems/{workitem_uid}/state", json=state_change)
    assert response.status_code == 200

    # Verify state changed
    response = client.get(f"/v2/workitems/{workitem_uid}")
    dataset = response.json()
    assert dataset["00741000"]["Value"][0] == "IN PROGRESS"
    # Note: Transaction UID should NOT be in response (security)
    assert "00081195" not in dataset


def test_complete_workitem_in_progress_to_completed(client: TestClient):
    """Complete workitem (IN PROGRESS → COMPLETED)."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.201"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Claim workitem
    txn_uid = "1.2.3.txn.201"
    claim_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]}
    }
    response = client.put(f"/v2/workitems/{workitem_uid}/state", json=claim_payload)
    assert response.status_code == 200

    # Complete workitem
    complete_payload = {
        "00741000": {"vr": "CS", "Value": ["COMPLETED"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]}
    }
    response = client.put(f"/v2/workitems/{workitem_uid}/state", json=complete_payload)
    assert response.status_code == 200

    # Verify state changed
    response = client.get(f"/v2/workitems/{workitem_uid}")
    dataset = response.json()
    assert dataset["00741000"]["Value"][0] == "COMPLETED"


def test_cannot_complete_with_wrong_txn_uid(client: TestClient):
    """Cannot complete with wrong transaction UID - should fail with 400."""
    # Create and claim workitem
    workitem_uid = "1.2.3.4.5.202"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Claim workitem
    txn_uid = "1.2.3.txn.202"
    claim_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]}
    }
    response = client.put(f"/v2/workitems/{workitem_uid}/state", json=claim_payload)
    assert response.status_code == 200

    # Try to complete with wrong UID
    wrong_uid = "wrong-uid"
    complete_payload = {
        "00741000": {"vr": "CS", "Value": ["COMPLETED"]},
        "00081195": {"vr": "UI", "Value": [wrong_uid]}
    }
    response = client.put(f"/v2/workitems/{workitem_uid}/state", json=complete_payload)
    assert response.status_code == 400
    assert "does not match" in response.json()["detail"]


def test_cannot_claim_without_txn_uid(client: TestClient):
    """Cannot claim workitem without transaction UID - should fail with 400."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.203"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Try to claim without transaction UID
    claim_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
    }
    response = client.put(f"/v2/workitems/{workitem_uid}/state", json=claim_payload)
    assert response.status_code == 400
    assert "Transaction UID required" in response.json()["detail"]


def test_cannot_transition_from_completed(client: TestClient):
    """COMPLETED is terminal - cannot transition from it."""
    # Create, claim, and complete workitem
    workitem_uid = "1.2.3.4.5.204"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Claim workitem
    txn_uid = "1.2.3.txn.204"
    claim_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]}
    }
    response = client.put(f"/v2/workitems/{workitem_uid}/state", json=claim_payload)
    assert response.status_code == 200

    # Complete workitem
    complete_payload = {
        "00741000": {"vr": "CS", "Value": ["COMPLETED"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]}
    }
    response = client.put(f"/v2/workitems/{workitem_uid}/state", json=complete_payload)
    assert response.status_code == 200

    # Try to transition from COMPLETED (should fail)
    invalid_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]}
    }
    response = client.put(f"/v2/workitems/{workitem_uid}/state", json=invalid_payload)
    assert response.status_code == 400
    assert "Cannot transition from COMPLETED" in response.json()["detail"]


def test_state_change_nonexistent_workitem(client: TestClient):
    """State change on non-existent workitem - should fail with 404."""
    workitem_uid = "1.2.3.4.5.999"
    state_change = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": ["1.2.3.txn"]}
    }
    response = client.put(f"/v2/workitems/{workitem_uid}/state", json=state_change)
    assert response.status_code == 404
    assert f"Workitem {workitem_uid} not found" in response.json()["detail"]


def test_missing_state_in_payload(client: TestClient):
    """State change without ProcedureStepState in payload - should fail with 400."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.205"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Try to change state without ProcedureStepState
    invalid_payload = {
        "00081195": {"vr": "UI", "Value": ["1.2.3.txn"]}
    }
    response = client.put(f"/v2/workitems/{workitem_uid}/state", json=invalid_payload)
    assert response.status_code == 400
    assert "ProcedureStepState" in response.json()["detail"]
