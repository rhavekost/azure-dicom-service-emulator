"""Tests for expiry service (Phase 3, Task 4)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.dicom import DicomInstance, DicomStudy
from app.services.expiry import delete_expired_studies, delete_study, get_expired_studies

pytestmark = pytest.mark.unit


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


@pytest.mark.asyncio
async def test_get_expired_studies(db_session):
    """Test get_expired_studies returns only expired studies."""
    now = datetime.now(timezone.utc)

    # Create expired study
    expired_study = DicomStudy(
        study_instance_uid="1.2.3.expired",
        expires_at=now - timedelta(hours=1),
    )
    db_session.add(expired_study)

    # Create study expiring in the future
    future_study = DicomStudy(
        study_instance_uid="1.2.3.future",
        expires_at=now + timedelta(hours=1),
    )
    db_session.add(future_study)

    # Create study with no expiry
    no_expiry_study = DicomStudy(
        study_instance_uid="1.2.3.noexpiry",
        expires_at=None,
    )
    db_session.add(no_expiry_study)

    await db_session.commit()

    # Get expired studies
    expired = await get_expired_studies(db_session)

    # Should only return the expired study
    assert len(expired) == 1
    assert expired[0].study_instance_uid == "1.2.3.expired"


@pytest.mark.asyncio
async def test_delete_expired_studies(db_session, storage_dir):
    """Test delete_expired_studies removes DB entries and files."""
    now = datetime.now(timezone.utc)

    # Create expired study
    expired_study_uid = "1.2.3.expired"
    expired_study = DicomStudy(
        study_instance_uid=expired_study_uid,
        expires_at=now - timedelta(hours=1),
    )
    db_session.add(expired_study)

    # Create instances for expired study
    series_uid = "1.2.3.4.series"
    instance1_uid = "1.2.3.4.5.instance1"
    instance2_uid = "1.2.3.4.5.instance2"

    # Create study directory with files
    study_dir = Path(storage_dir) / expired_study_uid
    study_dir.mkdir(parents=True)
    series_dir = study_dir / series_uid
    series_dir.mkdir()

    instance1_path = series_dir / f"{instance1_uid}.dcm"
    instance2_path = series_dir / f"{instance2_uid}.dcm"
    instance1_path.write_bytes(b"DICOM_DATA_1")
    instance2_path.write_bytes(b"DICOM_DATA_2")

    instance1 = DicomInstance(
        study_instance_uid=expired_study_uid,
        series_instance_uid=series_uid,
        sop_instance_uid=instance1_uid,
        sop_class_uid="1.2.840.10008.5.1.4.1.1.2",
        dicom_json={},
        file_path=str(instance1_path),
    )
    instance2 = DicomInstance(
        study_instance_uid=expired_study_uid,
        series_instance_uid=series_uid,
        sop_instance_uid=instance2_uid,
        sop_class_uid="1.2.840.10008.5.1.4.1.1.2",
        dicom_json={},
        file_path=str(instance2_path),
    )
    db_session.add(instance1)
    db_session.add(instance2)

    # Create non-expired study
    active_study_uid = "1.2.3.active"
    active_study = DicomStudy(
        study_instance_uid=active_study_uid,
        expires_at=now + timedelta(hours=1),
    )
    db_session.add(active_study)

    await db_session.commit()

    # Verify files exist before deletion
    assert instance1_path.exists()
    assert instance2_path.exists()
    assert study_dir.exists()

    # Delete expired studies
    count = await delete_expired_studies(db_session, storage_dir)

    # Should delete 1 study
    assert count == 1

    # Verify study was removed from database
    stmt = select(DicomStudy).where(DicomStudy.study_instance_uid == expired_study_uid)
    result = await db_session.execute(stmt)
    assert result.scalar_one_or_none() is None

    # Verify instances were removed from database
    stmt = select(DicomInstance).where(DicomInstance.study_instance_uid == expired_study_uid)
    result = await db_session.execute(stmt)
    instances = result.scalars().all()
    assert len(instances) == 0

    # Verify study directory was deleted
    assert not study_dir.exists()
    assert not instance1_path.exists()
    assert not instance2_path.exists()

    # Verify active study still exists
    stmt = select(DicomStudy).where(DicomStudy.study_instance_uid == active_study_uid)
    result = await db_session.execute(stmt)
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_delete_study(db_session, storage_dir):
    """Test delete_study removes all instances, series, and study directory."""
    study_uid = "1.2.3.study"
    series1_uid = "1.2.3.4.series1"
    series2_uid = "1.2.3.4.series2"

    # Create study
    study = DicomStudy(
        study_instance_uid=study_uid,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add(study)

    # Create study directory structure
    study_dir = Path(storage_dir) / study_uid
    study_dir.mkdir(parents=True)
    series1_dir = study_dir / series1_uid
    series1_dir.mkdir()
    series2_dir = study_dir / series2_uid
    series2_dir.mkdir()

    # Create instances in series 1
    instance1_path = series1_dir / "1.2.3.4.5.1.dcm"
    instance2_path = series1_dir / "1.2.3.4.5.2.dcm"
    instance1_path.write_bytes(b"DICOM_1")
    instance2_path.write_bytes(b"DICOM_2")

    instance1 = DicomInstance(
        study_instance_uid=study_uid,
        series_instance_uid=series1_uid,
        sop_instance_uid="1.2.3.4.5.1",
        sop_class_uid="1.2.840.10008.5.1.4.1.1.2",
        dicom_json={},
        file_path=str(instance1_path),
    )
    instance2 = DicomInstance(
        study_instance_uid=study_uid,
        series_instance_uid=series1_uid,
        sop_instance_uid="1.2.3.4.5.2",
        sop_class_uid="1.2.840.10008.5.1.4.1.1.2",
        dicom_json={},
        file_path=str(instance2_path),
    )
    db_session.add(instance1)
    db_session.add(instance2)

    # Create instances in series 2
    instance3_path = series2_dir / "1.2.3.4.5.3.dcm"
    instance3_path.write_bytes(b"DICOM_3")

    instance3 = DicomInstance(
        study_instance_uid=study_uid,
        series_instance_uid=series2_uid,
        sop_instance_uid="1.2.3.4.5.3",
        sop_class_uid="1.2.840.10008.5.1.4.1.1.2",
        dicom_json={},
        file_path=str(instance3_path),
    )
    db_session.add(instance3)

    await db_session.commit()

    # Verify everything exists
    assert study_dir.exists()
    assert series1_dir.exists()
    assert series2_dir.exists()
    assert instance1_path.exists()
    assert instance2_path.exists()
    assert instance3_path.exists()

    # Delete the study
    await delete_study(db_session, study_uid, storage_dir)

    # Verify all instances were deleted from DB
    stmt = select(DicomInstance).where(DicomInstance.study_instance_uid == study_uid)
    result = await db_session.execute(stmt)
    instances = result.scalars().all()
    assert len(instances) == 0

    # Verify study was deleted from DB
    stmt = select(DicomStudy).where(DicomStudy.study_instance_uid == study_uid)
    result = await db_session.execute(stmt)
    assert result.scalar_one_or_none() is None

    # Verify entire study directory was removed
    assert not study_dir.exists()
    assert not series1_dir.exists()
    assert not series2_dir.exists()
    assert not instance1_path.exists()
    assert not instance2_path.exists()
    assert not instance3_path.exists()


@pytest.mark.asyncio
async def test_delete_expired_studies_no_expired(db_session, storage_dir):
    """Test delete_expired_studies returns 0 when no studies are expired."""
    now = datetime.now(timezone.utc)

    # Create only non-expired studies
    study1 = DicomStudy(
        study_instance_uid="1.2.3.future",
        expires_at=now + timedelta(hours=1),
    )
    study2 = DicomStudy(
        study_instance_uid="1.2.3.noexpiry",
        expires_at=None,
    )
    db_session.add(study1)
    db_session.add(study2)

    await db_session.commit()

    # Delete expired studies
    count = await delete_expired_studies(db_session, storage_dir)

    # Should delete 0 studies
    assert count == 0

    # Verify both studies still exist
    stmt = select(DicomStudy)
    result = await db_session.execute(stmt)
    studies = result.scalars().all()
    assert len(studies) == 2


@pytest.mark.asyncio
async def test_delete_study_missing_directory(db_session, storage_dir):
    """Test delete_study handles missing study directory gracefully."""
    study_uid = "1.2.3.nostorage"

    # Create study in DB but no files on disk
    study = DicomStudy(
        study_instance_uid=study_uid,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add(study)

    # Create instance in DB but no file on disk
    instance = DicomInstance(
        study_instance_uid=study_uid,
        series_instance_uid="1.2.3.4",
        sop_instance_uid="1.2.3.4.5",
        sop_class_uid="1.2.840.10008.5.1.4.1.1.2",
        dicom_json={},
        file_path=f"{storage_dir}/{study_uid}/1.2.3.4/1.2.3.4.5.dcm",
    )
    db_session.add(instance)

    await db_session.commit()

    # Delete should not raise an error even if directory doesn't exist
    await delete_study(db_session, study_uid, storage_dir)

    # Verify study and instance were still deleted from DB
    stmt = select(DicomStudy).where(DicomStudy.study_instance_uid == study_uid)
    result = await db_session.execute(stmt)
    assert result.scalar_one_or_none() is None

    stmt = select(DicomInstance).where(DicomInstance.study_instance_uid == study_uid)
    result = await db_session.execute(stmt)
    instances = result.scalars().all()
    assert len(instances) == 0
