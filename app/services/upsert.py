"""
Upsert service for PUT STOW-RS operations (Phase 3, Task 2).

Implements create-or-replace logic for DICOM instances.
"""

import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dicom import DicomInstance


async def upsert_instance(
    db: AsyncSession,
    study_uid: str,
    series_uid: str,
    sop_uid: str,
    dcm_data: dict[str, Any],
    storage_dir: str
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
    db: AsyncSession,
    study_uid: str,
    series_uid: str,
    sop_uid: str,
    storage_dir: str
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

    # Delete file from filesystem
    file_path = Path(instance.file_path)
    if file_path.exists():
        file_path.unlink()

        # Clean up parent directory if empty (instance level)
        instance_dir = file_path.parent
        if instance_dir.exists() and not any(instance_dir.iterdir()):
            instance_dir.rmdir()

            # Clean up series directory if empty
            series_dir = instance_dir.parent
            if series_dir.exists() and not any(series_dir.iterdir()):
                series_dir.rmdir()

                # Clean up study directory if empty
                study_dir = series_dir.parent
                if study_dir.exists() and not any(study_dir.iterdir()):
                    study_dir.rmdir()

    # Delete from database
    stmt = sql_delete(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    await db.execute(stmt)
    await db.commit()


async def store_instance(
    db: AsyncSession,
    study_uid: str,
    series_uid: str,
    sop_uid: str,
    dcm_data: dict[str, Any],
    storage_dir: str
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
    instance_dir.mkdir(parents=True, exist_ok=True)

    # Write DICOM file
    file_name = f"{sop_uid}.dcm"
    file_path = instance_dir / file_name
    file_path.write_bytes(dcm_data["file_data"])

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
