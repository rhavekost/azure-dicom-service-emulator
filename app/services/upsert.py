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

    The session is left uncommitted so the caller can include additional
    writes (e.g. ChangeFeedEntry) in the same atomic transaction.

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

    Note:
        Caller is responsible for committing the session.
    """
    # Check if instance already exists
    stmt = select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    result = await db.execute(stmt)
    existing_instance = result.scalar_one_or_none()

    old_file_path: Path | None = None
    if existing_instance:
        # Capture old file path before the DB row is removed
        old_file_path = Path(existing_instance.file_path)
        # Delete old DB row only (no filesystem touch yet)
        await delete_instance(db, study_uid, series_uid, sop_uid, storage_dir)
        action = "replaced"
    else:
        action = "created"

    # Write new file + DB row.  If this raises, the caller's session rollback
    # will undo the delete above, and the old file on disk is untouched.
    await store_instance(db, study_uid, series_uid, sop_uid, dcm_data, storage_dir)

    # Only remove the old file after the new one has been written successfully.
    # At this point the new DCM bytes are on disk; losing the old file is safe.
    # When UIDs are identical the path is the same and store_instance has
    # already overwritten the file in-place — no unlink or rmdir needed.
    if old_file_path is not None:
        new_file_path = Path(storage_dir) / study_uid / series_uid / sop_uid / "instance.dcm"
        if old_file_path != new_file_path:
            instance_dir = old_file_path.parent
            await asyncio.to_thread(_try_unlink, old_file_path)
            # Clean up empty ancestor directories (instance → series → study)
            series_dir = instance_dir.parent
            study_dir = series_dir.parent
            await asyncio.to_thread(_try_rmdir, instance_dir)
            await asyncio.to_thread(_try_rmdir, series_dir)
            await asyncio.to_thread(_try_rmdir, study_dir)

    return action


async def delete_instance(
    db: AsyncSession, study_uid: str, series_uid: str, sop_uid: str, storage_dir: str
) -> None:
    """
    Delete a DICOM instance from the database only.

    Filesystem cleanup is intentionally omitted here so that the file is
    never removed before the surrounding DB transaction is committed.
    Callers that need to reclaim disk space (e.g. upsert_instance) must
    perform the filesystem unlink themselves *after* the write succeeds.

    Args:
        db: Database session
        study_uid: Study Instance UID
        series_uid: Series Instance UID
        sop_uid: SOP Instance UID
        storage_dir: Base directory for DICOM file storage

    Note:
        Caller is responsible for committing the session.
    """
    # Delete from database
    delete_stmt = sql_delete(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    await db.execute(delete_stmt)


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
        Caller is responsible for committing the session.
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
