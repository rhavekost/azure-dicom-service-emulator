"""
DICOMweb Standard API Router (v2)

Implements:
- STOW-RS: Store instances
- WADO-RS: Retrieve instances and metadata
- QIDO-RS: Query/search instances
- DELETE: Remove studies/series/instances
"""

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.dicom import ChangeFeedEntry, DicomInstance, DicomStudy
from app.models.events import DicomEvent
from app.services.dicom_engine import (
    build_store_response,
    dataset_to_dicom_json,
    delete_instance_file,
    extract_searchable_metadata,
    parse_dicom,
    read_instance,
    store_instance,
    validate_required_attributes,
    validate_searchable_attributes,
)
from app.services.frame_cache import FrameCache
from app.services.image_rendering import render_frame as render_frame_to_image
from app.services.multipart import build_multipart_response, parse_multipart_related
from app.services.search_utils import build_fuzzy_name_filter, parse_uid_list, translate_wildcards
from app.services.upsert import upsert_instance

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize frame cache
DICOM_STORAGE_DIR = Path(os.getenv("DICOM_STORAGE_DIR", "/data/dicom"))
frame_cache = FrameCache(DICOM_STORAGE_DIR)

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
FAILURE_REASON_UID_MISMATCH = 0xC409  # StudyInstanceUID mismatch
FAILURE_REASON_INSTANCE_ALREADY_EXISTS = 45070  # Instance already exists (0xB00E hex)


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
                failures.append(
                    {
                        "00081150": {"vr": "UI", "Value": [str(getattr(ds, "SOPClassUID", ""))]},
                        "00081155": {"vr": "UI", "Value": [str(getattr(ds, "SOPInstanceUID", ""))]},
                        "00081197": {"vr": "US", "Value": [FAILURE_REASON_UNABLE_TO_PROCESS]},
                    }
                )
                continue

            # Validate searchable attributes (Azure v2: warnings, not failures)
            validation_warnings = validate_searchable_attributes(ds)
            if validation_warnings:
                has_warnings = True
                # Add to warnings list to trigger WarningReason in response
                # We add a placeholder dict since build_store_response only checks truthiness
                warnings.append({})
                # Log warnings but continue storing
                sop_uid_temp = str(getattr(ds, "SOPInstanceUID", "unknown"))
                logger.warning(
                    f"Searchable attribute warnings for {sop_uid_temp}: {validation_warnings}"
                )

            sop_uid = str(ds.SOPInstanceUID)
            study_uid = str(ds.StudyInstanceUID)
            series_uid = str(ds.SeriesInstanceUID)

            # If study UID provided in URL, validate match
            if study_instance_uid and study_uid != study_instance_uid:
                failures.append(
                    {
                        "00081150": {"vr": "UI", "Value": [str(ds.SOPClassUID)]},
                        "00081155": {"vr": "UI", "Value": [sop_uid]},
                        "00081197": {"vr": "US", "Value": [FAILURE_REASON_UID_MISMATCH]},
                    }
                )
                continue

            if effective_study_uid is None:
                effective_study_uid = study_uid

            # Check for duplicate (POST should reject, PUT should upsert)
            existing_check = await db.execute(
                select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
            )
            existing_instance = existing_check.scalar_one_or_none()

            if existing_instance:
                # Duplicate detected - add to failed sequence
                failures.append(
                    {
                        "00081150": {"vr": "UI", "Value": [str(ds.SOPClassUID)]},
                        "00081155": {"vr": "UI", "Value": [sop_uid]},
                        "00081197": {"vr": "US", "Value": [FAILURE_REASON_INSTANCE_ALREADY_EXISTS]},
                    }
                )
                continue  # Skip storing this duplicate

            # Store file to disk
            file_path = store_instance(part.data, ds)
            file_size = len(part.data)

            # Extract metadata
            dicom_json = dataset_to_dicom_json(ds)
            searchable = extract_searchable_metadata(ds)

            # Create new instance (POST never updates, only creates)
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

            # Add change feed entry
            feed_entry = ChangeFeedEntry(
                study_instance_uid=study_uid,
                series_instance_uid=series_uid,
                sop_instance_uid=sop_uid,
                action="create",
                state="current",
            )
            db.add(feed_entry)

            stored.append(
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

            # Track instance and feed_entry for event publishing
            if stored_instance:
                instances_to_publish.append((stored_instance, feed_entry))

        except Exception as e:
            failures.append(
                {
                    "00081197": {"vr": "US", "Value": [0xC000]},
                    "00081196": {"vr": "LO", "Value": [str(e)]},
                }
            )
            has_warnings = True

    await db.commit()

    # Publish DicomImageCreated events (best-effort, after commit)
    for instance, feed_entry in instances_to_publish:
        try:
            from main import get_event_manager

            event_manager = get_event_manager()
            event = DicomEvent.from_instance_created(
                study_uid=instance.study_instance_uid,
                series_uid=instance.series_instance_uid,
                instance_uid=instance.sop_instance_uid,
                sequence_number=feed_entry.sequence,  # Use ChangeFeedEntry sequence (int)
                service_url=str(request.base_url).rstrip("/"),
            )
            await event_manager.publish(event)
        except Exception as e:
            # Log but don't fail the DICOM operation
            logger.error(f"Failed to publish event for instance {instance.sop_instance_uid}: {e}")

    if not effective_study_uid:
        effective_study_uid = "unknown"

    response_body = build_store_response(effective_study_uid, stored, warnings, failures)

    # Azure v2: 200 if all succeed, 202 if warnings or partial success, 409 if all fail
    if failures and not stored:
        # All instances failed (duplicates, validation errors, exceptions)
        status_code = 409
    elif has_warnings or warnings or failures:
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

    # Parse multipart DICOM data (reuse POST logic)
    body = await request.body()
    parts = parse_multipart_related(body, content_type)

    stored = []
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
                failures.append(
                    {
                        "00081150": {"vr": "UI", "Value": [str(getattr(ds, "SOPClassUID", ""))]},
                        "00081155": {"vr": "UI", "Value": [str(getattr(ds, "SOPInstanceUID", ""))]},
                        "00081197": {"vr": "US", "Value": [FAILURE_REASON_UNABLE_TO_PROCESS]},
                    }
                )
                continue

            # Note: PUT does not validate searchable attributes (Azure v2 behavior)
            # Searchable attribute validation only applies to POST

            sop_uid = str(ds.SOPInstanceUID)
            study_uid = str(ds.StudyInstanceUID)
            series_uid = str(ds.SeriesInstanceUID)

            # If study UID provided in URL, validate match
            if study_instance_uid and study_uid != study_instance_uid:
                failures.append(
                    {
                        "00081150": {"vr": "UI", "Value": [str(ds.SOPClassUID)]},
                        "00081155": {"vr": "UI", "Value": [sop_uid]},
                        "00081197": {"vr": "US", "Value": [FAILURE_REASON_UID_MISMATCH]},
                    }
                )
                continue

            if effective_study_uid is None:
                effective_study_uid = study_uid

            # Extract metadata for upsert service
            dicom_json = dataset_to_dicom_json(ds)
            searchable = extract_searchable_metadata(ds)

            # Prepare data package for upsert service
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

            # Use upsert service for create-or-replace
            action = await upsert_instance(
                db, study_uid, series_uid, sop_uid, dcm_data, str(DICOM_STORAGE_DIR)
            )

            # Add change feed entry
            feed_entry = ChangeFeedEntry(
                study_instance_uid=study_uid,
                series_instance_uid=series_uid,
                sop_instance_uid=sop_uid,
                action=action,  # "created" or "replaced"
                state="current",
            )
            db.add(feed_entry)

            # If previous change feed entry for this SOP exists, mark as replaced
            if action == "replaced":
                await _mark_previous_feed_entries(db, sop_uid)

            stored.append(
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

            # Get instance and track with feed_entry for event publishing
            result = await db.execute(
                select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
            )
            stored_instance = result.scalar_one_or_none()
            if stored_instance:
                instances_to_publish.append((stored_instance, feed_entry))

        except Exception as e:
            failures.append(
                {
                    "00081197": {"vr": "US", "Value": [0xC000]},
                    "00081196": {"vr": "LO", "Value": [str(e)]},
                }
            )
            has_warnings = True

    # Create/update DicomStudy with expiry
    if effective_study_uid and expires_at:
        result = await db.execute(
            select(DicomStudy).where(DicomStudy.study_instance_uid == effective_study_uid)
        )
        study = result.scalar_one_or_none()

        if not study:
            study = DicomStudy(study_instance_uid=effective_study_uid)
            db.add(study)

        # Set expiry
        study.expires_at = expires_at

    await db.commit()

    # Publish DicomImageCreated events (best-effort, after commit)
    for instance, feed_entry in instances_to_publish:
        try:
            from main import get_event_manager

            event_manager = get_event_manager()
            event = DicomEvent.from_instance_created(
                study_uid=instance.study_instance_uid,
                series_uid=instance.series_instance_uid,
                instance_uid=instance.sop_instance_uid,
                sequence_number=feed_entry.sequence,  # Use ChangeFeedEntry sequence (int)
                service_url=str(request.base_url).rstrip("/"),
            )
            await event_manager.publish(event)
        except Exception as e:
            # Log but don't fail the DICOM operation
            logger.error(f"Failed to publish event for instance {instance.sop_instance_uid}: {e}")

    if not effective_study_uid:
        effective_study_uid = "unknown"

    # Build response (no warnings for upsert - Azure v2 behavior)
    response_body = build_store_response(effective_study_uid, stored, [], failures)

    # Azure v2: 200 if all succeed, 202 if warnings, 409 if all fail
    if failures and not stored:
        status_code = 409
    elif has_warnings:
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


@router.get("/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}/frames/{frames}")
async def retrieve_frames(
    study_uid: str,
    series_uid: str,
    instance_uid: str,
    frames: str,
    accept: str = Header(default="application/octet-stream"),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve one or more frames from a DICOM instance.

    Frames parameter:
    - Single frame: "1"
    - Multiple frames: "1,3,5"

    Accept header:
    - application/octet-stream (single frame only)
    - multipart/related; type="application/octet-stream" (multiple frames)
    """
    # Parse frame numbers with validation
    try:
        frame_numbers = [int(f.strip()) for f in frames.split(",")]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid frame number format")

    # Verify instance exists in database
    result = await db.execute(
        select(DicomInstance).where(
            DicomInstance.study_instance_uid == study_uid,
            DicomInstance.series_instance_uid == series_uid,
            DicomInstance.sop_instance_uid == instance_uid,
        )
    )
    db_instance = result.scalar_one_or_none()

    if not db_instance:
        raise HTTPException(status_code=404, detail="Instance not found")

    # Single frame retrieval
    if len(frame_numbers) == 1:
        try:
            frame_path = frame_cache.get_frame(
                study_uid, series_uid, instance_uid, frame_numbers[0]
            )
            frame_data = frame_path.read_bytes()

            return Response(
                content=frame_data,
                media_type="application/octet-stream",
            )
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            if "out of range" in str(e):
                raise HTTPException(status_code=404, detail=str(e))
            elif "no pixel data" in str(e) or "decodable pixel data" in str(e):
                raise HTTPException(status_code=404, detail="Instance does not contain pixel data")
            else:
                raise HTTPException(status_code=500, detail=str(e))

    # Multiple frames - multipart/related response
    else:
        try:
            frame_parts = []
            for frame_num in frame_numbers:
                frame_path = frame_cache.get_frame(study_uid, series_uid, instance_uid, frame_num)
                frame_data = frame_path.read_bytes()
                frame_parts.append(("application/octet-stream", frame_data))

            # Build multipart response using helper
            boundary = f"boundary-{uuid.uuid4().hex[:16]}"
            multipart_content = build_multipart_response(frame_parts, boundary)

            return Response(
                content=multipart_content,
                media_type=f'multipart/related; type="application/octet-stream"; boundary={boundary}',
            )
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            if "out of range" in str(e):
                raise HTTPException(status_code=404, detail=str(e))
            else:
                raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}/rendered",
    response_class=Response,
    summary="Retrieve rendered DICOM instance (WADO-RS)",
)
async def retrieve_rendered_instance(
    study_uid: str,
    series_uid: str,
    instance_uid: str,
    quality: int = Query(default=100, ge=1, le=100),
    accept: str = Header(default="image/jpeg"),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve rendered DICOM instance as JPEG or PNG.

    Query parameters:
    - quality: JPEG quality (1-100, default: 100, ignored for PNG)

    Accept header:
    - image/jpeg (default)
    - image/png
    """
    # Verify instance exists
    result = await db.execute(
        select(DicomInstance).where(
            DicomInstance.study_instance_uid == study_uid,
            DicomInstance.series_instance_uid == series_uid,
            DicomInstance.sop_instance_uid == instance_uid,
        )
    )
    db_instance = result.scalar_one_or_none()

    if not db_instance:
        raise HTTPException(status_code=404, detail="Instance not found")

    # Determine format from Accept header
    if "image/png" in accept.lower():
        format = "png"
        media_type = "image/png"
    else:
        format = "jpeg"
        media_type = "image/jpeg"

    # Build path to DICOM file
    dcm_path = DICOM_STORAGE_DIR / study_uid / series_uid / instance_uid / "instance.dcm"

    if not dcm_path.exists():
        raise HTTPException(status_code=404, detail="Instance file not found")

    try:
        # Render frame 1 (entire instance)
        image_bytes = render_frame_to_image(
            dcm_path, frame_number=1, format=format, quality=quality
        )

        return Response(
            content=image_bytes,
            media_type=media_type,
        )
    except ValueError as e:
        if "no pixel data" in str(e) or "decodable pixel data" in str(e):
            raise HTTPException(status_code=404, detail="Instance does not contain pixel data")
        else:
            raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}/frames/{frame}/rendered",
    response_class=Response,
    summary="Retrieve rendered DICOM frame (WADO-RS)",
)
async def retrieve_rendered_frame(
    study_uid: str,
    series_uid: str,
    instance_uid: str,
    frame: int,
    quality: int = Query(default=100, ge=1, le=100),
    accept: str = Header(default="image/jpeg"),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve rendered DICOM frame as JPEG or PNG.

    Query parameters:
    - quality: JPEG quality (1-100, default: 100, ignored for PNG)

    Accept header:
    - image/jpeg (default)
    - image/png
    """
    # Verify instance exists
    result = await db.execute(
        select(DicomInstance).where(
            DicomInstance.study_instance_uid == study_uid,
            DicomInstance.series_instance_uid == series_uid,
            DicomInstance.sop_instance_uid == instance_uid,
        )
    )
    db_instance = result.scalar_one_or_none()

    if not db_instance:
        raise HTTPException(status_code=404, detail="Instance not found")

    # Determine format from Accept header
    if "image/png" in accept.lower():
        format = "png"
        media_type = "image/png"
    else:
        format = "jpeg"
        media_type = "image/jpeg"

    # Build path to DICOM file
    dcm_path = DICOM_STORAGE_DIR / study_uid / series_uid / instance_uid / "instance.dcm"

    if not dcm_path.exists():
        raise HTTPException(status_code=404, detail="Instance file not found")

    try:
        # Render specific frame
        image_bytes = render_frame_to_image(
            dcm_path, frame_number=frame, format=format, quality=quality
        )

        return Response(
            content=image_bytes,
            media_type=media_type,
        )
    except ValueError as e:
        if "out of range" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        elif "no pixel data" in str(e) or "decodable pixel data" in str(e):
            raise HTTPException(status_code=404, detail="Instance does not contain pixel data")
        elif "JPEG quality" in str(e):
            # Quality validation from Task 4
            raise HTTPException(status_code=400, detail=str(e))
        else:
            raise HTTPException(status_code=500, detail=str(e))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  QIDO-RS — Query/Search
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def filter_dicom_json_by_includefield(
    dicom_json: dict, includefield: str | list[str] | None, level: str = "study"
) -> dict:
    """Filter DICOM JSON based on includefield parameter.

    Args:
        dicom_json: Full DICOM JSON dictionary
        includefield: Comma-separated list of DICOM tags, list of tags, "all", or None
        level: Query level ("study", "series", or "instance")

    Returns:
        Filtered DICOM JSON dictionary

    Behavior:
        - includefield=all: return everything
        - includefield=<tag>: return only requested tags + required return tags
        - includefield=None: return as-is (default set already built)

    Required return tags (always included):
        - StudyInstanceUID (0020000D)
        - SeriesInstanceUID (0020000E)
        - SOPInstanceUID (00080018)
        - SOPClassUID (00080016)
    """
    # Required tags per DICOM PS3.18 Table 6.7.1-2a
    REQUIRED_TAGS = {
        "0020000D",  # StudyInstanceUID
        "0020000E",  # SeriesInstanceUID
        "00080018",  # SOPInstanceUID
        "00080016",  # SOPClassUID
    }

    # If no includefield specified, return as-is (default set)
    if not includefield:
        return dicom_json

    # Handle list of values (multiple query params)
    if isinstance(includefield, list):
        # Check if any value is "all"
        if "all" in includefield:
            return dicom_json
        # Combine all tags from the list (filter out empty strings)
        requested_tags = {
            tag.strip() for value in includefield for tag in value.split(",") if tag.strip()
        }
        # If no valid tags after filtering, return default
        if not requested_tags:
            return dicom_json
    else:
        # If includefield=all, return everything
        if includefield == "all":
            return dicom_json
        # Parse requested tags (comma-separated)
        requested_tags = {tag.strip() for tag in includefield.split(",") if tag.strip()}
        # If empty string or no valid tags, return default
        if not requested_tags:
            return dicom_json

    # Combine requested tags with required tags
    tags_to_include = requested_tags | REQUIRED_TAGS

    # Filter the DICOM JSON to only include requested + required tags
    filtered = {tag: value for tag, value in dicom_json.items() if tag in tags_to_include}

    return filtered


@router.get("/studies")
async def qido_rs_studies(
    request: Request,
    limit: int = Query(100, alias="limit"),
    offset: int = Query(0, alias="offset"),
    fuzzymatching: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """QIDO-RS: Search for studies.

    Query parameters:
    - PatientName: Patient name (supports exact match and wildcard)
    - fuzzymatching: Enable fuzzy prefix matching on person names (default: false)
    - limit: Maximum number of results (default: 100)
    - offset: Offset for pagination (default: 0)

    When fuzzymatching=true, person name queries use prefix word matching:
    - "joh" matches "John^Doe" (starts with "John")
    - "do" matches "Doe^John" (starts with "Doe")
    - "joh do" matches "John^Doe" (matches both components)
    """
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

    # Get includefield parameter (may be multiple values)
    includefield_list = request.query_params.getlist("includefield")
    includefield = includefield_list if includefield_list else None

    response = []
    for study_uid, study_instances in studies.items():
        first = study_instances[0]
        modalities = list({i.modality for i in study_instances if i.modality})
        series_uids = list({i.series_instance_uid for i in study_instances})

        study_json = {
            "0020000D": {"vr": "UI", "Value": [study_uid]},
            "00100020": {"vr": "LO", "Value": [first.patient_id] if first.patient_id else {}},
            "00100010": {
                "vr": "PN",
                "Value": [{"Alphabetic": first.patient_name}] if first.patient_name else {},
            },
            "00080020": {"vr": "DA", "Value": [first.study_date] if first.study_date else {}},
            "00080050": {
                "vr": "SH",
                "Value": [first.accession_number] if first.accession_number else {},
            },
            "00081030": {
                "vr": "LO",
                "Value": [first.study_description] if first.study_description else {},
            },
            "00080061": {"vr": "CS", "Value": modalities},  # ModalitiesInStudy
            "00201206": {"vr": "IS", "Value": [len(series_uids)]},  # NumberOfStudyRelatedSeries
            "00201208": {
                "vr": "IS",
                "Value": [len(study_instances)],
            },  # NumberOfStudyRelatedInstances
        }

        # Apply includefield filtering
        filtered_study_json = filter_dicom_json_by_includefield(study_json, includefield, "study")
        response.append(filtered_study_json)

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
    SeriesInstanceUID: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """QIDO-RS: Search for series within a study.

    Query parameters:
    - SeriesInstanceUID: Series UID (supports comma or backslash separated list)
    - limit: Maximum number of results (default: 100)
    - offset: Offset for pagination (default: 0)
    """
    # Start with study filter
    conditions = [DicomInstance.study_instance_uid == study_uid]

    # Add SeriesInstanceUID filter with list support
    if SeriesInstanceUID:
        if "," in SeriesInstanceUID or "\\" in SeriesInstanceUID:
            # UID list search
            uids = parse_uid_list(SeriesInstanceUID)
            conditions.append(DicomInstance.series_instance_uid.in_(uids))
        else:
            # Single UID - exact match
            conditions.append(DicomInstance.series_instance_uid == SeriesInstanceUID)

    query = select(DicomInstance).where(and_(*conditions)).offset(offset).limit(limit)

    result = await db.execute(query)
    instances = result.scalars().all()

    series: dict[str, list] = {}
    for inst in instances:
        if inst.series_instance_uid not in series:
            series[inst.series_instance_uid] = []
        series[inst.series_instance_uid].append(inst)

    # Get includefield parameter (may be multiple values)
    includefield_list = request.query_params.getlist("includefield")
    includefield = includefield_list if includefield_list else None

    response = []
    for series_uid, series_instances in series.items():
        first = series_instances[0]
        series_json = {
            "0020000D": {"vr": "UI", "Value": [study_uid]},
            "0020000E": {"vr": "UI", "Value": [series_uid]},
            "00080060": {"vr": "CS", "Value": [first.modality] if first.modality else {}},
            "0008103E": {
                "vr": "LO",
                "Value": [first.series_description] if first.series_description else {},
            },
            "00200011": {"vr": "IS", "Value": [first.series_number] if first.series_number else {}},
            "00201209": {
                "vr": "IS",
                "Value": [len(series_instances)],
            },  # NumberOfSeriesRelatedInstances
        }

        # Apply includefield filtering
        filtered_series_json = filter_dicom_json_by_includefield(
            series_json, includefield, "series"
        )
        response.append(filtered_series_json)

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
    SOPInstanceUID: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """QIDO-RS: Search for instances within a series.

    Query parameters:
    - SOPInstanceUID: SOP Instance UID (supports comma or backslash separated list)
    - limit: Maximum number of results (default: 100)
    - offset: Offset for pagination (default: 0)
    """
    # Start with study and series filters
    conditions = [
        DicomInstance.study_instance_uid == study_uid,
        DicomInstance.series_instance_uid == series_uid,
    ]

    # Add SOPInstanceUID filter with list support
    if SOPInstanceUID:
        if "," in SOPInstanceUID or "\\" in SOPInstanceUID:
            # UID list search
            uids = parse_uid_list(SOPInstanceUID)
            conditions.append(DicomInstance.sop_instance_uid.in_(uids))
        else:
            # Single UID - exact match
            conditions.append(DicomInstance.sop_instance_uid == SOPInstanceUID)

    query = select(DicomInstance).where(and_(*conditions)).offset(offset).limit(limit)

    result = await db.execute(query)
    instances = result.scalars().all()

    # Get includefield parameter (may be multiple values) and apply filtering
    includefield_list = request.query_params.getlist("includefield")
    includefield = includefield_list if includefield_list else None
    response = [
        filter_dicom_json_by_includefield(inst.dicom_json, includefield, "instance")
        for inst in instances
    ]

    return Response(
        content=_json_dumps(response),
        media_type="application/dicom+json",
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DELETE — Remove studies/series/instances
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@router.delete("/studies/{study_uid}")
async def delete_study(
    study_uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete an entire study."""
    return await _delete_instances(db, request, study_uid=study_uid)


@router.delete("/studies/{study_uid}/series/{series_uid}")
async def delete_series(
    study_uid: str,
    series_uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete an entire series."""
    return await _delete_instances(db, request, study_uid=study_uid, series_uid=series_uid)


@router.delete("/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}")
async def delete_instance(
    study_uid: str,
    series_uid: str,
    instance_uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single instance."""
    return await _delete_instances(
        db, request, study_uid=study_uid, series_uid=series_uid, instance_uid=instance_uid
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _json_dumps(obj) -> bytes:
    """JSON serialize with support for UUID and datetime."""

    class Encoder(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, uuid.UUID):
                return str(o)
            if isinstance(o, datetime):
                return o.isoformat()
            return super().default(o)

    return json.dumps(obj, cls=Encoder).encode("utf-8")


def parse_date_range(value: str):
    """Parse DICOM date range query value into SQL filter conditions.

    Supports four date formats per DICOM standard:
    - "20260115": exact match
    - "20260110-20260120": range (inclusive on both ends)
    - "-20260120": before or equal
    - "20260110-": after or equal

    Args:
        value: Date query string from QIDO-RS parameter

    Returns:
        Tuple of (filter_type, start_date, end_date) where:
        - filter_type: "exact" | "range" | "before" | "after"
        - start_date: Start date string (None for before queries)
        - end_date: End date string (None for after queries)

    Notes:
        - Does not validate date format (DICOM is lenient)
        - Assumes YYYYMMDD format but accepts any string
        - Lexicographic comparison works correctly for YYYYMMDD
    """
    if "-" not in value:
        # Exact match: "20260115"
        return ("exact", value, None)

    if value.startswith("-"):
        # Before or equal: "-20260120"
        end_date = value[1:]  # Strip leading hyphen
        return ("before", None, end_date)

    if value.endswith("-"):
        # After or equal: "20260110-"
        start_date = value[:-1]  # Strip trailing hyphen
        return ("after", start_date, None)

    # Range: "20260110-20260120"
    parts = value.split("-", 1)  # Split on first hyphen only
    if len(parts) == 2:
        start_date, end_date = parts
        return ("range", start_date, end_date)

    # Fallback: treat as exact match if parsing fails
    return ("exact", value, None)


def _parse_qido_params(params, fuzzymatching: bool = False) -> list:
    """Convert QIDO-RS query parameters to SQLAlchemy filter conditions.

    Supports:
    - UID list matching: comma or backslash separated UIDs (for StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID)
    - Date range matching: DICOM date range syntax (for StudyDate)
    - Wildcard matching: * (zero or more chars), ? (exactly one char)
    - Fuzzy matching: prefix word matching for person names (when fuzzymatching=true)
    - Exact matching: when no wildcards and fuzzymatching=false
    """
    filters = []
    for key, value in params.items():
        if key in ("limit", "offset", "fuzzymatching", "includefield"):
            continue

        db_column = QIDO_TAG_MAP.get(key)
        if db_column:
            column = getattr(DicomInstance, db_column, None)
            if column is not None:
                # UID list matching takes precedence (for UID fields only)
                if db_column in ("study_instance_uid", "series_instance_uid", "sop_instance_uid"):
                    if "," in value or "\\" in value:
                        # UID list search
                        uids = parse_uid_list(value)
                        filters.append(column.in_(uids))
                    else:
                        # Single UID - exact match
                        filters.append(column == value)
                # Date range matching (for date fields)
                elif db_column == "study_date":
                    filter_type, start_date, end_date = parse_date_range(value)

                    if filter_type == "exact":
                        # Exact date match
                        filters.append(column == start_date)
                    elif filter_type == "range":
                        # Date range (inclusive)
                        filters.append(and_(column >= start_date, column <= end_date))
                    elif filter_type == "before":
                        # Before or equal
                        filters.append(column <= end_date)
                    elif filter_type == "after":
                        # After or equal
                        filters.append(column >= start_date)
                # Wildcard matching takes precedence (for non-UID, non-date fields)
                elif "*" in value or "?" in value:
                    # Translate DICOM wildcards to SQL LIKE pattern
                    pattern = translate_wildcards(value)
                    filters.append(column.like(pattern))
                elif fuzzymatching and db_column in ("patient_name", "referring_physician_name"):
                    # Use prefix word matching for person names
                    filters.append(build_fuzzy_name_filter(value, column))
                else:
                    # Exact match
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
        media_type=f'multipart/related; type="application/dicom"; boundary={boundary}',
    )


async def _delete_instances(
    db: AsyncSession,
    request: Request,
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

    # Track instances with feed_entry for event publishing (need data before deletion)
    deleted_instances_data = []

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

        # Track instance data with feed_entry for event publishing
        deleted_instances_data.append(
            (
                {
                    "study_uid": inst.study_instance_uid,
                    "series_uid": inst.series_instance_uid,
                    "instance_uid": inst.sop_instance_uid,
                },
                feed_entry,
            )
        )

        # Mark previous feed entries
        await _mark_previous_feed_entries(db, inst.sop_instance_uid)

        # Delete file from disk
        delete_instance_file(inst.file_path)

        # Delete from DB
        await db.delete(inst)

    await db.commit()

    # Publish DicomImageDeleted events (best-effort, after commit)
    for inst_data, feed_entry in deleted_instances_data:
        try:
            from main import get_event_manager

            event_manager = get_event_manager()
            event = DicomEvent.from_instance_deleted(
                study_uid=inst_data["study_uid"],
                series_uid=inst_data["series_uid"],
                instance_uid=inst_data["instance_uid"],
                sequence_number=feed_entry.sequence,  # Use ChangeFeedEntry sequence (int)
                service_url=str(request.base_url).rstrip("/"),
            )
            await event_manager.publish(event)
        except Exception as e:
            # Log but don't fail the delete operation
            logger.error(
                f"Failed to publish delete event for instance {inst_data['instance_uid']}: {e}"
            )

    return Response(status_code=204)


async def _mark_previous_feed_entries(db: AsyncSession, sop_uid: str):
    """Mark previous change feed entries as replaced/deleted."""
    prev_entries = await db.execute(
        select(ChangeFeedEntry)
        .where(
            and_(
                ChangeFeedEntry.sop_instance_uid == sop_uid,
                ChangeFeedEntry.state == "current",
            )
        )
        .order_by(ChangeFeedEntry.sequence.desc())
        .offset(1)
    )
    for entry in prev_entries.scalars().all():
        entry.state = "replaced"
