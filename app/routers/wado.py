"""
WADO-RS Router — Retrieve DICOM instances and metadata.

Endpoints:
- GET /v2/studies/{study_uid}
- GET /v2/studies/{study_uid}/metadata
- GET /v2/studies/{study_uid}/series/{series_uid}
- GET /v2/studies/{study_uid}/series/{series_uid}/metadata
- GET /v2/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}
- GET /v2/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}/metadata
- GET /v2/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}/frames/{frames}
- GET /v2/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}/rendered
- GET /v2/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}/frames/{frame}/rendered
"""

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.dicom import DicomInstance
from app.routers._shared import (
    DICOM_STORAGE_DIR,
    _compute_etag,
    _json_dumps,
    frame_cache,
)
from app.services.accept_validation import (
    validate_accept_for_rendered,
    validate_accept_for_retrieve,
)
from app.services.dicom_engine import read_instance
from app.services.image_rendering import render_frame as render_frame_to_image
from app.services.multipart import build_multipart_response

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Shared OpenAPI response documentation for WADO-RS retrieve endpoints ──
_WADO_RETRIEVE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Multipart DICOM data for matching instances",
        "content": {
            'multipart/related; type="application/dicom"': {
                "schema": {"type": "string", "format": "binary"}
            }
        },
    }
}

_WADO_METADATA_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Array of DICOM JSON metadata objects",
        "content": {
            "application/dicom+json": {"schema": {"type": "array", "items": {"type": "object"}}}
        },
    },
    304: {"description": "Not Modified (ETag matched)"},
}

_WADO_FRAMES_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Raw frame data or multipart frame data",
        "content": {
            "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            'multipart/related; type="application/octet-stream"': {
                "schema": {"type": "string", "format": "binary"}
            },
        },
    }
}

