"""
DICOMweb Standard API Router (v2)

Implements:
- STOW-RS: Store instances
- WADO-RS: Retrieve instances and metadata
- QIDO-RS: Query/search instances
- DELETE: Remove studies/series/instances
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Request, Response, HTTPException
from sqlalchemy import select, func, and_, delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.dicom import DicomInstance, ChangeFeedEntry
from app.models.events import DicomEvent
from app.services.dicom_engine import (
    parse_dicom,
    dataset_to_dicom_json,
    extract_searchable_metadata,
    store_instance,
    read_instance,
    delete_instance_file,
    validate_required_attributes,
    build_store_response,
)
from app.services.multipart import parse_multipart_related, build_multipart_response
from main import get_event_manager

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Tag mapping for QIDO-RS query parameters ───────────────────────
QIDO_TAG_MAP = {
    "PatientID": "patient_id",
    "00100020": "patient_id",
    "PatientName": "patient_name",
    "00100010": "patient_name",
    "StudyDate": "study_date",
    "00080020": "study_date",
    "AccessionNumber": "accession_number",
    "00080050": "accession_number",
    "StudyDescription": "study_description",
    "00081030": "study_description",
    "Modality": "modality",
    "00080060": "modality",
    "StudyInstanceUID": "study_instance_uid",
    "0020000D": "study_instance_uid",
    "SeriesInstanceUID": "series_instance_uid",
    "0020000E": "series_instance_uid",
    "SOPInstanceUID": "sop_instance_uid",
    "00080018": "sop_instance_uid",
    "ReferringPhysicianName": "referring_physician_name",
    "00080090": "referring_physician_name",
}

# ── DICOM Failure Reason Codes ─────────────────────────────────────
# Used in STOW-RS FailedSOPSequence responses (tag 0x00081197)
FAILURE_REASON_UNABLE_TO_PROCESS = 0xC000  # Processing failure
FAILURE_REASON_UID_MISMATCH = 0xC409        # StudyInstanceUID mismatch


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

    stored = []
    warnings = []
    failures = []
    effective_study_uid = study_instance_uid
    has_warnings = False
    # Track instances for event publishing (after commit)
    instances_to_publish = []

    for part in parts:
        try:
            ds = parse_dicom(part.data)

            # Validate required attributes
            errors = validate_required_attributes(ds)
            if errors:
                failures.append({
                    "00081150": {"vr": "UI", "Value": [str(getattr(ds, "SOPClassUID", ""))]},
                    "00081155": {"vr": "UI", "Value": [str(getattr(ds, "SOPInstanceUID", ""))]},
                    "00081197": {"vr": "US", "Value": [FAILURE_REASON_UNABLE_TO_PROCESS]},
                })
                continue

            sop_uid = str(ds.SOPInstanceUID)
            study_uid = str(ds.StudyInstanceUID)
            series_uid = str(ds.SeriesInstanceUID)

            # If study UID provided in URL, validate match
            if study_instance_uid and study_uid != study_instance_uid:
                failures.append({
                    "00081150": {"vr": "UI", "Value": [str(ds.SOPClassUID)]},
                    "00081155": {"vr": "UI", "Value": [sop_uid]},
                    "00081197": {"vr": "US", "Value": [FAILURE_REASON_UID_MISMATCH]},
                })
                continue

            if effective_study_uid is None:
                effective_study_uid = study_uid

            # Store file to disk
            file_path = store_instance(part.data, ds)
            file_size = len(part.data)

            # Extract metadata
            dicom_json = dataset_to_dicom_json(ds)
            searchable = extract_searchable_metadata(ds)

            # Check for existing instance (update vs create)
            existing = await db.execute(
                select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
            )
            existing_instance = existing.scalar_one_or_none()

            action = "create"
            stored_instance = None
            if existing_instance:
                # Update existing — delete old file, update record
                action = "update"
                delete_instance_file(existing_instance.file_path)
                existing_instance.file_path = file_path
                existing_instance.file_size = file_size
                existing_instance.dicom_json = dicom_json
                existing_instance.transfer_syntax_uid = str(
                    getattr(ds.file_meta, "TransferSyntaxUID", "")
                ) if hasattr(ds, "file_meta") else None
                for col, val in searchable.items():
                    setattr(existing_instance, col, val)
                stored_instance = existing_instance
            else:
                # Create new instance
                instance = DicomInstance(
                    study_instance_uid=study_uid,
                    series_instance_uid=series_uid,
                    sop_instance_uid=sop_uid,
                    sop_class_uid=str(getattr(ds, "SOPClassUID", "")),
                    transfer_syntax_uid=str(
                        getattr(ds.file_meta, "TransferSyntaxUID", "")
                    ) if hasattr(ds, "file_meta") else None,
                    dicom_json=dicom_json,
                    file_path=file_path,
                    file_size=file_size,
                    **searchable,
                )
                db.add(instance)
                stored_instance = instance

            # Add change feed entry
            feed_entry = ChangeFeedEntry(
                study_instance_uid=study_uid,
                series_instance_uid=series_uid,
                sop_instance_uid=sop_uid,
                action=action,
                state="current",
            )
            db.add(feed_entry)

            # If previous change feed entry for this SOP exists, mark as replaced
            if action == "update":
                await _mark_previous_feed_entries(db, sop_uid)

            stored.append({
                "00081150": {"vr": "UI", "Value": [str(ds.SOPClassUID)]},
                "00081155": {"vr": "UI", "Value": [sop_uid]},
                "00081190": {"vr": "UR", "Value": [
                    f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}"
                ]},
            })

            # Track instance for event publishing
            instances_to_publish.append(stored_instance)

        except Exception as e:
            failures.append({
                "00081197": {"vr": "US", "Value": [0xC000]},
                "00081196": {"vr": "LO", "Value": [str(e)]},
            })
            has_warnings = True

    await db.commit()

    # Publish DicomImageCreated events (best-effort, after commit)
    for instance in instances_to_publish:
        try:
            event_manager = get_event_manager()
            event = DicomEvent.from_instance_created(
                study_uid=instance.study_instance_uid,
                series_uid=instance.series_instance_uid,
                instance_uid=instance.sop_instance_uid,
                sequence_number=instance.id,  # Use database ID as sequence
                service_url=str(request.base_url).rstrip("/"),
            )
            await event_manager.publish(event)
        except Exception as e:
            # Log but don't fail the DICOM operation
            logger.error(f"Failed to publish event for instance {instance.sop_instance_uid}: {e}")

    if not effective_study_uid:
        effective_study_uid = "unknown"

    response_body = build_store_response(effective_study_uid, stored, warnings, failures)

    # Azure v2: 200 if all succeed, 202 if warnings, 409 if all fail
    if failures and not stored:
        status_code = 409
    elif has_warnings or warnings:
        status_code = 202
    else:
        status_code = 200

    return Response(
        content=_json_dumps(response_body),
        status_code=status_code,
        media_type="application/dicom+json",
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  WADO-RS — Retrieve Instances & Metadata
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/studies/{study_uid}")
async def wado_rs_study(
    study_uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve all instances in a study as multipart/related."""
    accept = request.headers.get("accept", "")

    if "application/dicom+json" in accept or "metadata" in str(request.url):
        return await _retrieve_metadata(db, study_uid=study_uid)

    return await _retrieve_instances(db, study_uid=study_uid)


