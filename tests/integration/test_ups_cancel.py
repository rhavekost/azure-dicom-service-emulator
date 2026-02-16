"""Tests for UPS-RS cancel request endpoint (Phase 5, Task 7)."""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_cancel_scheduled_workitem(client: TestClient):
    """Cancel SCHEDULED workitem via cancelrequest."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.300"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Cancel workitem via cancelrequest
    response = client.post(f"/v2/workitems/{workitem_uid}/cancelrequest")

    assert response.status_code == 202

    # Verify state changed to CANCELED
    response = client.get(f"/v2/workitems/{workitem_uid}")
    dataset = response.json()
    assert dataset["00741000"]["Value"][0] == "CANCELED"


def test_cannot_cancel_in_progress_workitem(client: TestClient):
    """Cannot use cancelrequest for IN PROGRESS workitem."""
    # Create and claim workitem
    workitem_uid = "1.2.3.4.5.301"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Claim workitem (SCHEDULED → IN PROGRESS)
    state_change = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": ["1.2.3.txn"]},
    }
    response = client.put(f"/v2/workitems/{workitem_uid}/state", json=state_change)
    assert response.status_code == 200

    # Try to cancel via cancelrequest (should fail)
    response = client.post(f"/v2/workitems/{workitem_uid}/cancelrequest")

    assert response.status_code == 400
    assert "Cannot cancel workitem in IN PROGRESS state" in response.json()["detail"]
    assert "Use state change endpoint" in response.json()["detail"]


def test_cannot_cancel_completed_workitem(client: TestClient):
    """Cannot use cancelrequest for COMPLETED workitem."""
    # Create, claim, and complete workitem
    workitem_uid = "1.2.3.4.5.302"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Claim workitem
    txn_uid = "1.2.3.txn.302"
    claim_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    response = client.put(f"/v2/workitems/{workitem_uid}/state", json=claim_payload)
    assert response.status_code == 200

    # Complete workitem
    complete_payload = {
        "00741000": {"vr": "CS", "Value": ["COMPLETED"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    response = client.put(f"/v2/workitems/{workitem_uid}/state", json=complete_payload)
    assert response.status_code == 200

    # Try to cancel via cancelrequest (should fail)
    response = client.post(f"/v2/workitems/{workitem_uid}/cancelrequest")

    assert response.status_code == 400
    assert "Cannot cancel workitem in COMPLETED state" in response.json()["detail"]


def test_cancel_nonexistent_workitem(client: TestClient):
    """Cancel non-existent workitem - should fail with 404."""
    workitem_uid = "1.2.3.4.5.999"

    response = client.post(f"/v2/workitems/{workitem_uid}/cancelrequest")

    assert response.status_code == 404
    assert f"Workitem {workitem_uid} not found" in response.json()["detail"]


def test_canceled_workitem_state_persisted(client: TestClient):
    """Verify canceled workitem state is persisted in database and dataset."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.303"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Test^Patient"}]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Cancel workitem
    response = client.post(f"/v2/workitems/{workitem_uid}/cancelrequest")
    assert response.status_code == 202

    # Retrieve and verify state is CANCELED
    response = client.get(f"/v2/workitems/{workitem_uid}")
    assert response.status_code == 200
    dataset = response.json()

    # Verify state in dataset
    assert "00741000" in dataset
    assert dataset["00741000"]["vr"] == "CS"
    assert dataset["00741000"]["Value"][0] == "CANCELED"

    # Verify other attributes are preserved
    assert dataset["00080018"]["Value"][0] == workitem_uid
    assert dataset["00100010"]["Value"][0]["Alphabetic"] == "Test^Patient"


def test_canceled_workitem_retrievable(client: TestClient):
    """Verify canceled workitem can be retrieved and shows CANCELED state."""
    # Create workitem
    workitem_uid = "1.2.3.4.5.304"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)
    assert response.status_code == 201

    # Cancel workitem
    response = client.post(f"/v2/workitems/{workitem_uid}/cancelrequest")
    assert response.status_code == 202

    # Retrieve canceled workitem
    response = client.get(f"/v2/workitems/{workitem_uid}")

    assert response.status_code == 200
    dataset = response.json()
    assert dataset["00741000"]["Value"][0] == "CANCELED"

    # Verify workitem is still accessible (not deleted)
    assert dataset["00080018"]["Value"][0] == workitem_uid
