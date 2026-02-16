"""
Expiry service for automatic study deletion (Phase 3, Task 4).

Handles querying and deleting expired studies from database and filesystem.
"""

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dicom import DicomInstance, DicomStudy

logger = logging.getLogger(__name__)


async def get_expired_studies(db: AsyncSession) -> list[DicomStudy]:
    """
    Query studies that have expired.

    Returns studies where expires_at is not null and expires_at <= current time.

    Args:
        db: Database session

    Returns:
        List of expired DicomStudy objects
    """
    now = datetime.now(timezone.utc)

    stmt = select(DicomStudy).where(
        DicomStudy.expires_at.is_not(None), DicomStudy.expires_at <= now
    )

    result = await db.execute(stmt)
    expired_studies = result.scalars().all()

    logger.info(f"Found {len(expired_studies)} expired studies")
    return list(expired_studies)


async def delete_expired_studies(db: AsyncSession, storage_dir: str) -> int:
    """
    Delete all expired studies from database and filesystem.

    Args:
        db: Database session
        storage_dir: Base directory for DICOM file storage

    Returns:
        Count of studies deleted
    """
    expired_studies = await get_expired_studies(db)

    count = 0
    for study in expired_studies:
        try:
            await delete_study(db, study.study_instance_uid, storage_dir)
            count += 1
            logger.info(f"Deleted expired study: {study.study_instance_uid}")
        except Exception as e:
            logger.error(f"Failed to delete expired study {study.study_instance_uid}: {e}")

    logger.info(f"Deleted {count} expired studies")
    return count


async def delete_study(db: AsyncSession, study_uid: str, storage_dir: str) -> None:
    """
    Delete a study from database and filesystem.

    Deletes all instances, series, and the study directory.

    Args:
        db: Database session
        study_uid: Study Instance UID
        storage_dir: Base directory for DICOM file storage
    """
    # Delete all instances for this study
    stmt = sql_delete(DicomInstance).where(DicomInstance.study_instance_uid == study_uid)
    await db.execute(stmt)

    # Delete the study
    stmt = sql_delete(DicomStudy).where(DicomStudy.study_instance_uid == study_uid)
    await db.execute(stmt)

    await db.commit()

    # Delete study directory from filesystem
    study_dir = Path(storage_dir) / study_uid
    if study_dir.exists():
        try:
            shutil.rmtree(study_dir)
            logger.info(f"Deleted study directory: {study_dir}")
        except Exception as e:
            logger.error(f"Failed to delete study directory {study_dir}: {e}")
            raise
    else:
        logger.warning(f"Study directory not found: {study_dir}")
