"""Tests for UPS-RS search workitems endpoint (Phase 5, Task 8)."""

import pytest
from fastapi.testclient import TestClient


def test_search_all_workitems(client: TestClient):
    """Search with no filters returns all workitems."""
    # Create multiple workitems
    workitem_1 = "1.2.3.4.5"
    payload_1 = {
        "00080018": {"vr": "UI", "Value": [workitem_1]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
        "00100020": {"vr": "LO", "Value": ["PAT123"]},
    }
    client.post(f"/v2/workitems?{workitem_1}", json=payload_1)

    workitem_2 = "1.2.3.4.6"
    payload_2 = {
        "00080018": {"vr": "UI", "Value": [workitem_2]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
        "00100020": {"vr": "LO", "Value": ["PAT456"]},
    }
    client.post(f"/v2/workitems?{workitem_2}", json=payload_2)

    # Search without filters
    response = client.get("/v2/workitems")

    assert response.status_code == 200
    results = response.json()
    assert len(results) >= 2  # At least our 2 workitems

    # Verify workitems are in results
    uids = [w["00080018"]["Value"][0] for w in results]
    assert workitem_1 in uids
    assert workitem_2 in uids


def test_search_workitems_by_patient_id(client: TestClient):
    """Filter workitems by patient ID."""
    # Create workitems with different patient IDs
    workitem_1 = "1.2.3.4.10"
    payload_1 = {
        "00080018": {"vr": "UI", "Value": [workitem_1]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
        "00100020": {"vr": "LO", "Value": ["PAT123"]},
    }
    client.post(f"/v2/workitems?{workitem_1}", json=payload_1)

    workitem_2 = "1.2.3.4.11"
    payload_2 = {
        "00080018": {"vr": "UI", "Value": [workitem_2]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
        "00100020": {"vr": "LO", "Value": ["PAT456"]},
    }
    client.post(f"/v2/workitems?{workitem_2}", json=payload_2)

    # Search by patient ID
    response = client.get("/v2/workitems", params={"PatientID": "PAT123"})

    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["00100020"]["Value"][0] == "PAT123"


def test_search_workitems_by_patient_name(client: TestClient):
    """Filter workitems by patient name (case-insensitive)."""
    # Create workitems with different patient names
    workitem_1 = "1.2.3.4.20"
    payload_1 = {
        "00080018": {"vr": "UI", "Value": [workitem_1]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Doe^John"}]},
    }
    client.post(f"/v2/workitems?{workitem_1}", json=payload_1)

    workitem_2 = "1.2.3.4.21"
    payload_2 = {
        "00080018": {"vr": "UI", "Value": [workitem_2]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Smith^Jane"}]},
    }
    client.post(f"/v2/workitems?{workitem_2}", json=payload_2)

    # Search by patient name (case-insensitive partial match)
    response = client.get("/v2/workitems", params={"PatientName": "doe"})

    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert "Doe^John" in results[0]["00100010"]["Value"][0]["Alphabetic"]


def test_search_workitems_by_state(client: TestClient):
    """Filter workitems by state."""
    # Create workitems in different states
    workitem_1 = "1.2.3.4.30"
    payload_1 = {
        "00080018": {"vr": "UI", "Value": [workitem_1]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    client.post(f"/v2/workitems?{workitem_1}", json=payload_1)

    workitem_2 = "1.2.3.4.31"
    payload_2 = {
        "00080018": {"vr": "UI", "Value": [workitem_2]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    client.post(f"/v2/workitems?{workitem_2}", json=payload_2)

    # Claim workitem 2 (move to IN PROGRESS)
    state_change = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": ["1.2.3.txn"]}
    }
    client.put(f"/v2/workitems/{workitem_2}/state", json=state_change)

    # Search by state
    response = client.get("/v2/workitems", params={"ProcedureStepState": "SCHEDULED"})

    assert response.status_code == 200
    results = response.json()

    # Filter to only our test workitems
    our_results = [r for r in results if r["00080018"]["Value"][0] in [workitem_1, workitem_2]]
    assert len(our_results) == 1
    assert our_results[0]["00741000"]["Value"][0] == "SCHEDULED"


def test_search_workitems_with_multiple_filters(client: TestClient):
    """Combine multiple search filters."""
    # Create workitems
    workitem_1 = "1.2.3.4.40"
    payload_1 = {
        "00080018": {"vr": "UI", "Value": [workitem_1]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
        "00100020": {"vr": "LO", "Value": ["PAT789"]},
    }
    client.post(f"/v2/workitems?{workitem_1}", json=payload_1)

    workitem_2 = "1.2.3.4.41"
    payload_2 = {
        "00080018": {"vr": "UI", "Value": [workitem_2]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
        "00100020": {"vr": "LO", "Value": ["PAT999"]},
    }
    client.post(f"/v2/workitems?{workitem_2}", json=payload_2)

    # Claim workitem 1 (different state)
    state_change = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": ["1.2.3.txn"]}
    }
    client.put(f"/v2/workitems/{workitem_1}/state", json=state_change)

    # Search by patient ID AND state
    response = client.get(
        "/v2/workitems",
        params={"PatientID": "PAT999", "ProcedureStepState": "SCHEDULED"}
    )

    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["00100020"]["Value"][0] == "PAT999"
    assert results[0]["00741000"]["Value"][0] == "SCHEDULED"


def test_search_workitems_pagination(client: TestClient):
    """Test pagination with limit and offset."""
    # Create multiple workitems
    workitems = []
    for i in range(5):
        uid = f"1.2.3.4.50.{i}"
        payload = {
            "00080018": {"vr": "UI", "Value": [uid]},
            "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
        }
        client.post(f"/v2/workitems?{uid}", json=payload)
        workitems.append(uid)

    # Test limit
    response = client.get("/v2/workitems", params={"limit": 2})
    assert response.status_code == 200
    results = response.json()
    assert len(results) <= 2  # May have other workitems from previous tests

    # Test offset
    response_page1 = client.get("/v2/workitems", params={"limit": 2, "offset": 0})
    response_page2 = client.get("/v2/workitems", params={"limit": 2, "offset": 2})

    assert response_page1.status_code == 200
    assert response_page2.status_code == 200

    page1 = response_page1.json()
    page2 = response_page2.json()

    # Verify pagination returns different results
    if len(page1) > 0 and len(page2) > 0:
        page1_uids = [w["00080018"]["Value"][0] for w in page1]
        page2_uids = [w["00080018"]["Value"][0] for w in page2]
        # Pages should not have overlapping workitems
        assert len(set(page1_uids) & set(page2_uids)) == 0


def test_search_workitems_no_results(client: TestClient):
    """Return empty array when no matches."""
    response = client.get("/v2/workitems", params={"PatientID": "NONEXISTENT"})

    assert response.status_code == 200
    results = response.json()
    assert results == []


def test_search_workitems_transaction_uid_not_exposed(client: TestClient):
    """Transaction UIDs are never exposed in search results."""
    # Create and claim a workitem
    workitem_uid = "1.2.3.4.60"
    payload = {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    }
    client.post(f"/v2/workitems?{workitem_uid}", json=payload)

    # Claim workitem
    state_change = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": ["1.2.3.secret.txn"]}
    }
    client.put(f"/v2/workitems/{workitem_uid}/state", json=state_change)

    # Search for the workitem
    response = client.get("/v2/workitems", params={"ProcedureStepState": "IN PROGRESS"})

    assert response.status_code == 200
    results = response.json()

    # Find our workitem
    our_workitem = None
    for r in results:
        if r["00080018"]["Value"][0] == workitem_uid:
            our_workitem = r
            break

    assert our_workitem is not None
    # Transaction UID must NOT be present
    assert "00081195" not in our_workitem
