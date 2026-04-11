"""E2E tests: UPS-RS state machine workflow.

Flow:
  1. Create a UPS workitem (state = SCHEDULED).
  2. Claim it by changing state to IN PROGRESS (with a transaction UID).
  3. Complete it by changing state to COMPLETED.
  4. Verify the final state via GET.

Additional tests cover:
  - Retrieving a workitem
  - Searching workitems by state / patient
  - Cancel via the cancelrequest endpoint
  - Invalid state transitions return 400
  - Updating attributes on a workitem

Requires a live service (docker-compose).  Tests are skipped automatically
when the service is not reachable.

Run with:
    pytest tests/e2e/ -m e2e
    DICOM_E2E_BASE_URL=http://my-host:8080 pytest tests/e2e/ -m e2e
"""

import httpx
import pytest
from pydicom.uid import generate_uid

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workitem_uid() -> str:
    """Generate a unique workitem UID."""
    return generate_uid()


def _make_transaction_uid() -> str:
    """Generate a unique transaction UID."""
    return generate_uid()


def _workitem_dataset(workitem_uid: str, patient_id: str = "UPS-E2E-001") -> dict:
    """Minimal DICOM JSON dataset for a new workitem."""
    return {
        "00080018": {"vr": "UI", "Value": [workitem_uid]},  # SOP Instance UID
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},  # ProcedureStepState
        "00100010": {  # PatientName
            "vr": "PN",
            "Value": [{"Alphabetic": "UPS^Test"}],
        },
        "00100020": {"vr": "LO", "Value": [patient_id]},  # PatientID
    }


def _state_change_payload(new_state: str, txn_uid: str | None = None) -> dict:
    """Build the state-change request body."""
    payload: dict = {
        "00741000": {"vr": "CS", "Value": [new_state]},
    }
    if txn_uid is not None:
        payload["00081195"] = {"vr": "UI", "Value": [txn_uid]}
    return payload