_WADO_RENDERED_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Rendered image (JPEG or PNG)",
        "content": {
            "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
            "image/png": {"schema": {"type": "string", "format": "binary"}},
        },
    }
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  WADO-RS — Retrieve Instances & Metadata
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@router.get(
    "/studies/{study_uid}",
    response_class=Response,
    responses={**_WADO_RETRIEVE_RESPONSES, **_WADO_METADATA_RESPONSES},
)
async def wado_rs_study(
    study_uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve all instances in a study as multipart/related."""
    accept = request.headers.get("accept", "")

    if "application/dicom+json" in accept or "metadata" in str(request.url):
        # if_none_match not passed — ETag is returned but If-None-Match caching
        # is not honoured on Accept-based retrieve paths (only on /metadata endpoints)
        return await _retrieve_metadata(db, study_uid=study_uid)

    # Validate Accept header for retrieve
    if not validate_accept_for_retrieve(accept):
        raise HTTPException(status_code=406, detail="Not Acceptable")

    return await _retrieve_instances(db, study_uid=study_uid)


@router.get(
    "/studies/{study_uid}/metadata",
    response_class=Response,
    responses=_WADO_METADATA_RESPONSES,
)
async def wado_rs_study_metadata(
    study_uid: str,
    db: AsyncSession = Depends(get_db),
    if_none_match: Optional[str] = Header(default=None, alias="If-None-Match"),
):
    """Retrieve metadata for all instances in a study."""
    return await _retrieve_metadata(db, study_uid=study_uid, if_none_match=if_none_match)


@router.get(
    "/studies/{study_uid}/series/{series_uid}",
    response_class=Response,
    responses={**_WADO_RETRIEVE_RESPONSES, **_WADO_METADATA_RESPONSES},
)
async def wado_rs_series(
    study_uid: str,
    series_uid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve all instances in a series."""
    accept = request.headers.get("accept", "")
    if "application/dicom+json" in accept:
        # if_none_match not passed — ETag is returned but If-None-Match caching
        # is not honoured on Accept-based retrieve paths (only on /metadata endpoints)
        return await _retrieve_metadata(db, study_uid=study_uid, series_uid=series_uid)

    # Validate Accept header for retrieve
    if not validate_accept_for_retrieve(accept):
        raise HTTPException(status_code=406, detail="Not Acceptable")

    return await _retrieve_instances(db, study_uid=study_uid, series_uid=series_uid)


@router.get(
    "/studies/{study_uid}/series/{series_uid}/metadata",
    response_class=Response,
    responses=_WADO_METADATA_RESPONSES,
)
async def wado_rs_series_metadata(
    study_uid: str,
    series_uid: str,
    db: AsyncSession = Depends(get_db),
    if_none_match: Optional[str] = Header(default=None, alias="If-None-Match"),
):
    """Retrieve metadata for all instances in a series."""
    return await _retrieve_metadata(
        db, study_uid=study_uid, series_uid=series_uid, if_none_match=if_none_match
    )


@router.get(
    "/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}",
    response_class=Response,
    responses={**_WADO_RETRIEVE_RESPONSES, **_WADO_METADATA_RESPONSES},
)
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
        # if_none_match not passed — ETag is returned but If-None-Match caching
        # is not honoured on Accept-based retrieve paths (only on /metadata endpoints)
        return await _retrieve_metadata(
            db, study_uid=study_uid, series_uid=series_uid, instance_uid=instance_uid
        )

    # Validate Accept header for retrieve
    if not validate_accept_for_retrieve(accept):
        raise HTTPException(status_code=406, detail="Not Acceptable")

    return await _retrieve_instances(
        db, study_uid=study_uid, series_uid=series_uid, instance_uid=instance_uid
    )


@router.get(
    "/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}/metadata",
    response_class=Response,
    responses=_WADO_METADATA_RESPONSES,
)
async def wado_rs_instance_metadata(
    study_uid: str,
    series_uid: str,
    instance_uid: str,
    db: AsyncSession = Depends(get_db),
    if_none_match: Optional[str] = Header(default=None, alias="If-None-Match"),
):
    """Retrieve metadata for a single instance."""
    return await _retrieve_metadata(
        db,
        study_uid=study_uid,
        series_uid=series_uid,
        instance_uid=instance_uid,
        if_none_match=if_none_match,
    )


@router.get(
    "/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}/frames/{frames}",
    response_class=Response,
    responses=_WADO_FRAMES_RESPONSES,
)
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
    responses=_WADO_RENDERED_RESPONSES,
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
    # Validate Accept header for rendered
    if not validate_accept_for_rendered(accept):
        raise HTTPException(status_code=406, detail="Not Acceptable")

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
    responses=_WADO_RENDERED_RESPONSES,
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
    # Validate Accept header for rendered
    if not validate_accept_for_rendered(accept):
        raise HTTPException(status_code=406, detail="Not Acceptable")

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
#  Internal helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _retrieve_metadata(
    db: AsyncSession,
    study_uid: str | None = None,
    series_uid: str | None = None,
    instance_uid: str | None = None,
    if_none_match: str | None = None,
) -> Response:
    """Retrieve DICOM JSON metadata for matching instances.

    Returns an ETag header computed as a SHA-256 hash of the serialised
    response body. Honours the If-None-Match request header per RFC 7232:
    - exact ETag match → 304 Not Modified
    - wildcard (*) → 304 Not Modified (resource exists)
    - no match → 200 with full body
    """
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
    content = _json_dumps(metadata)
    etag = _compute_etag(content)

    # RFC 7232 § 3.2 — evaluate If-None-Match before sending the body.
    # The header may be a comma-separated list of ETags; parse each token.
    if if_none_match is not None:
        tokens = [t.strip() for t in if_none_match.split(",")]
        if "*" in tokens or etag in tokens:
            return Response(status_code=304, headers={"ETag": etag})

    return Response(
        content=content,
        media_type="application/dicom+json",
        headers={"ETag": etag},
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
        data = await read_instance(inst.file_path)
        parts.append(("application/dicom", data))

    body = build_multipart_response(parts, boundary)

    return Response(
        content=body,
        media_type=f'multipart/related; type="application/dicom"; boundary={boundary}',
    )
