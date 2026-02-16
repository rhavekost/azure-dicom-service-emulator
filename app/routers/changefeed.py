"""Azure Change Feed API router."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.dicom import ChangeFeedEntry

router = APIRouter()


@router.get("/changefeed")
async def get_change_feed(
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    start_time: Optional[str] = Query(None, alias="startTime"),
    end_time: Optional[str] = Query(None, alias="endTime"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get change feed entries with optional time-window filtering.

    Azure DICOM Service v2 compatible change feed.
    """
    query = select(ChangeFeedEntry)

    # Apply time filters if provided
    if start_time:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        query = query.where(ChangeFeedEntry.timestamp >= start_dt)

    if end_time:
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        query = query.where(ChangeFeedEntry.timestamp <= end_dt)

    # Order by sequence and apply pagination
    query = query.order_by(ChangeFeedEntry.sequence).offset(offset).limit(limit)

    result = await db.execute(query)
    entries = result.scalars().all()

    # Format response to match Azure API
    return [
        {
            "Sequence": entry.sequence,
            "StudyInstanceUID": entry.study_instance_uid,
            "SeriesInstanceUID": entry.series_instance_uid,
            "SOPInstanceUID": entry.sop_instance_uid,
            "Action": entry.action,
            "Timestamp": entry.timestamp.isoformat(),
            "State": entry.state,
            "Metadata": entry.dicom_metadata,
        }
        for entry in entries
    ]


@router.get("/changefeed/latest")
async def get_latest_change_feed(
    db: AsyncSession = Depends(get_db),
):
    """Get the latest change feed entry."""
    query = select(ChangeFeedEntry).order_by(ChangeFeedEntry.sequence.desc()).limit(1)
    result = await db.execute(query)
    entry = result.scalar_one_or_none()

    if not entry:
        return {"Sequence": 0}

    return {
        "Sequence": entry.sequence,
        "StudyInstanceUID": entry.study_instance_uid,
        "SeriesInstanceUID": entry.series_instance_uid,
        "SOPInstanceUID": entry.sop_instance_uid,
        "Action": entry.action,
        "Timestamp": entry.timestamp.isoformat(),
        "State": entry.state,
        "Metadata": entry.dicom_metadata,
    }
