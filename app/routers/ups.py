"""UPS-RS (Unified Procedure Step) router."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.dicom import Workitem

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/workitems",
    status_code=201,
    summary="Create workitem (UPS-RS)",
)
async def create_workitem(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Create UPS workitem (no UID in path).

    Workitem UID can be provided in:
    1. Query parameter: /v2/workitems?{workitem_uid}
    2. Dataset: 00080018 (SOP Instance UID)

    Returns:
    - 201 Created with Location header
    - 400 Bad Request if validation fails
    - 409 Conflict if workitem already exists
    """
    payload = await request.json()

    workitem_uid = None

    # Extract workitem UID from query string (format: /v2/workitems?{uid})
    query_string = request.url.query
    if query_string:
        workitem_uid = query_string

    # If still not found, try to get from dataset
    if not workitem_uid:
        sop_instance_tag = payload.get("00080018", {})
        sop_values = sop_instance_tag.get("Value", [])
        if sop_values:
            workitem_uid = sop_values[0]

    if not workitem_uid:
        raise HTTPException(status_code=400, detail="SOP Instance UID (0008,0018) required")

    return await _do_create_workitem(workitem_uid=workitem_uid, payload=payload, db=db)


@router.post(
    "/workitems/{workitem_uid}",
    status_code=201,
    summary="Create or update workitem with UID in path (UPS-RS)",
)
async def create_or_update_workitem(
    workitem_uid: str,
    request: Request,
    transaction_uid: str | None = Query(default=None, alias="transaction-uid"),
    db: AsyncSession = Depends(get_db),
):
    """
    Create or update a workitem, routing by existence + ``transaction-uid``.

    - Workitem does **not** exist → create (``transaction-uid`` must be absent).
    - Workitem **exists** + ``transaction-uid`` present → update (Azure SDK
      conformance statement §10.5: ``POST /v2/workitems/{uid}?transaction-uid=xxx``).
    - Workitem **exists** + no ``transaction-uid`` → update SCHEDULED workitem
      (no transaction lock required for SCHEDULED state).

    Returns:
    - 201 Created (create path); 200 OK (update path — returned via
      ``Response(status_code=200)``, overriding the decorator default)
    - 400/404/409 on errors
    """
    payload = await request.json()

    # Query param takes precedence; fall back to header for consistency with PUT.
    txn_uid = transaction_uid or request.headers.get("Transaction-UID")

    # Check if workitem exists to decide create vs. update
    result = await db.execute(select(Workitem).where(Workitem.sop_instance_uid == workitem_uid))
    existing = result.scalar_one_or_none()

    if existing is None:
        # Create path — transaction-uid must not be supplied
        if txn_uid is not None:
            raise HTTPException(
                status_code=400,
                detail="transaction-uid must not be supplied when creating a workitem",
            )
        return await _do_create_workitem(workitem_uid=workitem_uid, payload=payload, db=db)

    # Update path — workitem exists; pass resolved transaction UID
    return await _do_update_workitem(
        workitem_uid=workitem_uid,
        payload=payload,
        txn_uid=txn_uid,
        db=db,
    )


@router.put(
    "/workitems/{workitem_uid}",
    status_code=200,
    summary="Update workitem (UPS-RS)",
)
async def update_workitem(
    workitem_uid: str,
    request: Request,
    transaction_uid: str | None = Query(default=None, alias="transaction-uid"),
    db: AsyncSession = Depends(get_db),
):
    """
    Update UPS workitem attributes (PUT — backwards-compatible).

    Transaction UID can be supplied as:
    - ``transaction-uid`` query param (Azure SDK style)
    - ``Transaction-UID`` request header (legacy)

    Transaction UID is required for IN PROGRESS workitems.
    Cannot update COMPLETED/CANCELED workitems.

    Returns:
    - 200 OK on success
    - 400 Bad Request if invalid update
    - 404 Not Found if workitem doesn't exist
    - 409 Conflict if transaction UID doesn't match
    """
    payload = await request.json()

    # Query param takes precedence; fall back to header for backwards compat.
    txn_uid = transaction_uid or request.headers.get("Transaction-UID")

    return await _do_update_workitem(
        workitem_uid=workitem_uid,
        payload=payload,
        txn_uid=txn_uid,
        db=db,
    )


# ── Internal helpers (shared logic) ───────────────────────────────────────────


async def _do_create_workitem(
    workitem_uid: str,
    payload: dict,
    db: AsyncSession,
) -> Response:
    """Create a new workitem. Raises HTTPException on validation failure."""
    # Validate ProcedureStepState is SCHEDULED
    state_tag = payload.get("00741000", {})
    state_values = state_tag.get("Value", [])
    if state_values and state_values[0] != "SCHEDULED":
        raise HTTPException(
            status_code=400, detail="ProcedureStepState must be SCHEDULED for new workitem"
        )

    # Validate no Transaction UID in payload
    if "00081195" in payload:
        raise HTTPException(
            status_code=400, detail="Transaction UID must not be present in new workitem"
        )

    # Extract searchable attributes
    patient_name, patient_id = _extract_searchable(payload)

    workitem = Workitem(
        sop_instance_uid=workitem_uid,
        procedure_step_state="SCHEDULED",
        patient_name=patient_name,
        patient_id=patient_id,
        dicom_dataset=payload,
    )

    db.add(workitem)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Workitem {workitem_uid} already exists")

    return Response(status_code=201, headers={"Location": f"/v2/workitems/{workitem_uid}"})


