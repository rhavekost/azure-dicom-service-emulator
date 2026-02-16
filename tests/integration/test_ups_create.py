
"""Tests for UPS-RS create workitem endpoint (Phase 5, Task 3)."""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

def test_create_workitem_post(client: TestClient):
    """Create workitem via POST."""
    workitem_uid = "1.2.3.4.5"

    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Doe^John"}]},
        "00100020": {"vr": "LO", "Value": ["PAT123"]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }

    response = client.post(
        f"/v2/workitems?{workitem_uid}",
        json=payload,
        headers={"Content-Type": "application/dicom+json"}
    )

    assert response.status_code == 201
    assert "Location" in response.headers
    assert response.headers["Location"].endswith(workitem_uid)


def test_create_workitem_duplicate(client: TestClient):
    """Cannot create duplicate workitem."""
    workitem_uid = "1.2.3.4.5"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }

    # Create first time
    client.post(f"/v2/workitems?{workitem_uid}", json=payload)

    # Try to create again
    response = client.post(f"/v2/workitems?{workitem_uid}", json=payload)

    assert response.status_code == 409


def test_create_workitem_must_be_scheduled(client: TestClient):
    """New workitem must have SCHEDULED state."""
    payload = {
        "00080018": {"vr": "UI", "Value": ["1.2.3.4.5"]},
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
    }

    response = client.post("/v2/workitems", json=payload)

    assert response.status_code == 400
    assert "SCHEDULED" in response.json()["detail"]


def test_create_workitem_no_transaction_uid(client: TestClient):
    """New workitem must not have Transaction UID."""
    payload = {
        "00080018": {"vr": "UI", "Value": ["1.2.3.4.6"]},
        "00081195": {"vr": "UI", "Value": ["1.2.3.txn"]},  # Transaction UID should not be present
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }

    response = client.post("/v2/workitems", json=payload)

    assert response.status_code == 400
    assert "Transaction UID" in response.json()["detail"]


def test_create_workitem_uid_from_dataset(client: TestClient):
    """Extract workitem UID from dataset if not in query/path."""
    workitem_uid = "1.2.3.4.7"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }

    # No UID in query or path - should extract from dataset
    response = client.post("/v2/workitems", json=payload)

    assert response.status_code == 201
    assert response.headers["Location"].endswith(workitem_uid)


def test_create_workitem_path_parameter(client: TestClient):
    """Create workitem using path parameter."""
    workitem_uid = "1.2.3.4.8"
    payload = {
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Smith^Jane"}]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }

    response = client.post(f"/v2/workitems/{workitem_uid}", json=payload)

    assert response.status_code == 201
    assert response.headers["Location"].endswith(workitem_uid)
