"""Tests for upsert service (Phase 3, Task 2)."""

import pytest
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models.dicom import DicomInstance
from app.services.upsert import upsert_instance, delete_instance, store_instance


# Test database setup
@pytest.fixture
async def db_session(tmp_path):
    """Create a temporary test database."""
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    # Create tables
    from app.database import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create session
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session

    # Cleanup
    await engine.dispose()


@pytest.fixture
def storage_dir(tmp_path):
    """Create a temporary storage directory."""
    storage = tmp_path / "dicom_storage"
    storage.mkdir()
    return str(storage)


@pytest.fixture
def sample_dicom_data():
    """Sample DICOM data for testing."""
    return {
        "dicom_json": {
            "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Test^Patient"}]},
            "00100020": {"vr": "LO", "Value": ["PAT001"]},
            "00080060": {"vr": "CS", "Value": ["CT"]},
        },
        "file_data": b"MOCK_DICOM_DATA",
        "metadata": {
            "patient_id": "PAT001",
            "patient_name": "Test^Patient",
            "modality": "CT",
            "sop_class_uid": "1.2.840.10008.5.1.4.1.1.2",
        }
    }


@pytest.mark.asyncio
async def test_upsert_creates_new_instance(db_session, storage_dir, sample_dicom_data):
    """Test upsert_instance creates a new instance when it doesn't exist."""
    study_uid = "1.2.3"
    series_uid = "1.2.3.4"
    sop_uid = "1.2.3.4.5"

    result = await upsert_instance(
        db_session,
        study_uid,
        series_uid,
        sop_uid,
        sample_dicom_data,
        storage_dir
    )

    assert result == "created"

    # Verify instance was stored in database
    stmt = select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    result = await db_session.execute(stmt)
    instance = result.scalar_one_or_none()

    assert instance is not None
    assert instance.study_instance_uid == study_uid
    assert instance.series_instance_uid == series_uid
    assert instance.sop_instance_uid == sop_uid
    assert instance.patient_id == "PAT001"
    assert instance.modality == "CT"

    # Verify file was written
    file_path = Path(instance.file_path)
    assert file_path.exists()
    assert file_path.read_bytes() == sample_dicom_data["file_data"]


@pytest.mark.asyncio
async def test_upsert_replaces_existing_instance(db_session, storage_dir, sample_dicom_data):
    """Test upsert_instance replaces an existing instance."""
    study_uid = "1.2.3"
    series_uid = "1.2.3.4"
    sop_uid = "1.2.3.4.5"

    # Create initial instance
    result1 = await upsert_instance(
        db_session,
        study_uid,
        series_uid,
        sop_uid,
        sample_dicom_data,
        storage_dir
    )
    assert result1 == "created"

    # Get the original file path
    stmt = select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    result = await db_session.execute(stmt)
    original_instance = result.scalar_one()
    original_file_path = Path(original_instance.file_path)

    assert original_file_path.exists()

    # Create updated data
    updated_data = {
        "dicom_json": {
            "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Updated^Patient"}]},
            "00100020": {"vr": "LO", "Value": ["PAT002"]},
            "00080060": {"vr": "CS", "Value": ["MR"]},
        },
        "file_data": b"UPDATED_DICOM_DATA",
        "metadata": {
            "patient_id": "PAT002",
            "patient_name": "Updated^Patient",
            "modality": "MR",
            "sop_class_uid": "1.2.840.10008.5.1.4.1.1.4",
        }
    }

    # Replace the instance
    result2 = await upsert_instance(
        db_session,
        study_uid,
        series_uid,
        sop_uid,
        updated_data,
        storage_dir
    )
    assert result2 == "replaced"

    # Verify instance was updated in database
    stmt = select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    result = await db_session.execute(stmt)
    updated_instance = result.scalar_one()

    assert updated_instance.patient_id == "PAT002"
    assert updated_instance.modality == "MR"

    # Verify new file was written
    new_file_path = Path(updated_instance.file_path)
    assert new_file_path.exists()
    assert new_file_path.read_bytes() == updated_data["file_data"]

    # Verify old file path is same as new (same UIDs = same path)
    # The new data should have overwritten the old file
    assert new_file_path == original_file_path


@pytest.mark.asyncio
async def test_delete_instance(db_session, storage_dir, sample_dicom_data):
    """Test delete_instance removes instance from DB and filesystem."""
    study_uid = "1.2.3"
    series_uid = "1.2.3.4"
    sop_uid = "1.2.3.4.5"

    # Create instance first
    await upsert_instance(
        db_session,
        study_uid,
        series_uid,
        sop_uid,
        sample_dicom_data,
        storage_dir
    )

    # Get file path before deletion
    stmt = select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    result = await db_session.execute(stmt)
    instance = result.scalar_one()
    file_path = Path(instance.file_path)

    assert file_path.exists()

    # Delete the instance
    await delete_instance(db_session, study_uid, series_uid, sop_uid, storage_dir)

    # Verify instance was removed from database
    stmt = select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    result = await db_session.execute(stmt)
    instance = result.scalar_one_or_none()

    assert instance is None

    # Verify file was deleted
    assert not file_path.exists()


@pytest.mark.asyncio
async def test_store_instance(db_session, storage_dir, sample_dicom_data):
    """Test store_instance creates DB entry and writes file."""
    study_uid = "1.2.3"
    series_uid = "1.2.3.4"
    sop_uid = "1.2.3.4.5"

    await store_instance(
        db_session,
        study_uid,
        series_uid,
        sop_uid,
        sample_dicom_data,
        storage_dir
    )

    # Verify instance was stored in database
    stmt = select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    result = await db_session.execute(stmt)
    instance = result.scalar_one()

    assert instance.study_instance_uid == study_uid
    assert instance.series_instance_uid == series_uid
    assert instance.sop_instance_uid == sop_uid

    # Verify file was written
    file_path = Path(instance.file_path)
    assert file_path.exists()
    assert file_path.read_bytes() == sample_dicom_data["file_data"]