async def _do_update_workitem(
    workitem_uid: str,
    payload: dict,
    txn_uid: str | None,
    db: AsyncSession,
) -> Response:
    """Update an existing workitem. Raises HTTPException on validation failure."""
    result = await db.execute(select(Workitem).where(Workitem.sop_instance_uid == workitem_uid))
    workitem = result.scalar_one_or_none()

    if not workitem:
        raise HTTPException(status_code=404, detail=f"Workitem {workitem_uid} not found")

    # Validate cannot change SOP Instance UID
    if "00080018" in payload:
        sop_value = payload["00080018"].get("Value", [])
        if sop_value and sop_value[0] != workitem_uid:
            raise HTTPException(status_code=400, detail="Cannot change SOP Instance UID")

    # Validate cannot change state via update endpoint
    if "00741000" in payload:
        raise HTTPException(
            status_code=400, detail="Use state change endpoint to modify ProcedureStepState"
        )

    # Validate cannot add/change transaction UID in payload
    if "00081195" in payload:
        raise HTTPException(status_code=400, detail="Cannot modify Transaction UID")

    # Check terminal states (400 Bad Request)
    if workitem.procedure_step_state in ["COMPLETED", "CANCELED"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot update workitem in {workitem.procedure_step_state} state",
        )

    # Validate transaction UID ownership (409 Conflict)
    from app.services.ups_state_machine import StateTransitionError, can_update_workitem

    try:
        can_update_workitem(
            current_state=workitem.procedure_step_state,
            current_txn_uid=workitem.transaction_uid,
            provided_txn_uid=txn_uid,
        )
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # Merge updates into dataset
    updated_dataset = workitem.dicom_dataset.copy()
    updated_dataset.update(payload)

    # Re-extract searchable attributes from merged dataset
    patient_name, patient_id = _extract_searchable(updated_dataset)

    workitem.dicom_dataset = updated_dataset
    workitem.patient_name = patient_name
    workitem.patient_id = patient_id

    await db.commit()

    return Response(status_code=200)


def _extract_searchable(dataset: dict) -> tuple[str | None, str | None]:
    """Extract patient_name and patient_id from a DICOM JSON dataset."""
    patient_name = None
    patient_id = None

    patient_name_tag = dataset.get("00100010", {})
    if patient_name_tag.get("Value"):
        pn_value = patient_name_tag["Value"][0]
        if isinstance(pn_value, dict):
            patient_name = pn_value.get("Alphabetic", "")
        else:
            patient_name = str(pn_value)

    patient_id_tag = dataset.get("00100020", {})
    patient_id_values = patient_id_tag.get("Value", [])
    if patient_id_values:
        patient_id = patient_id_values[0]

    return patient_name, patient_id


# ── Read ───────────────────────────────────────────────────────────────────────