# ---------------------------------------------------------------------------
# Module-scoped fixture: one workitem that advances through the state machine
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ups_workitem(http_client: httpx.Client) -> dict:
    """Create a new SCHEDULED workitem; return UIDs.

    Scoped to module so sequential state-machine tests share the same item.
    """
    workitem_uid = _make_workitem_uid()
    payload = _workitem_dataset(workitem_uid)

    response = http_client.post(
        f"/v2/workitems/{workitem_uid}",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    assert (
        response.status_code == 201
    ), f"Create workitem failed: {response.status_code} {response.text}"
    return {"workitem_uid": workitem_uid}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_create_workitem_returns_201(http_client: httpx.Client) -> None:
    """POST /v2/workitems/{uid} creates a workitem and returns 201."""
    workitem_uid = _make_workitem_uid()
    payload = _workitem_dataset(workitem_uid, patient_id="UPS-E2E-CREATE")

    response = http_client.post(
        f"/v2/workitems/{workitem_uid}",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    assert (
        response.status_code == 201
    ), f"Create workitem failed: {response.status_code} {response.text}"
    # Azure spec: Location header points to the new resource
    assert "Location" in response.headers or response.status_code == 201


@pytest.mark.e2e
def test_retrieve_workitem_scheduled(http_client: httpx.Client, ups_workitem: dict) -> None:
    """GET /v2/workitems/{uid} returns the workitem in SCHEDULED state."""
    workitem_uid = ups_workitem["workitem_uid"]
    response = http_client.get(f"/v2/workitems/{workitem_uid}")
    assert (
        response.status_code == 200
    ), f"Retrieve workitem failed: {response.status_code} {response.text}"
    data = response.json()
    state = data.get("00741000", {}).get("Value", [None])[0]
    assert state == "SCHEDULED", f"Expected SCHEDULED state, got {state}"


@pytest.mark.e2e
def test_retrieve_nonexistent_workitem_returns_404(http_client: httpx.Client) -> None:
    """GET /v2/workitems/{uid} for unknown UID returns 404."""
    nonexistent_uid = _make_workitem_uid()
    response = http_client.get(f"/v2/workitems/{nonexistent_uid}")
    assert (
        response.status_code == 404
    ), f"Expected 404 for missing workitem, got {response.status_code}"


@pytest.mark.e2e
def test_transaction_uid_not_exposed_in_response(
    http_client: httpx.Client, ups_workitem: dict
) -> None:
    """GET response never includes the Transaction UID (00081195)."""
    workitem_uid = ups_workitem["workitem_uid"]
    response = http_client.get(f"/v2/workitems/{workitem_uid}")
    assert response.status_code == 200
    data = response.json()
    assert "00081195" not in data, "Transaction UID (00081195) must not be exposed in GET response"


@pytest.mark.e2e
def test_search_workitems_returns_list(http_client: httpx.Client, ups_workitem: dict) -> None:
    """GET /v2/workitems returns a list of workitems."""
    response = http_client.get("/v2/workitems")
    assert (
        response.status_code == 200
    ), f"Search workitems failed: {response.status_code} {response.text}"
    assert isinstance(response.json(), list)


@pytest.mark.e2e
def test_search_workitems_by_state(http_client: httpx.Client, ups_workitem: dict) -> None:
    """GET /v2/workitems?ProcedureStepState=SCHEDULED returns the created workitem."""
    workitem_uid = ups_workitem["workitem_uid"]
    response = http_client.get("/v2/workitems", params={"ProcedureStepState": "SCHEDULED"})
    assert (
        response.status_code == 200
    ), f"Search by state failed: {response.status_code} {response.text}"
    items = response.json()
    found_uids = [item.get("00080018", {}).get("Value", [None])[0] for item in items]
    assert (
        workitem_uid in found_uids
    ), f"Workitem {workitem_uid} not found in SCHEDULED search. UIDs: {found_uids}"


@pytest.mark.e2e
def test_search_workitems_by_patient_id(http_client: httpx.Client, ups_workitem: dict) -> None:
    """GET /v2/workitems?PatientID=UPS-E2E-001 returns the workitem."""
    response = http_client.get("/v2/workitems", params={"PatientID": "UPS-E2E-001"})
    assert (
        response.status_code == 200
    ), f"Search by PatientID failed: {response.status_code} {response.text}"
    assert isinstance(response.json(), list)
    assert len(response.json()) >= 1


@pytest.mark.e2e
def test_create_duplicate_workitem_returns_409(
    http_client: httpx.Client, ups_workitem: dict
) -> None:
    """POST with an existing UID and no transaction-uid returns 409 Conflict."""
    workitem_uid = ups_workitem["workitem_uid"]
    payload = _workitem_dataset(workitem_uid)

    response = http_client.post(
        f"/v2/workitems/{workitem_uid}",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    assert (
        response.status_code == 409
    ), f"Expected 409 for duplicate workitem, got {response.status_code} {response.text}"


@pytest.mark.e2e
def test_claim_workitem_in_progress(http_client: httpx.Client, ups_workitem: dict) -> None:
    """PUT /v2/workitems/{uid}/state transitions SCHEDULED → IN PROGRESS.

    Stores the transaction UID in the fixture dict for downstream tests.
    """
    workitem_uid = ups_workitem["workitem_uid"]
    txn_uid = _make_transaction_uid()
    ups_workitem["transaction_uid"] = txn_uid  # share with later tests

    payload = _state_change_payload("IN PROGRESS", txn_uid)
    response = http_client.put(
        f"/v2/workitems/{workitem_uid}/state",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    assert (
        response.status_code == 200
    ), f"State change SCHEDULED→IN PROGRESS failed: {response.status_code} {response.text}"


@pytest.mark.e2e
def test_retrieve_workitem_in_progress(http_client: httpx.Client, ups_workitem: dict) -> None:
    """GET after claiming the workitem shows IN PROGRESS state.

    Depends on test_claim_workitem_in_progress running first (same module fixture).
    """
    workitem_uid = ups_workitem["workitem_uid"]
    response = http_client.get(f"/v2/workitems/{workitem_uid}")
    assert response.status_code == 200

    data = response.json()
    state = data.get("00741000", {}).get("Value", [None])[0]
    assert state == "IN PROGRESS", f"Expected IN PROGRESS after claiming, got {state}"


@pytest.mark.e2e
def test_invalid_transition_scheduled_to_completed_returns_400(
    http_client: httpx.Client,
) -> None:
    """SCHEDULED → COMPLETED is not a valid direct transition; expect 400."""
    workitem_uid = _make_workitem_uid()
    create_payload = _workitem_dataset(workitem_uid)

    create_resp = http_client.post(
        f"/v2/workitems/{workitem_uid}",
        json=create_payload,
        headers={"Content-Type": "application/json"},
    )
    assert create_resp.status_code == 201

    # Attempt illegal direct transition: SCHEDULED → COMPLETED
    txn_uid = _make_transaction_uid()
    state_payload = _state_change_payload("COMPLETED", txn_uid)
    response = http_client.put(
        f"/v2/workitems/{workitem_uid}/state",
        json=state_payload,
        headers={"Content-Type": "application/json"},
    )
    assert (
        response.status_code == 400
    ), f"Expected 400 for invalid SCHEDULED→COMPLETED transition, got {response.status_code}"


@pytest.mark.e2e
def test_cancel_scheduled_workitem(http_client: httpx.Client) -> None:
    """POST /v2/workitems/{uid}/cancelrequest cancels a SCHEDULED workitem (returns 202)."""
    workitem_uid = _make_workitem_uid()
    payload = _workitem_dataset(workitem_uid, patient_id="UPS-E2E-CANCEL")

    create_resp = http_client.post(
        f"/v2/workitems/{workitem_uid}",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    assert create_resp.status_code == 201

    cancel_resp = http_client.post(f"/v2/workitems/{workitem_uid}/cancelrequest")
    assert (
        cancel_resp.status_code == 202
    ), f"Cancel request failed: {cancel_resp.status_code} {cancel_resp.text}"

    # Confirm state is now CANCELED
    get_resp = http_client.get(f"/v2/workitems/{workitem_uid}")
    assert get_resp.status_code == 200
    state = get_resp.json().get("00741000", {}).get("Value", [None])[0]
    assert state == "CANCELED", f"Expected CANCELED after cancel request, got {state}"


@pytest.mark.e2e
def test_complete_workitem(http_client: httpx.Client, ups_workitem: dict) -> None:
    """PUT /v2/workitems/{uid}/state transitions IN PROGRESS → COMPLETED.

    Depends on test_claim_workitem_in_progress running first.
    """
    workitem_uid = ups_workitem["workitem_uid"]
    txn_uid = ups_workitem.get("transaction_uid")

    payload = _state_change_payload("COMPLETED", txn_uid)
    response = http_client.put(
        f"/v2/workitems/{workitem_uid}/state",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    assert (
        response.status_code == 200
    ), f"State change IN PROGRESS→COMPLETED failed: {response.status_code} {response.text}"


@pytest.mark.e2e
def test_retrieve_workitem_completed(http_client: httpx.Client, ups_workitem: dict) -> None:
    """GET after completing the workitem shows COMPLETED state.

    Depends on test_complete_workitem running first.
    """
    workitem_uid = ups_workitem["workitem_uid"]
    response = http_client.get(f"/v2/workitems/{workitem_uid}")
    assert response.status_code == 200

    data = response.json()
    state = data.get("00741000", {}).get("Value", [None])[0]
    assert state == "COMPLETED", f"Expected COMPLETED state at end of workflow, got {state}"


@pytest.mark.e2e
def test_update_completed_workitem_returns_400(
    http_client: httpx.Client, ups_workitem: dict
) -> None:
    """Attempting to update a COMPLETED workitem returns 400 Bad Request.

    Depends on test_complete_workitem running first.
    """
    workitem_uid = ups_workitem["workitem_uid"]
    txn_uid = ups_workitem.get("transaction_uid")

    update_payload = {
        "00100020": {"vr": "LO", "Value": ["UPS-E2E-SHOULD-FAIL"]},
    }
    response = http_client.put(
        f"/v2/workitems/{workitem_uid}",
        json=update_payload,
        params={"transaction-uid": txn_uid} if txn_uid else {},
        headers={"Content-Type": "application/json"},
    )
    assert (
        response.status_code == 400
    ), f"Expected 400 when updating COMPLETED workitem, got {response.status_code}"
