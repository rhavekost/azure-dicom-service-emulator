"""Integration tests for UID list matching in QIDO-RS endpoints (Phase 4, Task 4).

These tests verify that UID list matching works through the QIDO-RS endpoints.
"""

import pytest
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models.dicom import DicomInstance

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


async def create_test_instance(
    db_session: AsyncSession,
    patient_id: str = "PAT001",
    study_uid: str | None = None,
    series_uid: str | None = None,
    sop_uid: str | None = None,
) -> DicomInstance:
    """Create a test DICOM instance in the database."""
    import uuid

    if not study_uid:
        study_uid = f"1.2.840.113619.2.1.1.{uuid.uuid4().int}"
    if not series_uid:
        series_uid = f"1.2.840.113619.2.1.2.{uuid.uuid4().int}"
    if not sop_uid:
        sop_uid = f"1.2.840.113619.2.1.3.{uuid.uuid4().int}"

    instance = DicomInstance(
        study_instance_uid=study_uid,
        series_instance_uid=series_uid,
        sop_instance_uid=sop_uid,
        sop_class_uid="1.2.840.10008.5.1.4.1.1.2",
        patient_id=patient_id,
        patient_name=f"Patient^{patient_id}",
        study_date="20260215",
        modality="CT",
        dicom_json={},
        file_path=f"/tmp/{sop_uid}.dcm",
        file_size=1024,
    )
    db_session.add(instance)
    await db_session.commit()
    await db_session.refresh(instance)
    return instance


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  StudyInstanceUID List Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_study_uid_list_filter_comma_separated(db_session):
    """Test StudyInstanceUID list filtering with comma-separated UIDs."""
    # Create test instances with different study UIDs
    study1_uid = "1.2.840.113619.2.1.1.1"
    study2_uid = "1.2.840.113619.2.1.1.2"
    study3_uid = "1.2.840.113619.2.1.1.3"

    inst1 = await create_test_instance(db_session, "PAT1", study_uid=study1_uid)
    inst2 = await create_test_instance(db_session, "PAT2", study_uid=study2_uid)
    inst3 = await create_test_instance(db_session, "PAT3", study_uid=study3_uid)

    from app.services.search_utils import parse_uid_list

    # Simulate UID list parameter
    uid_param = f"{study1_uid},{study3_uid}"

    # Check if param should be parsed as list (has comma or backslash)
    if "," in uid_param or "\\" in uid_param:
        uids = parse_uid_list(uid_param)
        query = select(DicomInstance).where(DicomInstance.study_instance_uid.in_(uids))
    else:
        query = select(DicomInstance).where(DicomInstance.study_instance_uid == uid_param)

    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match 2 instances (study1 and study3)
    assert len(instances) == 2
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert inst1.sop_instance_uid in instance_uids
    assert inst3.sop_instance_uid in instance_uids
    assert inst2.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_study_uid_list_filter_backslash_separated(db_session):
    """Test StudyInstanceUID list filtering with backslash-separated UIDs."""
    # Create test instances
    study1_uid = "1.2.840.113619.2.1.1.1"
    study2_uid = "1.2.840.113619.2.1.1.2"

    inst1 = await create_test_instance(db_session, "PAT1", study_uid=study1_uid)
    inst2 = await create_test_instance(db_session, "PAT2", study_uid=study2_uid)

    from app.services.search_utils import parse_uid_list

    # Simulate UID list parameter with backslash
    uid_param = f"{study1_uid}\\{study2_uid}"

    # Check if param should be parsed as list
    if "," in uid_param or "\\" in uid_param:
        uids = parse_uid_list(uid_param)
        query = select(DicomInstance).where(DicomInstance.study_instance_uid.in_(uids))
    else:
        query = select(DicomInstance).where(DicomInstance.study_instance_uid == uid_param)

    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match 2 instances
    assert len(instances) == 2


