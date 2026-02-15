"""UPS-RS (Unified Procedure Step) router."""

import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.dicom import Workitem

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/workitems",
    status_code=201,
    summary="Create workitem (UPS-RS)",
)
@router.post(
    "/workitems/{workitem_uid}",
    status_code=201,
    summary="Create workitem (UPS-RS)",
)
async def create_workitem(
    request: Request,
    workitem_uid: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Create UPS workitem.

    Workitem UID can be provided in:
    1. Query parameter: /v2/workitems?{workitem_uid}
    2. Path parameter: /v2/workitems/{workitem_uid}
    3. Dataset: 00080018 (SOP Instance UID)

    Returns:
    - 201 Created with Location header
    - 400 Bad Request if validation fails
    - 409 Conflict if workitem already exists
    """
    payload = await request.json()

    # Extract workitem UID from query string if not in path
    if not workitem_uid:
        query_string = request.url.query
        if query_string:
            # Query parameter format: /v2/workitems?{uid}
            workitem_uid = query_string

    # If still not found, try to get from dataset
    if not workitem_uid:
        # Try to get from dataset
        sop_instance_tag = payload.get("00080018", {})
        sop_values = sop_instance_tag.get("Value", [])
        if sop_values:
            workitem_uid = sop_values[0]

    if not workitem_uid:
        raise HTTPException(
            status_code=400,
            detail="SOP Instance UID (0008,0018) required"
        )

    # Validate ProcedureStepState is SCHEDULED
    state_tag = payload.get("00741000", {})
    state_values = state_tag.get("Value", [])
    if state_values and state_values[0] != "SCHEDULED":
        raise HTTPException(
            status_code=400,
            detail="ProcedureStepState must be SCHEDULED for new workitem"
        )

    # Validate no Transaction UID
    if "00081195" in payload:
        raise HTTPException(
            status_code=400,
            detail="Transaction UID must not be present in new workitem"
        )

    # Check if workitem already exists
    result = await db.execute(
        select(Workitem).where(Workitem.sop_instance_uid == workitem_uid)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Workitem {workitem_uid} already exists"
        )

    # Extract searchable attributes
    patient_name = None
    patient_id = None

    patient_name_tag = payload.get("00100010", {})
    if patient_name_tag.get("Value"):
        pn_value = patient_name_tag["Value"][0]
        if isinstance(pn_value, dict):
            patient_name = pn_value.get("Alphabetic", "")
        else:
            patient_name = str(pn_value)

    patient_id_tag = payload.get("00100020", {})
    patient_id_values = patient_id_tag.get("Value", [])
    if patient_id_values:
        patient_id = patient_id_values[0]

    # Create workitem
    workitem = Workitem(
        sop_instance_uid=workitem_uid,
        procedure_step_state="SCHEDULED",
        patient_name=patient_name,
        patient_id=patient_id,
        dicom_dataset=payload
    )

    db.add(workitem)
    await db.commit()

    # Return 201 with Location header
    return Response(
        status_code=201,
        headers={"Location": f"/v2/workitems/{workitem_uid}"}
    )


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

    Returns workitem DICOM dataset.
    Transaction UID is NEVER included in response (security).

    Returns:
    - 200 OK with DICOM JSON
    - 404 Not Found if workitem doesn't exist
    """
    result = await db.execute(
        select(Workitem).where(Workitem.sop_instance_uid == workitem_uid)
    )
    workitem = result.scalar_one_or_none()

    if not workitem:
        raise HTTPException(
            status_code=404,
            detail=f"Workitem {workitem_uid} not found"
        )

    # Return DICOM dataset WITHOUT transaction UID
    dataset = workitem.dicom_dataset.copy()

    # Remove transaction UID (security)
    dataset.pop("00081195", None)

    return dataset
