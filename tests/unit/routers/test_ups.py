"""Unit tests for UPS-RS (Unified Procedure Step) endpoints."""

import pytest
from pydicom.uid import generate_uid

pytestmark = pytest.mark.unit


# Minimal valid workitem payload (DICOM JSON format)
def make_workitem(uid: str | None = None, state: str = "SCHEDULED") -> tuple:
    uid = uid or generate_uid()
    payload = {
        "00080018": {"vr": "UI", "Value": [uid]},  # SOP Instance UID
        "00741000": {"vr": "CS", "Value": [state]},  # Procedure Step State
        "00100020": {"vr": "LO", "Value": ["PAT-UPS-001"]},  # Patient ID
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "UPS^Test"}]},  # Patient Name
    }
    return uid, payload


def test_create_workitem_returns_201(client):
    uid, payload = make_workitem()
    response = client.post("/v2/workitems", json=payload)
    assert response.status_code == 201


def test_create_workitem_sets_location_header(client):
    uid, payload = make_workitem()
    response = client.post("/v2/workitems", json=payload)
    assert response.status_code == 201
    assert "Location" in response.headers


def test_create_workitem_duplicate_returns_409(client):
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201
    response = client.post("/v2/workitems", json=payload)
    assert response.status_code == 409


def test_create_workitem_invalid_state_returns_400(client):
    uid, payload = make_workitem(state="IN PROGRESS")
    response = client.post("/v2/workitems", json=payload)
    assert response.status_code == 400


def test_create_workitem_with_transaction_uid_returns_400(client):
    uid, payload = make_workitem()
    payload["00081195"] = {"vr": "UI", "Value": [generate_uid()]}  # Transaction UID
    response = client.post("/v2/workitems", json=payload)
    assert response.status_code == 400


def test_search_workitems_empty(client):
    response = client.get("/v2/workitems")
    assert response.status_code == 200
    assert response.json() == []


def test_search_workitems_returns_result(client):
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201
    response = client.get("/v2/workitems")
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_search_by_patient_id(client):
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201
    response = client.get("/v2/workitems?PatientID=PAT-UPS-001")
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_search_by_patient_id_no_match(client):
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201
    response = client.get("/v2/workitems?PatientID=NOBODY")
    assert response.status_code == 200
    assert response.json() == []


def test_search_by_state(client):
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201
    response = client.get("/v2/workitems?ProcedureStepState=SCHEDULED")
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_retrieve_workitem(client):
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201
    response = client.get(f"/v2/workitems/{uid}")
    assert response.status_code == 200


def test_retrieve_workitem_not_found(client):
    response = client.get(f"/v2/workitems/{generate_uid()}")
    assert response.status_code == 404


def test_cancel_workitem(client):
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201
    cancel_payload = {"00741000": {"vr": "CS", "Value": ["CANCELED"]}}
    response = client.post(f"/v2/workitems/{uid}/cancelrequest", json=cancel_payload)
    assert response.status_code in (200, 202, 204)


def test_subscription_endpoints_return_501(client):
    uid = generate_uid()
    response = client.post(f"/v2/workitems/{uid}/subscribers/test")
    assert response.status_code == 501


def test_unsubscribe_endpoint_returns_501(client):
    uid = generate_uid()
    response = client.delete(f"/v2/workitems/{uid}/subscribers/test")
    assert response.status_code == 501


def test_list_subscriptions_returns_501(client):
    response = client.get("/v2/workitems/subscriptions")
    assert response.status_code == 501


# ── update_workitem (PUT /v2/workitems/{uid}) ──────────────────────────────


def test_update_workitem_not_found_returns_404(client):
    """Updating a nonexistent workitem returns 404."""
    response = client.put(f"/v2/workitems/{generate_uid()}", json={})
    assert response.status_code == 404


def test_update_workitem_scheduled_returns_200(client):
    """Updating a SCHEDULED workitem returns 200."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    update_payload = {
        "00100020": {"vr": "LO", "Value": ["NEW-PAT-ID"]},
    }
    response = client.put(f"/v2/workitems/{uid}", json=update_payload)
    assert response.status_code == 200


def test_update_workitem_cannot_change_sop_uid(client):
    """Updating with a different SOP UID returns 400."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    update_payload = {
        "00080018": {"vr": "UI", "Value": [generate_uid()]},  # different UID
    }
    response = client.put(f"/v2/workitems/{uid}", json=update_payload)
    assert response.status_code == 400


def test_update_workitem_cannot_change_state(client):
    """Updating with ProcedureStepState via update endpoint returns 400."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    update_payload = {
        "00741000": {"vr": "CS", "Value": ["COMPLETED"]},
    }
    response = client.put(f"/v2/workitems/{uid}", json=update_payload)
    assert response.status_code == 400


def test_update_workitem_cannot_modify_transaction_uid(client):
    """Updating with Transaction UID in payload returns 400."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    update_payload = {
        "00081195": {"vr": "UI", "Value": [generate_uid()]},
    }
    response = client.put(f"/v2/workitems/{uid}", json=update_payload)
    assert response.status_code == 400


