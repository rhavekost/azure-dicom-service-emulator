"""Integration tests for PUT STOW-RS endpoint (Phase 3)."""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
import pydicom
from pydicom.dataset import Dataset
from io import BytesIO

from app.models.dicom import DicomInstance, DicomStudy
from app.services.upsert import upsert_instance
from app.services.dicom_engine import (
    dataset_to_dicom_json,
    extract_searchable_metadata,
)

pytestmark = pytest.mark.integration


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


def create_synthetic_dicom(
    study_uid: str = "1.2.3",
    series_uid: str = "1.2.3.4",
    sop_uid: str = "1.2.3.4.5",
    patient_id: str = "PAT001",
) -> tuple[bytes, Dataset]:
    """Create a minimal synthetic DICOM file for testing."""
    ds = Dataset()

    # File meta information
    file_meta = Dataset()
    file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT Image Storage
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    ds.file_meta = file_meta

    # Required DICOM attributes
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"

    # Patient module
    ds.PatientName = "Test^Patient"
    ds.PatientID = patient_id

    # Study module
    ds.StudyDate = "20240101"
    ds.StudyTime = "120000"
    ds.AccessionNumber = "ACC001"

    # Series module
    ds.Modality = "CT"
    ds.SeriesNumber = 1

    # Instance module
    ds.InstanceNumber = 1

    # Save to bytes
    bio = BytesIO()
    ds.save_as(bio, write_like_original=False)
    return bio.getvalue(), ds


@pytest.mark.asyncio
async def test_put_creates_new_instance(db_session, storage_dir):
    """Test PUT creates a new instance when it doesn't exist."""
    study_uid = "1.2.999.1"
    series_uid = "1.2.999.1.1"
    sop_uid = "1.2.999.1.1.1"

    # Create synthetic DICOM
    dicom_bytes, ds = create_synthetic_dicom(study_uid, series_uid, sop_uid, "PUT_TEST_001")

    # Extract metadata
    dicom_json = dataset_to_dicom_json(ds)
    searchable = extract_searchable_metadata(ds)

    # Prepare data package
    dcm_data = {
        "file_data": dicom_bytes,
        "dicom_json": dicom_json,
        "metadata": {
            "sop_class_uid": str(ds.SOPClassUID),
            "transfer_syntax_uid": str(ds.file_meta.TransferSyntaxUID),
            **searchable,
        }
    }

    # Upsert instance
    action = await upsert_instance(
        db_session, study_uid, series_uid, sop_uid, dcm_data, storage_dir
    )

    assert action == "created"

    # Verify instance exists in database
    result = await db_session.execute(
        select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    )
    instance = result.scalar_one_or_none()

    assert instance is not None
    assert instance.study_instance_uid == study_uid
    assert instance.series_instance_uid == series_uid
    assert instance.sop_instance_uid == sop_uid
    assert instance.patient_id == "PUT_TEST_001"

    # Verify file exists
    file_path = Path(instance.file_path)
    assert file_path.exists()


@pytest.mark.asyncio
async def test_put_replaces_existing_instance(db_session, storage_dir):
    """Test PUT replaces an existing instance (upsert behavior)."""
    study_uid = "1.2.999.2"
    series_uid = "1.2.999.2.1"
    sop_uid = "1.2.999.2.1.1"

    # Create initial instance
    dicom_bytes_v1, ds_v1 = create_synthetic_dicom(study_uid, series_uid, sop_uid, "PUT_TEST_002_V1")
    dicom_json_v1 = dataset_to_dicom_json(ds_v1)
    searchable_v1 = extract_searchable_metadata(ds_v1)

    dcm_data_v1 = {
        "file_data": dicom_bytes_v1,
        "dicom_json": dicom_json_v1,
        "metadata": {
            "sop_class_uid": str(ds_v1.SOPClassUID),
            "transfer_syntax_uid": str(ds_v1.file_meta.TransferSyntaxUID),
            **searchable_v1,
        }
    }

    action1 = await upsert_instance(
        db_session, study_uid, series_uid, sop_uid, dcm_data_v1, storage_dir
    )
    assert action1 == "created"

    # Verify initial instance
    result = await db_session.execute(
        select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    )
    instance_v1 = result.scalar_one()
    assert instance_v1.patient_id == "PUT_TEST_002_V1"
    initial_file_path = instance_v1.file_path

    # Update the instance (same UIDs, different patient ID)
    dicom_bytes_v2, ds_v2 = create_synthetic_dicom(study_uid, series_uid, sop_uid, "PUT_TEST_002_V2")
    dicom_json_v2 = dataset_to_dicom_json(ds_v2)
    searchable_v2 = extract_searchable_metadata(ds_v2)

    dcm_data_v2 = {
        "file_data": dicom_bytes_v2,
        "dicom_json": dicom_json_v2,
        "metadata": {
            "sop_class_uid": str(ds_v2.SOPClassUID),
            "transfer_syntax_uid": str(ds_v2.file_meta.TransferSyntaxUID),
            **searchable_v2,
        }
    }

    action2 = await upsert_instance(
        db_session, study_uid, series_uid, sop_uid, dcm_data_v2, storage_dir
    )
    assert action2 == "replaced"

    # Verify instance was replaced
    result = await db_session.execute(
        select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    )
    instance_v2 = result.scalar_one()

    # Patient ID should be updated
    assert instance_v2.patient_id == "PUT_TEST_002_V2"

    # Should only have one instance (not two)
    result_count = await db_session.execute(
        select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    )
    all_instances = result_count.scalars().all()
    assert len(all_instances) == 1