@router.get(
    "/workitems",
    summary="Search workitems (UPS-RS)",
)
async def search_workitems(
    PatientID: str | None = Query(default=None),
    PatientName: str | None = Query(default=None),
    ProcedureStepState: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    Search for workitems.

    Query parameters:
    - PatientID: Filter by patient identifier
    - PatientName: Filter by patient name (case-insensitive)
    - ProcedureStepState: Filter by state (SCHEDULED, IN PROGRESS, etc.)
    - limit: Max results (default 100)
    - offset: Pagination offset

    Returns:
    - 200 OK with array of workitems (transaction UIDs removed)
    """
    query = select(Workitem)

    if PatientID:
        query = query.where(Workitem.patient_id == PatientID)

    if PatientName:
        query = query.where(Workitem.patient_name.ilike(f"%{PatientName}%"))

    if ProcedureStepState:
        query = query.where(Workitem.procedure_step_state == ProcedureStepState)

    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    workitems = result.scalars().all()

    results = []
    for workitem in workitems:
        dataset = workitem.dicom_dataset.copy()
        dataset.pop("00081195", None)  # Never expose transaction UID
        results.append(dataset)

    return results


@router.get(
    "/workitems/subscriptions",
    status_code=501,
    summary="List subscriptions (UPS-RS) - NOT IMPLEMENTED",
)
async def list_subscriptions():
    """List all subscriptions - NOT IMPLEMENTED."""
    raise HTTPException(status_code=501, detail="Subscriptions not implemented in this emulator")


@router.get(
    "/workitems/{workitem_uid}",
    summary="Retrieve workitem (UPS-RS)",
)
async def retrieve_workitem(
    workitem_uid: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve UPS workitem.

    Returns workitem DICOM dataset without the Transaction UID (security).

    Returns:
    - 200 OK with DICOM JSON
    - 404 Not Found if workitem doesn't exist
    """
    result = await db.execute(select(Workitem).where(Workitem.sop_instance_uid == workitem_uid))
    workitem = result.scalar_one_or_none()

    if not workitem:
        raise HTTPException(status_code=404, detail=f"Workitem {workitem_uid} not found")

    dataset = workitem.dicom_dataset.copy()
    dataset.pop("00081195", None)  # Never expose transaction UID

    return dataset


# ── State / lifecycle ──────────────────────────────────────────────────────────


@router.put(
    "/workitems/{workitem_uid}/state",
    status_code=200,
    summary="Change workitem state (UPS-RS)",
)
async def change_workitem_state(
    workitem_uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Change workitem state (claim, complete, cancel).

    State machine enforces valid transitions and transaction UID ownership.

    Returns:
    - 200 OK on success
    - 400 Bad Request if invalid transition or transaction UID mismatch
    - 404 Not Found if workitem doesn't exist
    """
    payload = await request.json()

    result = await db.execute(select(Workitem).where(Workitem.sop_instance_uid == workitem_uid))
    workitem = result.scalar_one_or_none()

    if not workitem:
        raise HTTPException(status_code=404, detail=f"Workitem {workitem_uid} not found")

    state_tag = payload.get("00741000", {})
    state_values = state_tag.get("Value", [])
    if not state_values:
        raise HTTPException(status_code=400, detail="ProcedureStepState (0074,1000) required")
    new_state = state_values[0]

    txn_tag = payload.get("00081195", {})
    txn_values = txn_tag.get("Value", [])
    provided_txn_uid = txn_values[0] if txn_values else None

    from app.services.ups_state_machine import StateTransitionError, validate_state_transition

    try:
        validate_state_transition(
            current_state=workitem.procedure_step_state,
            new_state=new_state,
            current_txn_uid=workitem.transaction_uid,
            provided_txn_uid=provided_txn_uid,
        )
    except StateTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    workitem.procedure_step_state = new_state

    if new_state == "IN PROGRESS" and workitem.transaction_uid is None:
        workitem.transaction_uid = provided_txn_uid

    updated_dataset = workitem.dicom_dataset.copy()
    updated_dataset["00741000"] = {"vr": "CS", "Value": [new_state]}

    if new_state == "IN PROGRESS" and provided_txn_uid:
        updated_dataset["00081195"] = {"vr": "UI", "Value": [provided_txn_uid]}

    workitem.dicom_dataset = updated_dataset

    await db.commit()

    return Response(status_code=200)


@router.post(
    "/workitems/{workitem_uid}/cancelrequest",
    status_code=202,
    summary="Request workitem cancellation (UPS-RS)",
)
async def request_cancellation(
    workitem_uid: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Request cancellation of SCHEDULED workitem.

    For IN PROGRESS workitems, use the state change endpoint with a transaction UID.

    Returns:
    - 202 Accepted on success
    - 400 Bad Request if not SCHEDULED
    - 404 Not Found if workitem doesn't exist
    """
    result = await db.execute(select(Workitem).where(Workitem.sop_instance_uid == workitem_uid))
    workitem = result.scalar_one_or_none()

    if not workitem:
        raise HTTPException(status_code=404, detail=f"Workitem {workitem_uid} not found")

    if workitem.procedure_step_state != "SCHEDULED":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel workitem in {workitem.procedure_step_state} state. "
            "Use state change endpoint for IN PROGRESS workitems.",
        )

    workitem.procedure_step_state = "CANCELED"

    updated_dataset = workitem.dicom_dataset.copy()
    updated_dataset["00741000"] = {"vr": "CS", "Value": ["CANCELED"]}
    workitem.dicom_dataset = updated_dataset

    await db.commit()

    return Response(status_code=202)


@router.post(
    "/workitems/{workitem_uid}/subscribers/{aet}",
    status_code=501,
    summary="Subscribe to workitem (UPS-RS) - NOT IMPLEMENTED",
)
async def subscribe_to_workitem(workitem_uid: str, aet: str):
    """Subscribe to workitem events - NOT IMPLEMENTED."""
    raise HTTPException(status_code=501, detail="Subscriptions not implemented in this emulator")


@router.delete(
    "/workitems/{workitem_uid}/subscribers/{aet}",
    status_code=501,
    summary="Unsubscribe from workitem (UPS-RS) - NOT IMPLEMENTED",
)
async def unsubscribe_from_workitem(workitem_uid: str, aet: str):
    """Unsubscribe from workitem events - NOT IMPLEMENTED."""
    raise HTTPException(status_code=501, detail="Subscriptions not implemented in this emulator")
