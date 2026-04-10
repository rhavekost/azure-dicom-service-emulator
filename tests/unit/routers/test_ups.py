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
