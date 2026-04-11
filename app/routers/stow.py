"""
STOW-RS Router — Store DICOM instances.

Endpoints:
- POST /v2/studies                        (store new instances)
- POST /v2/studies/{study_instance_uid}   (store into a specific study)
- PUT  /v2/studies                        (upsert instances with optional expiry)
- PUT  /v2/studies/{study_instance_uid}   (upsert into a specific study)
- POST /v2/studies/$bulkUpdate            (bulk-update metadata across studies)
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.dicom import ChangeFeedEntry, DicomInstance, DicomStudy, Operation
from app.models.events import DicomEvent
from app.routers._shared import (
    DICOM_STORAGE_DIR,
    _json_dumps,
    _mark_previous_feed_entries,
    _publish_change_event,
)
from app.services.dicom_engine import (
    build_store_response,
    dataset_to_dicom_json,
    extract_searchable_metadata,
    parse_dicom,
    store_instance,
    validate_required_attributes,
    validate_searchable_attributes,
)
from app.services.multipart import parse_multipart_related
from app.services.upsert import upsert_instance

logger = logging.getLogger(__name__)

router = APIRouter()

# ── DICOM Failure Reason Codes ─────────────────────────────────────
# Used in STOW-RS FailedSOPSequence responses (tag 0x00081197)
FAILURE_REASON_UNABLE_TO_PROCESS = 0xC000  # Processing failure
FAILURE_REASON_UID_MISMATCH = 0xC409  # StudyInstanceUID mismatch
FAILURE_REASON_INSTANCE_ALREADY_EXISTS = 45070  # Instance already exists (0xB00E hex)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Bulk Update — POST /studies/$bulkUpdate
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Tags in changeDataset that map to indexed columns on DicomInstance.
_BULK_UPDATE_TAG_TO_COLUMN: dict[str, str] = {
    "00100020": "patient_id",
    "00100010": "patient_name",
    "00080020": "study_date",
    "00080050": "accession_number",
    "00081030": "study_description",
    "00080060": "modality",
}

# Subset of the above that also exist as columns on DicomStudy.
_BULK_UPDATE_TAG_TO_STUDY_COLUMN: dict[str, str] = {
    "00100020": "patient_id",
}


class BulkUpdateRequest(BaseModel):
    """Request body for POST /v2/studies/$bulkUpdate."""

    study_instance_uids: list[str] | None = Field(default=None, alias="studyInstanceUids")
    change_dataset: dict | None = Field(default=None, alias="changeDataset")

    model_config = {"populate_by_name": True}


def _extract_scalar_value(tag_entry: dict) -> str | None:
    """Extract a simple string value from a DICOM JSON tag entry.

    Returns the first Value element as a string when it is a plain scalar,
    or None when the Value list is empty or the entry has no Value key.

    Note: Integer VR values (IS, DS) are not supported — returns None for
    non-string, non-PN values.  Only use this helper with string-valued DICOM
    tags (LO, LT, SH, DA, CS, etc.).
    """
    values = tag_entry.get("Value")
    if not values:
        return None
    first = values[0]
    if isinstance(first, str):
        return first
    # Person name object — return Alphabetic component
    if isinstance(first, dict):
        return first.get("Alphabetic")
    return None


# NOTE: This route must appear before POST /studies/{study_instance_uid} (STOW-RS)
# so FastAPI matches the literal "$bulkUpdate" segment before treating it as a path param.
@router.post("/studies/$bulkUpdate", status_code=202)
async def bulk_update_studies(
    request: Request,
    body: BulkUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Bulk-update DICOM metadata across all instances in the specified studies.

    Merges ``changeDataset`` tags into each instance's ``dicom_json``.
    Also updates indexed columns for known tags.
    Creates a ``ChangeFeedEntry`` (state=replaced) per updated instance.
    Returns 202 with a Location header pointing to the created operation.

    Azure v2 behaviour:
    - Study UIDs that do not exist are silently skipped.
    - 400 if ``studyInstanceUids`` is absent/empty.
    - 400 if ``changeDataset`` is absent/empty.
    """
    if not body.study_instance_uids:
        raise HTTPException(status_code=400, detail="studyInstanceUids must not be empty")
    if not body.change_dataset:
        raise HTTPException(status_code=400, detail="changeDataset must not be empty")

    # Validate changeDataset structure: each tag entry must be a DICOM JSON object
    # containing a "vr" key (e.g. {"vr": "LO", "Value": [...]}).
    for tag_key, tag_value in body.change_dataset.items():
        if not isinstance(tag_value, dict) or "vr" not in tag_value:
            raise HTTPException(
                status_code=400,
                detail=f"changeDataset tag '{tag_key}' must be a DICOM JSON object with a 'vr' key",
            )

    operation_id = uuid.uuid4()

    # Create the operation record up front (will be committed with instance updates)
    operation = Operation(
        id=operation_id,
        type="bulk-update",
        status="running",
        percent_complete=0,
    )
    db.add(operation)

    updated_count = 0
    studies_updated_count = 0

    try:
        for study_uid in body.study_instance_uids:
            result = await db.execute(
                select(DicomInstance).where(DicomInstance.study_instance_uid == study_uid)
            )
            instances = result.scalars().all()

            for inst in instances:
                # Merge change_dataset into dicom_json (immutable dict update)
                updated_json = {**inst.dicom_json, **body.change_dataset}
                inst.dicom_json = updated_json

                # Update indexed columns for known tags
                for tag, column in _BULK_UPDATE_TAG_TO_COLUMN.items():
                    if tag in body.change_dataset:
                        new_value = _extract_scalar_value(body.change_dataset[tag])
                        setattr(inst, column, new_value)

                # Create a change feed entry for this update
                feed_entry = ChangeFeedEntry(
                    study_instance_uid=inst.study_instance_uid,
                    series_instance_uid=inst.series_instance_uid,
                    sop_instance_uid=inst.sop_instance_uid,
                    action="update",
                    state="replaced",
                    dicom_metadata=updated_json,
                )
                db.add(feed_entry)
                updated_count += 1

            if instances:
                studies_updated_count += 1
                # Update (or create) the DicomStudy record for this study with
                # whichever changeDataset tags map to DicomStudy columns.
                study_result = await db.execute(
                    select(DicomStudy).where(DicomStudy.study_instance_uid == study_uid)
                )
                dicom_study = study_result.scalar_one_or_none()
                if dicom_study is None:
                    dicom_study = DicomStudy(study_instance_uid=study_uid)
                    db.add(dicom_study)
                for tag, column in _BULK_UPDATE_TAG_TO_STUDY_COLUMN.items():
                    if tag in body.change_dataset:
                        new_value = _extract_scalar_value(body.change_dataset[tag])
                        setattr(dicom_study, column, new_value)

        # Mark operation as succeeded
        operation.status = "succeeded"
        operation.percent_complete = 100
        operation.results = {
            "studiesUpdated": studies_updated_count,
            "instancesUpdated": updated_count,
        }

        await db.commit()
    except Exception:
        await db.rollback()
        operation.status = "failed"
        try:
            db.add(operation)  # re-attach after rollback cleared pending objects
            await db.commit()  # best-effort to save the failed status
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Bulk update failed")

    location = str(request.base_url).rstrip("/") + f"/v2/operations/{operation_id}"
    return Response(
        status_code=202,
        headers={"Location": location},
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STOW-RS — Shared per-instance processing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class StowResult:
    """Collected results from processing a batch of DICOM multipart parts."""

    stored: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    has_warnings: bool = False
    effective_study_uid: str | None = None
    # Pairs of (DicomInstance, ChangeFeedEntry) for post-commit event publishing
    instances_to_publish: list[tuple] = field(default_factory=list)


async def _process_stow_instances(
    parts: list,
    db: AsyncSession,
    *,
    study_instance_uid: str | None = None,
    upsert: bool = False,
) -> StowResult:
    """Process a list of multipart DICOM parts and store or upsert each instance.

    Args:
        parts: Parsed multipart parts, each with a ``data`` attribute.
        db: An open async database session.
        study_instance_uid: When provided, every part must belong to this study UID;
            mismatches are recorded as failures.
        upsert: When ``True`` (PUT semantics) use :func:`upsert_instance` so that
            existing instances are replaced rather than rejected.  When ``False``
            (POST semantics) duplicate SOPs are recorded as failures and searchable
            attribute validation warnings are collected.

    Returns:
        A :class:`StowResult` with the stored/warning/failure lists and any
        (instance, feed_entry) pairs that should have events published after the
        caller commits the session.
    """
    result = StowResult(effective_study_uid=study_instance_uid)

    for part in parts:
        try:
            ds = parse_dicom(part.data)

            # Validate required attributes — failure for both POST and PUT
            errors = validate_required_attributes(ds)
            if errors:
                result.failures.append(
                    {
                        "00081150": {"vr": "UI", "Value": [str(getattr(ds, "SOPClassUID", ""))]},
                        "00081155": {"vr": "UI", "Value": [str(getattr(ds, "SOPInstanceUID", ""))]},
                        "00081197": {"vr": "US", "Value": [FAILURE_REASON_UNABLE_TO_PROCESS]},
                    }
                )
                continue

            # POST only: validate searchable attributes (Azure v2 — warnings, not failures)
            if not upsert:
                validation_warnings = validate_searchable_attributes(ds)
                if validation_warnings:
                    result.has_warnings = True
                    result.warnings.append({})
                    sop_uid_temp = str(getattr(ds, "SOPInstanceUID", "unknown"))
                    logger.warning(
                        f"Searchable attribute warnings for {sop_uid_temp}: {validation_warnings}"
                    )

            sop_uid = str(ds.SOPInstanceUID)
            study_uid = str(ds.StudyInstanceUID)
            series_uid = str(ds.SeriesInstanceUID)

            # Validate study UID match when provided in URL
            if study_instance_uid and study_uid != study_instance_uid:
                result.failures.append(
                    {
                        "00081150": {"vr": "UI", "Value": [str(ds.SOPClassUID)]},
                        "00081155": {"vr": "UI", "Value": [sop_uid]},
                        "00081197": {"vr": "US", "Value": [FAILURE_REASON_UID_MISMATCH]},
                    }
                )
                continue

            if result.effective_study_uid is None:
                result.effective_study_uid = study_uid

            # Extract metadata (needed by both paths)
            dicom_json = dataset_to_dicom_json(ds)
            searchable = extract_searchable_metadata(ds)

            if upsert:
                # PUT path: create-or-replace via upsert service
                dcm_data = {
                    "file_data": part.data,
                    "dicom_json": dicom_json,
                    "metadata": {
                        "sop_class_uid": str(getattr(ds, "SOPClassUID", "")),
                        "transfer_syntax_uid": str(getattr(ds.file_meta, "TransferSyntaxUID", ""))
                        if hasattr(ds, "file_meta")
                        else None,
                        **searchable,
                    },
                }
                action = await upsert_instance(
                    db, study_uid, series_uid, sop_uid, dcm_data, str(DICOM_STORAGE_DIR)
                )

                feed_entry = ChangeFeedEntry(
                    study_instance_uid=study_uid,
                    series_instance_uid=series_uid,
                    sop_instance_uid=sop_uid,
                    action=action,
                    state="current",
                )
                db.add(feed_entry)

                if action == "replaced":
                    await _mark_previous_feed_entries(db, sop_uid)

                # Re-query to get the persisted instance for event publishing
                inst_result = await db.execute(
                    select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
                )
                stored_instance = inst_result.scalar_one_or_none()

            else:
                # POST path: reject duplicates, then insert
                existing_check = await db.execute(
                    select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
                )
                existing_instance = existing_check.scalar_one_or_none()

                if existing_instance:
                    result.failures.append(
                        {
                            "00081150": {"vr": "UI", "Value": [str(ds.SOPClassUID)]},
                            "00081155": {"vr": "UI", "Value": [sop_uid]},
                            "00081197": {
                                "vr": "US",
                                "Value": [FAILURE_REASON_INSTANCE_ALREADY_EXISTS],
                            },
                        }
                    )
                    continue

                file_path = await store_instance(part.data, ds)
                file_size = len(part.data)

                instance = DicomInstance(
                    study_instance_uid=study_uid,
                    series_instance_uid=series_uid,
                    sop_instance_uid=sop_uid,
                    sop_class_uid=str(getattr(ds, "SOPClassUID", "")),
                    transfer_syntax_uid=str(getattr(ds.file_meta, "TransferSyntaxUID", ""))
                    if hasattr(ds, "file_meta")
                    else None,
                    dicom_json=dicom_json,
                    file_path=file_path,
                    file_size=file_size,
                    **searchable,
                )
                db.add(instance)
                stored_instance = instance

                feed_entry = ChangeFeedEntry(
                    study_instance_uid=study_uid,
                    series_instance_uid=series_uid,
                    sop_instance_uid=sop_uid,
                    action="create",
                    state="current",
                )
                db.add(feed_entry)

            result.stored.append(
                {
                    "00081150": {"vr": "UI", "Value": [str(ds.SOPClassUID)]},
                    "00081155": {"vr": "UI", "Value": [sop_uid]},
                    "00081190": {
                        "vr": "UR",
                        "Value": [
                            f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}"
                        ],
                    },
                }
            )

            if stored_instance:
                result.instances_to_publish.append((stored_instance, feed_entry))

        except Exception as e:
            result.failures.append(
                {
                    "00081197": {"vr": "US", "Value": [0xC000]},
                    "00081196": {"vr": "LO", "Value": [str(e)]},
                }
            )
            result.has_warnings = True

    return result


