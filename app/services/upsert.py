"""
Upsert service for PUT STOW-RS operations (Phase 3, Task 2).

Implements create-or-replace logic for DICOM instances.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dicom import DicomInstance


def _try_unlink(path: Path) -> None:
    """Unlink a file, ignoring FileNotFoundError (EAFP)."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _try_rmdir(path: Path) -> None:
    """Remove a directory if empty, ignoring OSError (EAFP, atomic check)."""
    try:
        path.rmdir()  # atomic: raises OSError if non-empty or missing
    except OSError:
        pass


async def upsert_instance(
    db: AsyncSession,
    study_uid: str,
    series_uid: str,
    sop_uid: str,
    dcm_data: dict[str, Any],
    storage_dir: str,
) -> str:
    """
    Create or replace a DICOM instance.

    Args:
        db: Database session
        study_uid: Study Instance UID
        series_uid: Series Instance UID
        sop_uid: SOP Instance UID
        dcm_data: Dictionary containing:
            - file_data: Raw DICOM bytes
            - dicom_json: DICOM JSON metadata
            - metadata: Extracted searchable metadata
        storage_dir: Base directory for DICOM file storage

    Returns:
        "created" if instance was newly created
        "replaced" if instance was replaced
    """
    # Check if instance already exists
    stmt = select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    result = await db.execute(stmt)
    existing_instance = result.scalar_one_or_none()

    if existing_instance:
        # Delete old instance
        await delete_instance(db, study_uid, series_uid, sop_uid, storage_dir)
        action = "replaced"
    else:
        action = "created"

    # Store new instance
    await store_instance(db, study_uid, series_uid, sop_uid, dcm_data, storage_dir)

    return action


async def delete_instance(
    db: AsyncSession, study_uid: str, series_uid: str, sop_uid: str, storage_dir: str
) -> None:
    """
    Delete a DICOM instance from database and filesystem.

    Args:
        db: Database session
        study_uid: Study Instance UID
        series_uid: Series Instance UID
        sop_uid: SOP Instance UID
        storage_dir: Base directory for DICOM file storage
    """
    # Get instance to find file path
    stmt = select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    result = await db.execute(stmt)
    instance = result.scalar_one_or_none()

    if not instance:
        return

    # Delete file from filesystem (EAFP — avoids TOCTOU race)
    file_path = Path(instance.file_path)
    await asyncio.to_thread(_try_unlink, file_path)

    # Clean up empty ancestor directories (instance → series → study)
    instance_dir = file_path.parent
    await asyncio.to_thread(_try_rmdir, instance_dir)

    series_dir = instance_dir.parent
    await asyncio.to_thread(_try_rmdir, series_dir)

    study_dir = series_dir.parent
    await asyncio.to_thread(_try_rmdir, study_dir)

    # Delete from database
    delete_stmt = sql_delete(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    await db.execute(delete_stmt)
    await db.commit()


async def store_instance(
    db: AsyncSession,
    study_uid: str,
    series_uid: str,
    sop_uid: str,
    dcm_data: dict[str, Any],
    storage_dir: str,
) -> None:
    """
    Store a DICOM instance in database and filesystem.

    Args:
        db: Database session
        study_uid: Study Instance UID
        series_uid: Series Instance UID
        sop_uid: SOP Instance UID
        dcm_data: Dictionary containing:
            - file_data: Raw DICOM bytes
            - dicom_json: DICOM JSON metadata
            - metadata: Extracted searchable metadata
        storage_dir: Base directory for DICOM file storage

    Note:
        This is a simplified placeholder implementation.
        Full integration with existing STOW-RS logic will happen in Task 3.
    """
    # Create directory structure: storage_dir/study/series/instance/
    instance_dir = Path(storage_dir) / study_uid / series_uid / sop_uid
    await asyncio.to_thread(instance_dir.mkdir, parents=True, exist_ok=True)

    # Write DICOM file
    file_name = "instance.dcm"  # Consistent with dicom_engine and frame_cache
    file_path = instance_dir / file_name
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(dcm_data["file_data"])

    # Extract metadata
    metadata = dcm_data.get("metadata", {})

    # Create database entry
    instance = DicomInstance(
        id=uuid.uuid4(),
        study_instance_uid=study_uid,
        series_instance_uid=series_uid,
        sop_instance_uid=sop_uid,
        sop_class_uid=metadata.get("sop_class_uid"),
        transfer_syntax_uid=metadata.get("transfer_syntax_uid"),
        patient_id=metadata.get("patient_id"),
        patient_name=metadata.get("patient_name"),
        study_date=metadata.get("study_date"),
        study_time=metadata.get("study_time"),
        accession_number=metadata.get("accession_number"),
        study_description=metadata.get("study_description"),
        modality=metadata.get("modality"),
        series_description=metadata.get("series_description"),
        series_number=metadata.get("series_number"),
        instance_number=metadata.get("instance_number"),
        referring_physician_name=metadata.get("referring_physician_name"),
        dicom_json=dcm_data["dicom_json"],
        file_path=str(file_path),
        file_size=len(dcm_data["file_data"]),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    db.add(instance)
    await db.commit()