@router.get("/studies/{study_uid}/metadata")
async def wado_rs_study_metadata(
    study_uid: str,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve metadata for all instances in a study."""
    return await _retrieve_metadata(db, study_uid=study_uid)


@router.get("/studies/{study_uid}/series/{series_uid}")
async def wado_rs_series(
    study_uid: str,
    series_uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve all instances in a series."""
    accept = request.headers.get("accept", "")
    if "application/dicom+json" in accept:
        return await _retrieve_metadata(db, study_uid=study_uid, series_uid=series_uid)
    return await _retrieve_instances(db, study_uid=study_uid, series_uid=series_uid)


@router.get("/studies/{study_uid}/series/{series_uid}/metadata")
async def wado_rs_series_metadata(
    study_uid: str,
    series_uid: str,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve metadata for all instances in a series."""
    return await _retrieve_metadata(db, study_uid=study_uid, series_uid=series_uid)


@router.get("/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}")
async def wado_rs_instance(
    study_uid: str,
    series_uid: str,
    instance_uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve a single DICOM instance."""
    accept = request.headers.get("accept", "")
    if "application/dicom+json" in accept:
        return await _retrieve_metadata(
            db, study_uid=study_uid, series_uid=series_uid, instance_uid=instance_uid
        )
    return await _retrieve_instances(
        db, study_uid=study_uid, series_uid=series_uid, instance_uid=instance_uid
    )


@router.get("/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}/metadata")
async def wado_rs_instance_metadata(
    study_uid: str,
    series_uid: str,
    instance_uid: str,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve metadata for a single instance."""
    return await _retrieve_metadata(
        db, study_uid=study_uid, series_uid=series_uid, instance_uid=instance_uid
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  QIDO-RS — Query/Search
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/studies")
async def qido_rs_studies(
    request: Request,
    limit: int = Query(100, alias="limit"),
    offset: int = Query(0, alias="offset"),
    fuzzymatching: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """QIDO-RS: Search for studies."""
    filters = _parse_qido_params(request.query_params, fuzzymatching)

    # Build query for distinct studies
    query = select(DicomInstance).where(and_(*filters)) if filters else select(DicomInstance)

    # Get distinct studies with aggregation
    result = await db.execute(
        query.order_by(DicomInstance.study_date.desc()).offset(offset).limit(limit)
    )
    instances = result.scalars().all()

    # Group by study for study-level response
    studies: dict[str, list] = {}
    for inst in instances:
        if inst.study_instance_uid not in studies:
            studies[inst.study_instance_uid] = []
        studies[inst.study_instance_uid].append(inst)

    response = []
    for study_uid, study_instances in studies.items():
        first = study_instances[0]
        modalities = list({i.modality for i in study_instances if i.modality})
        series_uids = list({i.series_instance_uid for i in study_instances})

        study_json = {
            "0020000D": {"vr": "UI", "Value": [study_uid]},
            "00100020": {"vr": "LO", "Value": [first.patient_id] if first.patient_id else {}},
            "00100010": {"vr": "PN", "Value": [{"Alphabetic": first.patient_name}] if first.patient_name else {}},
            "00080020": {"vr": "DA", "Value": [first.study_date] if first.study_date else {}},
            "00080050": {"vr": "SH", "Value": [first.accession_number] if first.accession_number else {}},
            "00081030": {"vr": "LO", "Value": [first.study_description] if first.study_description else {}},
            "00080061": {"vr": "CS", "Value": modalities},  # ModalitiesInStudy
            "00201206": {"vr": "IS", "Value": [len(series_uids)]},  # NumberOfStudyRelatedSeries
            "00201208": {"vr": "IS", "Value": [len(study_instances)]},  # NumberOfStudyRelatedInstances
        }
        response.append(study_json)

    return Response(
        content=_json_dumps(response),
        media_type="application/dicom+json",
    )


@router.get("/studies/{study_uid}/series")
async def qido_rs_series(
    study_uid: str,
    request: Request,
    limit: int = Query(100, alias="limit"),
    offset: int = Query(0, alias="offset"),
    db: AsyncSession = Depends(get_db),
):
    """QIDO-RS: Search for series within a study."""
    query = select(DicomInstance).where(
        DicomInstance.study_instance_uid == study_uid
    ).offset(offset).limit(limit)

    result = await db.execute(query)
    instances = result.scalars().all()

    series: dict[str, list] = {}
    for inst in instances:
        if inst.series_instance_uid not in series:
            series[inst.series_instance_uid] = []
        series[inst.series_instance_uid].append(inst)

    response = []
    for series_uid, series_instances in series.items():
        first = series_instances[0]
        series_json = {
            "0020000D": {"vr": "UI", "Value": [study_uid]},
            "0020000E": {"vr": "UI", "Value": [series_uid]},
            "00080060": {"vr": "CS", "Value": [first.modality] if first.modality else {}},
            "0008103E": {"vr": "LO", "Value": [first.series_description] if first.series_description else {}},
            "00200011": {"vr": "IS", "Value": [first.series_number] if first.series_number else {}},
            "00201209": {"vr": "IS", "Value": [len(series_instances)]},  # NumberOfSeriesRelatedInstances
        }
        response.append(series_json)

    return Response(
        content=_json_dumps(response),
        media_type="application/dicom+json",
    )


@router.get("/studies/{study_uid}/series/{series_uid}/instances")
async def qido_rs_instances(
    study_uid: str,
    series_uid: str,
    request: Request,
    limit: int = Query(100, alias="limit"),
    offset: int = Query(0, alias="offset"),
    db: AsyncSession = Depends(get_db),
):
    """QIDO-RS: Search for instances within a series."""
    query = select(DicomInstance).where(
        and_(
            DicomInstance.study_instance_uid == study_uid,
            DicomInstance.series_instance_uid == series_uid,
        )
    ).offset(offset).limit(limit)

    result = await db.execute(query)
    instances = result.scalars().all()

    response = [inst.dicom_json for inst in instances]

    return Response(
        content=_json_dumps(response),
        media_type="application/dicom+json",
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DELETE — Remove studies/series/instances
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.delete("/studies/{study_uid}")
async def delete_study(study_uid: str, db: AsyncSession = Depends(get_db)):
    """Delete an entire study."""
    return await _delete_instances(db, study_uid=study_uid)


@router.delete("/studies/{study_uid}/series/{series_uid}")
async def delete_series(
    study_uid: str, series_uid: str, db: AsyncSession = Depends(get_db),
):
    """Delete an entire series."""
    return await _delete_instances(db, study_uid=study_uid, series_uid=series_uid)


@router.delete("/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}")
async def delete_instance(
    study_uid: str, series_uid: str, instance_uid: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single instance."""
    return await _delete_instances(
        db, study_uid=study_uid, series_uid=series_uid, instance_uid=instance_uid
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _json_dumps(obj) -> bytes:
    """JSON serialize with support for UUID and datetime."""
    import json

    class Encoder(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, uuid.UUID):
                return str(o)
            if isinstance(o, datetime):
                return o.isoformat()
            return super().default(o)

    return json.dumps(obj, cls=Encoder).encode("utf-8")


def _parse_qido_params(params, fuzzymatching: bool = False) -> list:
    """Convert QIDO-RS query parameters to SQLAlchemy filter conditions."""
    filters = []
    for key, value in params.items():
        if key in ("limit", "offset", "fuzzymatching", "includefield"):
            continue

        db_column = QIDO_TAG_MAP.get(key)
        if db_column:
            column = getattr(DicomInstance, db_column, None)
            if column is not None:
                if "*" in value:
                    # Wildcard matching
                    pattern = value.replace("*", "%")
                    filters.append(column.ilike(pattern))
                elif fuzzymatching and db_column in ("patient_name", "referring_physician_name"):
                    filters.append(column.ilike(f"%{value}%"))
                else:
                    filters.append(column == value)

    return filters


async def _retrieve_metadata(
    db: AsyncSession,
    study_uid: str | None = None,
    series_uid: str | None = None,
    instance_uid: str | None = None,
) -> Response:
    """Retrieve DICOM JSON metadata for matching instances."""
    conditions = []
    if study_uid:
        conditions.append(DicomInstance.study_instance_uid == study_uid)
    if series_uid:
        conditions.append(DicomInstance.series_instance_uid == series_uid)
    if instance_uid:
        conditions.append(DicomInstance.sop_instance_uid == instance_uid)

    query = select(DicomInstance).where(and_(*conditions)) if conditions else select(DicomInstance)
    result = await db.execute(query)
    instances = result.scalars().all()

    if not instances:
        raise HTTPException(status_code=404, detail="No matching instances found")

    metadata = [inst.dicom_json for inst in instances]

    return Response(
        content=_json_dumps(metadata),
        media_type="application/dicom+json",
    )


async def _retrieve_instances(
    db: AsyncSession,
    study_uid: str | None = None,
    series_uid: str | None = None,
    instance_uid: str | None = None,
) -> Response:
    """Retrieve DICOM instances as multipart/related."""
    conditions = []
    if study_uid:
        conditions.append(DicomInstance.study_instance_uid == study_uid)
    if series_uid:
        conditions.append(DicomInstance.series_instance_uid == series_uid)
    if instance_uid:
        conditions.append(DicomInstance.sop_instance_uid == instance_uid)

    query = select(DicomInstance).where(and_(*conditions)) if conditions else select(DicomInstance)
    result = await db.execute(query)
    instances = result.scalars().all()

    if not instances:
        raise HTTPException(status_code=404, detail="No matching instances found")

    # Build multipart response
    boundary = f"boundary-{uuid.uuid4().hex[:16]}"
    parts = []
    for inst in instances:
        data = read_instance(inst.file_path)
        parts.append(("application/dicom", data))

    body = build_multipart_response(parts, boundary)

    return Response(
        content=body,
        media_type=f"multipart/related; type=\"application/dicom\"; boundary={boundary}",
    )


async def _delete_instances(
    db: AsyncSession,
    study_uid: str | None = None,
    series_uid: str | None = None,
    instance_uid: str | None = None,
) -> Response:
    """Delete matching instances from DB and disk."""
    conditions = []
    if study_uid:
        conditions.append(DicomInstance.study_instance_uid == study_uid)
    if series_uid:
        conditions.append(DicomInstance.series_instance_uid == series_uid)
    if instance_uid:
        conditions.append(DicomInstance.sop_instance_uid == instance_uid)

    query = select(DicomInstance).where(and_(*conditions))
    result = await db.execute(query)
    instances = result.scalars().all()

    if not instances:
        raise HTTPException(status_code=404, detail="No matching instances found")

    for inst in instances:
        # Add change feed entry for deletion
        feed_entry = ChangeFeedEntry(
            study_instance_uid=inst.study_instance_uid,
            series_instance_uid=inst.series_instance_uid,
            sop_instance_uid=inst.sop_instance_uid,
            action="delete",
            state="current",
        )
        db.add(feed_entry)

        # Mark previous feed entries
        await _mark_previous_feed_entries(db, inst.sop_instance_uid)

        # Delete file from disk
        delete_instance_file(inst.file_path)

        # Delete from DB
        await db.delete(inst)

    await db.commit()
    return Response(status_code=204)


async def _mark_previous_feed_entries(db: AsyncSession, sop_uid: str):
    """Mark previous change feed entries as replaced/deleted."""
    prev_entries = await db.execute(
        select(ChangeFeedEntry).where(
            and_(
                ChangeFeedEntry.sop_instance_uid == sop_uid,
                ChangeFeedEntry.state == "current",
            )
        ).order_by(ChangeFeedEntry.sequence.desc()).offset(1)
    )
    for entry in prev_entries.scalars().all():
        entry.state = "replaced"