def test_update_workitem_completed_returns_400(client):
    """Updating a COMPLETED workitem returns 400."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    # Claim the workitem (SCHEDULED → IN PROGRESS)
    txn_uid = generate_uid()
    claim_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    assert client.put(f"/v2/workitems/{uid}/state", json=claim_payload).status_code == 200

    # Complete it (IN PROGRESS → COMPLETED)
    complete_payload = {
        "00741000": {"vr": "CS", "Value": ["COMPLETED"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    assert client.put(f"/v2/workitems/{uid}/state", json=complete_payload).status_code == 200

    # Now try to update — should return 400
    response = client.put(f"/v2/workitems/{uid}", json={"00100020": {"vr": "LO", "Value": ["X"]}})
    assert response.status_code == 400


def test_update_workitem_in_progress_wrong_txn_uid_returns_409(client):
    """Updating an IN PROGRESS workitem with wrong transaction UID returns 409."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    # Claim it
    txn_uid = generate_uid()
    claim_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    assert client.put(f"/v2/workitems/{uid}/state", json=claim_payload).status_code == 200

    # Update with wrong transaction UID
    wrong_txn = generate_uid()
    response = client.put(
        f"/v2/workitems/{uid}",
        json={"00100020": {"vr": "LO", "Value": ["X"]}},
        headers={"Transaction-UID": wrong_txn},
    )
    assert response.status_code == 409


def test_update_workitem_in_progress_with_correct_txn_uid_returns_200(client):
    """Updating an IN PROGRESS workitem with correct transaction UID returns 200."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    # Claim it
    txn_uid = generate_uid()
    claim_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    assert client.put(f"/v2/workitems/{uid}/state", json=claim_payload).status_code == 200

    # Update with correct transaction UID
    response = client.put(
        f"/v2/workitems/{uid}",
        json={"00100020": {"vr": "LO", "Value": ["NEW-ID"]}},
        headers={"Transaction-UID": txn_uid},
    )
    assert response.status_code == 200


def test_update_workitem_updates_patient_name_as_string(client):
    """Updating patient name as plain string (not dict) succeeds."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    update_payload = {
        "00100010": {"vr": "PN", "Value": ["NewPatient"]},  # string, not dict
    }
    response = client.put(f"/v2/workitems/{uid}", json=update_payload)
    assert response.status_code == 200


# ── change_workitem_state (PUT /v2/workitems/{uid}/state) ──────────────────


def test_change_state_not_found_returns_404(client):
    """Changing state for a nonexistent workitem returns 404."""
    state_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [generate_uid()]},
    }
    response = client.put(f"/v2/workitems/{generate_uid()}/state", json=state_payload)
    assert response.status_code == 404


def test_change_state_missing_state_tag_returns_400(client):
    """Changing state without ProcedureStepState returns 400."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    response = client.put(f"/v2/workitems/{uid}/state", json={})
    assert response.status_code == 400


def test_change_state_scheduled_to_in_progress(client):
    """Claiming a workitem (SCHEDULED → IN PROGRESS) returns 200."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    txn_uid = generate_uid()
    state_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    response = client.put(f"/v2/workitems/{uid}/state", json=state_payload)
    assert response.status_code == 200


def test_change_state_in_progress_to_completed(client):
    """Completing a workitem (IN PROGRESS → COMPLETED) returns 200."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    txn_uid = generate_uid()
    claim_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    assert client.put(f"/v2/workitems/{uid}/state", json=claim_payload).status_code == 200

    complete_payload = {
        "00741000": {"vr": "CS", "Value": ["COMPLETED"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    response = client.put(f"/v2/workitems/{uid}/state", json=complete_payload)
    assert response.status_code == 200


def test_change_state_in_progress_to_canceled(client):
    """Canceling an IN PROGRESS workitem returns 200."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    txn_uid = generate_uid()
    claim_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    assert client.put(f"/v2/workitems/{uid}/state", json=claim_payload).status_code == 200

    cancel_payload = {
        "00741000": {"vr": "CS", "Value": ["CANCELED"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    response = client.put(f"/v2/workitems/{uid}/state", json=cancel_payload)
    assert response.status_code == 200


def test_change_state_invalid_transition_returns_400(client):
    """Invalid state transition returns 400."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    # SCHEDULED → COMPLETED is invalid (must go via IN PROGRESS)
    state_payload = {
        "00741000": {"vr": "CS", "Value": ["COMPLETED"]},
        "00081195": {"vr": "UI", "Value": [generate_uid()]},
    }
    response = client.put(f"/v2/workitems/{uid}/state", json=state_payload)
    assert response.status_code == 400


def test_cancel_workitem_not_scheduled_returns_400(client):
    """Canceling an IN PROGRESS workitem via cancelrequest returns 400."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    # Claim it first
    txn_uid = generate_uid()
    claim_payload = {
        "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
        "00081195": {"vr": "UI", "Value": [txn_uid]},
    }
    assert client.put(f"/v2/workitems/{uid}/state", json=claim_payload).status_code == 200

    # Now try cancelrequest — should return 400 (not SCHEDULED)
    response = client.post(f"/v2/workitems/{uid}/cancelrequest")
    assert response.status_code == 400


def test_cancel_workitem_not_found_returns_404(client):
    """Canceling a nonexistent workitem via cancelrequest returns 404."""
    response = client.post(f"/v2/workitems/{generate_uid()}/cancelrequest")
    assert response.status_code == 404


def test_create_workitem_uid_from_path(client):
    """Creating a workitem with UID in path parameter stores it correctly."""
    uid = generate_uid()
    payload = {
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
        "00100020": {"vr": "LO", "Value": ["PAT-PATH-001"]},
    }
    response = client.post(f"/v2/workitems/{uid}", json=payload)
    assert response.status_code == 201
    assert f"/v2/workitems/{uid}" in response.headers.get("Location", "")


def test_search_by_patient_name(client):
    """Search workitems by PatientName filter."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    response = client.get("/v2/workitems?PatientName=UPS")
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_search_by_patient_name_no_match(client):
    """Search workitems by PatientName with no match returns empty list."""
    uid, payload = make_workitem()
    assert client.post("/v2/workitems", json=payload).status_code == 201

    response = client.get("/v2/workitems?PatientName=NOBODY")
    assert response.status_code == 200
    assert response.json() == []