async def _publish_stow_events(
    instances_to_publish: list[tuple],
    request: Request,
) -> None:
    """Publish DicomImageCreated events for successfully stored instances.

    Best-effort: errors are logged but not re-raised so that the DICOM operation
    already committed to the database is never rolled back due to an event failure.
    """
    service_url = str(request.base_url).rstrip("/")
    for instance, feed_entry in instances_to_publish:
        event = DicomEvent.from_instance_created(
            study_uid=instance.study_instance_uid,
            series_uid=instance.series_instance_uid,
            instance_uid=instance.sop_instance_uid,
            sequence_number=feed_entry.sequence,
            service_url=service_url,
        )
        await _publish_change_event(event, instance.sop_instance_uid)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STOW-RS — Store Instances
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@router.post("/studies")
@router.post("/studies/{study_instance_uid}")
async def stow_rs(
    request: Request,
    study_instance_uid: Optional[str] = None,
    content_type: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    """
    STOW-RS: Store DICOM instances.

    Accepts multipart/related with application/dicom parts.
    Azure v2 behavior: only fails on missing required attributes;
    searchable attribute issues return HTTP 202 with warnings.
    """
    body = await request.body()
    parts = parse_multipart_related(body, content_type)

    result = await _process_stow_instances(
        parts, db, study_instance_uid=study_instance_uid, upsert=False
    )

    await db.commit()

    await _publish_stow_events(result.instances_to_publish, request)

    effective_study_uid = result.effective_study_uid or "unknown"
    response_body = build_store_response(
        effective_study_uid, result.stored, result.warnings, result.failures
    )

    # Azure v2: 200 if all succeed, 202 if warnings or partial success, 409 if all fail
    if result.failures and not result.stored:
        # All instances failed (duplicates, validation errors, exceptions)
        status_code = 409
    elif result.has_warnings or result.warnings or result.failures:
        # Return 202 for warnings OR partial success (some stored, some failed)
        status_code = 202
    else:
        # All succeeded
        status_code = 200

    return Response(
        content=_json_dumps(response_body),
        status_code=status_code,
        media_type="application/dicom+json",
    )


@router.put("/studies")
@router.put("/studies/{study_instance_uid}")
async def stow_rs_put(
    request: Request,
    study_instance_uid: Optional[str] = None,
    content_type: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    """
    STOW-RS with PUT (Upsert): Store or update DICOM instances with optional expiry.

    Supports expiry headers for automatic study deletion:
    - msdicom-expiry-time-milliseconds: Time until expiry in milliseconds
    - msdicom-expiry-option: Must be "RelativeToNow"
    - msdicom-expiry-level: Must be "Study"

    Azure v2 behavior: Unlike POST, PUT does not emit 45070 warnings for upsert.
    """
    # Parse expiry headers
    expiry_ms = request.headers.get("msdicom-expiry-time-milliseconds")
    expiry_option = request.headers.get("msdicom-expiry-option")
    expiry_level = request.headers.get("msdicom-expiry-level")

    expires_at = None
    if expiry_ms and expiry_option == "RelativeToNow" and expiry_level == "Study":
        expiry_seconds = int(expiry_ms) / 1000
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expiry_seconds)

    body = await request.body()
    parts = parse_multipart_related(body, content_type)

    result = await _process_stow_instances(
        parts, db, study_instance_uid=study_instance_uid, upsert=True
    )

    # Create/update DicomStudy with expiry (PUT-only feature)
    if result.effective_study_uid and expires_at:
        study_result = await db.execute(
            select(DicomStudy).where(DicomStudy.study_instance_uid == result.effective_study_uid)
        )
        study = study_result.scalar_one_or_none()
        if not study:
            study = DicomStudy(study_instance_uid=result.effective_study_uid)
            db.add(study)
        study.expires_at = expires_at

    await db.commit()

    await _publish_stow_events(result.instances_to_publish, request)

    effective_study_uid = result.effective_study_uid or "unknown"

    # Build response (no warnings for upsert — Azure v2 behavior)
    response_body = build_store_response(effective_study_uid, result.stored, [], result.failures)

    # Azure v2: 200 if all succeed, 202 if warnings, 409 if all fail
    if result.failures and not result.stored:
        status_code = 409
    elif result.has_warnings:
        status_code = 202
    else:
        status_code = 200

    return Response(
        content=_json_dumps(response_body),
        status_code=status_code,
        media_type="application/dicom+json",
    )