@pytest.mark.asyncio
async def test_put_with_expiry_creates_study(db_session, storage_dir):
    """Test that DicomStudy can be created with expires_at."""
    study_uid = "1.2.999.3"
    series_uid = "1.2.999.3.1"
    sop_uid = "1.2.999.3.1.1"

    # Create synthetic DICOM
    dicom_bytes, ds = create_synthetic_dicom(study_uid, series_uid, sop_uid, "PUT_TEST_003")
    dicom_json = dataset_to_dicom_json(ds)
    searchable = extract_searchable_metadata(ds)

    dcm_data = {
        "file_data": dicom_bytes,
        "dicom_json": dicom_json,
        "metadata": {
            "sop_class_uid": str(ds.SOPClassUID),
            "transfer_syntax_uid": str(ds.file_meta.TransferSyntaxUID),
            **searchable,
        }
    }

    # Upsert instance
    await upsert_instance(db_session, study_uid, series_uid, sop_uid, dcm_data, storage_dir)

    # Create DicomStudy with expiry (simulating what PUT endpoint does)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    result = await db_session.execute(
        select(DicomStudy).where(DicomStudy.study_instance_uid == study_uid)
    )
    study = result.scalar_one_or_none()

    if not study:
        study = DicomStudy(study_instance_uid=study_uid)
        db_session.add(study)

    study.expires_at = expires_at
    await db_session.commit()

    # Verify DicomStudy was created with expires_at
    result = await db_session.execute(
        select(DicomStudy).where(DicomStudy.study_instance_uid == study_uid)
    )
    study = result.scalar_one()

    assert study is not None
    assert study.expires_at is not None

    # Verify expires_at is approximately 1 hour from now
    time_diff = abs((study.expires_at - expires_at).total_seconds())
    assert time_diff < 1  # Within 1 second


@pytest.mark.asyncio
async def test_put_updates_existing_study_expiry(db_session, storage_dir):
    """Test that PUT updates expires_at on existing study."""
    study_uid = "1.2.999.4"
    series_uid = "1.2.999.4.1"
    sop_uid = "1.2.999.4.1.1"

    # Create study with initial expiry
    initial_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    study = DicomStudy(
        study_instance_uid=study_uid,
        expires_at=initial_expiry,
    )
    db_session.add(study)
    await db_session.commit()

    # Create instance
    dicom_bytes, ds = create_synthetic_dicom(study_uid, series_uid, sop_uid, "PUT_TEST_004")
    dicom_json = dataset_to_dicom_json(ds)
    searchable = extract_searchable_metadata(ds)

    dcm_data = {
        "file_data": dicom_bytes,
        "dicom_json": dicom_json,
        "metadata": {
            "sop_class_uid": str(ds.SOPClassUID),
            "transfer_syntax_uid": str(ds.file_meta.TransferSyntaxUID),
            **searchable,
        }
    }

    await upsert_instance(db_session, study_uid, series_uid, sop_uid, dcm_data, storage_dir)

    # Update expiry (simulating what PUT endpoint does with new expiry headers)
    new_expiry = datetime.now(timezone.utc) + timedelta(hours=2)

    result = await db_session.execute(
        select(DicomStudy).where(DicomStudy.study_instance_uid == study_uid)
    )
    study = result.scalar_one()
    study.expires_at = new_expiry
    await db_session.commit()

    # Verify study expiry was updated
    result = await db_session.execute(
        select(DicomStudy).where(DicomStudy.study_instance_uid == study_uid)
    )
    study = result.scalar_one()

    # Expiry should be updated to ~2 hours from now
    time_diff = abs((study.expires_at - new_expiry).total_seconds())
    assert time_diff < 1

    # Should NOT be the initial expiry
    assert abs((study.expires_at - initial_expiry).total_seconds()) > 3500


@pytest.mark.asyncio
async def test_upsert_no_duplicate_instances(db_session, storage_dir):
    """Test that upsert doesn't create duplicate instances."""
    study_uid = "1.2.999.5"
    series_uid = "1.2.999.5.1"
    sop_uid = "1.2.999.5.1.1"

    # Create instance twice with same UIDs
    for i in range(2):
        dicom_bytes, ds = create_synthetic_dicom(
            study_uid, series_uid, sop_uid, f"PUT_TEST_005_V{i+1}"
        )
        dicom_json = dataset_to_dicom_json(ds)
        searchable = extract_searchable_metadata(ds)

        dcm_data = {
            "file_data": dicom_bytes,
            "dicom_json": dicom_json,
            "metadata": {
                "sop_class_uid": str(ds.SOPClassUID),
                "transfer_syntax_uid": str(ds.file_meta.TransferSyntaxUID),
                **searchable,
            }
        }

        await upsert_instance(db_session, study_uid, series_uid, sop_uid, dcm_data, storage_dir)

    # Verify only one instance exists
    result = await db_session.execute(
        select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    )
    all_instances = result.scalars().all()
    assert len(all_instances) == 1

    # Should have the latest patient ID
    assert all_instances[0].patient_id == "PUT_TEST_005_V2"