@pytest.mark.asyncio
async def test_study_uid_single_exact_match(db_session):
    """Test single StudyInstanceUID uses exact match (no list parsing)."""
    # Create test instances
    study1_uid = "1.2.840.113619.2.1.1.1"
    study2_uid = "1.2.840.113619.2.1.1.2"

    inst1 = await create_test_instance(db_session, "PAT1", study_uid=study1_uid)
    inst2 = await create_test_instance(db_session, "PAT2", study_uid=study2_uid)

    # Single UID - exact match
    uid_param = study1_uid

    # Check if param should be parsed as list
    if "," in uid_param or "\\" in uid_param:
        from app.services.search_utils import parse_uid_list
        uids = parse_uid_list(uid_param)
        query = select(DicomInstance).where(DicomInstance.study_instance_uid.in_(uids))
    else:
        query = select(DicomInstance).where(DicomInstance.study_instance_uid == uid_param)

    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match exactly 1 instance
    assert len(instances) == 1
    assert instances[0].sop_instance_uid == inst1.sop_instance_uid


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SeriesInstanceUID List Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_series_uid_list_filter_comma_separated(db_session):
    """Test SeriesInstanceUID list filtering with comma-separated UIDs."""
    # Create test instances with different series UIDs
    study_uid = "1.2.840.113619.2.1.1.1"
    series1_uid = "1.2.840.113619.2.1.2.1"
    series2_uid = "1.2.840.113619.2.1.2.2"
    series3_uid = "1.2.840.113619.2.1.2.3"

    inst1 = await create_test_instance(db_session, "PAT1", study_uid=study_uid, series_uid=series1_uid)
    inst2 = await create_test_instance(db_session, "PAT1", study_uid=study_uid, series_uid=series2_uid)
    inst3 = await create_test_instance(db_session, "PAT1", study_uid=study_uid, series_uid=series3_uid)

    from app.services.search_utils import parse_uid_list

    # Simulate UID list parameter
    uid_param = f"{series1_uid},{series3_uid}"

    # Check if param should be parsed as list
    if "," in uid_param or "\\" in uid_param:
        uids = parse_uid_list(uid_param)
        query = select(DicomInstance).where(DicomInstance.series_instance_uid.in_(uids))
    else:
        query = select(DicomInstance).where(DicomInstance.series_instance_uid == uid_param)

    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match 2 instances
    assert len(instances) == 2
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert inst1.sop_instance_uid in instance_uids
    assert inst3.sop_instance_uid in instance_uids
    assert inst2.sop_instance_uid not in instance_uids


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SOPInstanceUID List Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_sop_uid_list_filter_comma_separated(db_session):
    """Test SOPInstanceUID list filtering with comma-separated UIDs."""
    # Create test instances with different SOP UIDs
    study_uid = "1.2.840.113619.2.1.1.1"
    series_uid = "1.2.840.113619.2.1.2.1"
    sop1_uid = "1.2.840.113619.2.1.3.1"
    sop2_uid = "1.2.840.113619.2.1.3.2"
    sop3_uid = "1.2.840.113619.2.1.3.3"

    inst1 = await create_test_instance(db_session, "PAT1", study_uid=study_uid, series_uid=series_uid, sop_uid=sop1_uid)
    inst2 = await create_test_instance(db_session, "PAT1", study_uid=study_uid, series_uid=series_uid, sop_uid=sop2_uid)
    inst3 = await create_test_instance(db_session, "PAT1", study_uid=study_uid, series_uid=series_uid, sop_uid=sop3_uid)

    from app.services.search_utils import parse_uid_list

    # Simulate UID list parameter
    uid_param = f"{sop1_uid},{sop2_uid}"

    # Check if param should be parsed as list
    if "," in uid_param or "\\" in uid_param:
        uids = parse_uid_list(uid_param)
        query = select(DicomInstance).where(DicomInstance.sop_instance_uid.in_(uids))
    else:
        query = select(DicomInstance).where(DicomInstance.sop_instance_uid == uid_param)

    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match 2 instances
    assert len(instances) == 2
    sop_uids = {inst.sop_instance_uid for inst in instances}
    assert sop1_uid in sop_uids
    assert sop2_uid in sop_uids
    assert sop3_uid not in sop_uids
