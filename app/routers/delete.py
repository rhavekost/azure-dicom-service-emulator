"""
DELETE Router — Remove DICOM studies, series, and instances.

Endpoints:
- DELETE /v2/studies/{study_uid}
- DELETE /v2/studies/{study_uid}/series/{series_uid}
- DELETE /v2/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.dicom import ChangeFeedEntry, DicomInstance
from app.models.events import DicomEvent
from app.routers._shared import _mark_previous_feed_entries, _publish_change_event
from app.services.dicom_engine import delete_instance_file

logger = logging.getLogger(__name__)

router = APIRouter()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DELETE Endpoints
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
#  Internal helper
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


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
        await delete_instance_file(inst.file_path)

        # Delete from DB
        await db.delete(inst)

    try:
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise

    # Publish DicomImageDeleted events (best-effort, after commit)
    service_url = str(request.base_url).rstrip("/")
    for inst_data, feed_entry in deleted_instances_data:
        event = DicomEvent.from_instance_deleted(
            study_uid=inst_data["study_uid"],
            series_uid=inst_data["series_uid"],
            instance_uid=inst_data["instance_uid"],
            sequence_number=feed_entry.sequence,
            service_url=service_url,
        )
        await _publish_change_event(event, inst_data["instance_uid"])

    return Response(status_code=204)
