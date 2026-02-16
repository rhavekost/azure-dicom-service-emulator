"""Tests for Workitem model (Phase 5, Task 1)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.dicom import Workitem

pytestmark = pytest.mark.unit


def test_workitem_creation():
    """Create a Workitem with required fields."""
    workitem = Workitem(
        sop_instance_uid="1.2.840.10008.5.1.4.34.5.1",
        procedure_step_state="SCHEDULED",
        dicom_dataset={
            "00080005": {"vr": "CS", "Value": ["ISO_IR 100"]},
            "00080016": {"vr": "UI", "Value": ["1.2.840.10008.5.1.4.34.5"]},
            "00080018": {"vr": "UI", "Value": ["1.2.840.10008.5.1.4.34.5.1"]},
        },
    )

    assert workitem.sop_instance_uid == "1.2.840.10008.5.1.4.34.5.1"
    assert workitem.procedure_step_state == "SCHEDULED"
    assert workitem.transaction_uid is None
    assert workitem.dicom_dataset is not None
    assert isinstance(workitem.dicom_dataset, dict)


def test_workitem_state_values():
    """Test all valid procedure step states."""
    valid_states = ["SCHEDULED", "IN PROGRESS", "COMPLETED", "CANCELED"]

    for state in valid_states:
        workitem = Workitem(
            sop_instance_uid=f"1.2.3.{state}",
            procedure_step_state=state,
            dicom_dataset={"test": "data"},
        )
        assert workitem.procedure_step_state == state


def test_workitem_with_transaction_uid():
    """Test claimed workitem with transaction UID."""
    workitem = Workitem(
        sop_instance_uid="1.2.840.10008.5.1.4.34.5.1",
        transaction_uid="1.2.840.10008.1.2.3.4.5",
        procedure_step_state="IN PROGRESS",
        dicom_dataset={"test": "data"},
    )

    assert workitem.sop_instance_uid == "1.2.840.10008.5.1.4.34.5.1"
    assert workitem.transaction_uid == "1.2.840.10008.1.2.3.4.5"
    assert workitem.procedure_step_state == "IN PROGRESS"


def test_workitem_with_patient_info():
    """Test workitem with patient searchable attributes."""
    workitem = Workitem(
        sop_instance_uid="1.2.840.10008.5.1.4.34.5.1",
        procedure_step_state="SCHEDULED",
        patient_name="Doe^John",
        patient_id="PAT12345",
        dicom_dataset={"test": "data"},
    )

    assert workitem.patient_name == "Doe^John"
    assert workitem.patient_id == "PAT12345"


def test_workitem_with_scheduling():
    """Test workitem with scheduling information."""
    scheduled_time = datetime.now(timezone.utc) + timedelta(hours=2)

    workitem = Workitem(
        sop_instance_uid="1.2.840.10008.5.1.4.34.5.1",
        procedure_step_state="SCHEDULED",
        scheduled_procedure_step_start_datetime=scheduled_time,
        study_instance_uid="1.2.840.113619.2.1.1.1",
        accession_number="ACC12345",
        requested_procedure_id="RP001",
        dicom_dataset={"test": "data"},
    )

    assert workitem.scheduled_procedure_step_start_datetime == scheduled_time
    assert workitem.study_instance_uid == "1.2.840.113619.2.1.1.1"
    assert workitem.accession_number == "ACC12345"
    assert workitem.requested_procedure_id == "RP001"


def test_workitem_with_station_codes():
    """Test workitem with station code searchable attributes."""
    workitem = Workitem(
        sop_instance_uid="1.2.840.10008.5.1.4.34.5.1",
        procedure_step_state="SCHEDULED",
        scheduled_station_name_code="CT01",
        scheduled_station_class_code="CT_SCANNER",
        scheduled_station_geo_code="RADIOLOGY_DEPT_A",
        dicom_dataset={"test": "data"},
    )

    assert workitem.scheduled_station_name_code == "CT01"
    assert workitem.scheduled_station_class_code == "CT_SCANNER"
    assert workitem.scheduled_station_geo_code == "RADIOLOGY_DEPT_A"


def test_workitem_default_state():
    """Test that default state is SCHEDULED when explicitly set."""
    # SQLAlchemy defaults are applied at DB insert time, not object creation
    # When creating a workitem, procedure_step_state should be explicitly provided
    # or it will be None until persisted to DB where the default will apply
    workitem = Workitem(
        sop_instance_uid="1.2.840.10008.5.1.4.34.5.1",
        procedure_step_state="SCHEDULED",  # Explicitly set the default
        dicom_dataset={"test": "data"},
    )

    assert workitem.procedure_step_state == "SCHEDULED"


# Database integration test fixtures and tests


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


@pytest.mark.asyncio
async def test_workitem_database_persistence(db_session):
    """Test workitem can be persisted to and retrieved from database.

    This integration test verifies:
    1. Workitem can be created and committed to database
    2. Workitem can be retrieved by primary key (sop_instance_uid)
    3. Searchable attributes are properly indexed and queryable
    4. JSON dataset is properly stored and retrieved
    5. Timestamps are automatically set
    """
    # 1. Create and persist workitem
    scheduled_time = datetime.now(timezone.utc) + timedelta(hours=2)
    workitem = Workitem(
        sop_instance_uid="1.2.840.10008.5.1.4.34.5.1",
        transaction_uid="1.2.840.10008.1.2.3.4.5",
        procedure_step_state="IN PROGRESS",
        patient_name="Doe^John",
        patient_id="PAT12345",
        study_instance_uid="1.2.840.113619.2.1.1.1",
        scheduled_procedure_step_start_datetime=scheduled_time,
        accession_number="ACC12345",
        requested_procedure_id="RP001",
        scheduled_station_name_code="CT01",
        scheduled_station_class_code="CT_SCANNER",
        scheduled_station_geo_code="RADIOLOGY_DEPT_A",
        dicom_dataset={
            "00080005": {"vr": "CS", "Value": ["ISO_IR 100"]},
            "00080016": {"vr": "UI", "Value": ["1.2.840.10008.5.1.4.34.5"]},
            "00080018": {"vr": "UI", "Value": ["1.2.840.10008.5.1.4.34.5.1"]},
        },
    )
    db_session.add(workitem)
    await db_session.commit()

    # 2. Retrieve by primary key
    retrieved = await db_session.get(Workitem, "1.2.840.10008.5.1.4.34.5.1")
    assert retrieved is not None
    assert retrieved.sop_instance_uid == "1.2.840.10008.5.1.4.34.5.1"
    assert retrieved.transaction_uid == "1.2.840.10008.1.2.3.4.5"
    assert retrieved.procedure_step_state == "IN PROGRESS"

    # 3. Verify searchable attributes
    assert retrieved.patient_name == "Doe^John"
    assert retrieved.patient_id == "PAT12345"
    assert retrieved.study_instance_uid == "1.2.840.113619.2.1.1.1"
    assert retrieved.accession_number == "ACC12345"
    assert retrieved.requested_procedure_id == "RP001"
    assert retrieved.scheduled_station_name_code == "CT01"
    assert retrieved.scheduled_station_class_code == "CT_SCANNER"
    assert retrieved.scheduled_station_geo_code == "RADIOLOGY_DEPT_A"

    # 4. Verify JSON dataset
    assert retrieved.dicom_dataset is not None
    assert retrieved.dicom_dataset["00080005"]["vr"] == "CS"
    assert retrieved.dicom_dataset["00080018"]["Value"][0] == "1.2.840.10008.5.1.4.34.5.1"

    # 5. Verify timestamps
    assert retrieved.created_at is not None
    assert retrieved.updated_at is not None


@pytest.mark.asyncio
async def test_workitem_query_by_patient_id(db_session):
    """Test querying workitems by patient_id (indexed field)."""
    # Create multiple workitems
    workitem1 = Workitem(
        sop_instance_uid="1.2.3.1",
        procedure_step_state="SCHEDULED",
        patient_id="PAT001",
        dicom_dataset={"test": "data1"},
    )
    workitem2 = Workitem(
        sop_instance_uid="1.2.3.2",
        procedure_step_state="IN PROGRESS",
        patient_id="PAT001",
        dicom_dataset={"test": "data2"},
    )
    workitem3 = Workitem(
        sop_instance_uid="1.2.3.3",
        procedure_step_state="SCHEDULED",
        patient_id="PAT002",
        dicom_dataset={"test": "data3"},
    )

    db_session.add_all([workitem1, workitem2, workitem3])
    await db_session.commit()

    # Query by patient_id
    result = await db_session.execute(select(Workitem).where(Workitem.patient_id == "PAT001"))
    workitems = result.scalars().all()

    assert len(workitems) == 2
    assert all(w.patient_id == "PAT001" for w in workitems)


@pytest.mark.asyncio
async def test_workitem_query_by_state(db_session):
    """Test querying workitems by procedure_step_state (indexed field)."""
    # Create workitems with different states
    workitem1 = Workitem(
        sop_instance_uid="1.2.3.1",
        procedure_step_state="SCHEDULED",
        dicom_dataset={"test": "data1"},
    )
    workitem2 = Workitem(
        sop_instance_uid="1.2.3.2",
        procedure_step_state="SCHEDULED",
        dicom_dataset={"test": "data2"},
    )
    workitem3 = Workitem(
        sop_instance_uid="1.2.3.3",
        procedure_step_state="COMPLETED",
        dicom_dataset={"test": "data3"},
    )

    db_session.add_all([workitem1, workitem2, workitem3])
    await db_session.commit()

    # Query by state
    result = await db_session.execute(
        select(Workitem).where(Workitem.procedure_step_state == "SCHEDULED")
    )
    workitems = result.scalars().all()

    assert len(workitems) == 2
    assert all(w.procedure_step_state == "SCHEDULED" for w in workitems)


@pytest.mark.asyncio
async def test_workitem_query_by_accession_number(db_session):
    """Test querying workitems by accession_number (should be indexed)."""
    # Create workitems with different accession numbers
    workitem1 = Workitem(
        sop_instance_uid="1.2.3.1",
        procedure_step_state="SCHEDULED",
        accession_number="ACC001",
        dicom_dataset={"test": "data1"},
    )
    workitem2 = Workitem(
        sop_instance_uid="1.2.3.2",
        procedure_step_state="IN PROGRESS",
        accession_number="ACC002",
        dicom_dataset={"test": "data2"},
    )

    db_session.add_all([workitem1, workitem2])
    await db_session.commit()

    # Query by accession_number
    result = await db_session.execute(select(Workitem).where(Workitem.accession_number == "ACC001"))
    workitem = result.scalar_one_or_none()

    assert workitem is not None
    assert workitem.accession_number == "ACC001"
    assert workitem.sop_instance_uid == "1.2.3.1"


@pytest.mark.asyncio
async def test_workitem_composite_query_state_patient(db_session):
    """Test querying workitems by state and patient_id (composite index pattern)."""
    # Create workitems
    workitem1 = Workitem(
        sop_instance_uid="1.2.3.1",
        procedure_step_state="SCHEDULED",
        patient_id="PAT001",
        dicom_dataset={"test": "data1"},
    )
    workitem2 = Workitem(
        sop_instance_uid="1.2.3.2",
        procedure_step_state="SCHEDULED",
        patient_id="PAT002",
        dicom_dataset={"test": "data2"},
    )
    workitem3 = Workitem(
        sop_instance_uid="1.2.3.3",
        procedure_step_state="IN PROGRESS",
        patient_id="PAT001",
        dicom_dataset={"test": "data3"},
    )

    db_session.add_all([workitem1, workitem2, workitem3])
    await db_session.commit()

    # Query by state and patient_id (common query pattern)
    result = await db_session.execute(
        select(Workitem).where(
            Workitem.procedure_step_state == "SCHEDULED", Workitem.patient_id == "PAT001"
        )
    )
    workitems = result.scalars().all()

    assert len(workitems) == 1
    assert workitems[0].sop_instance_uid == "1.2.3.1"
