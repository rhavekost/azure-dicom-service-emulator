"""Smoke tests for fuzzy matching in QIDO-RS (Phase 4, Task 2).

These tests verify the integration between _parse_qido_params() and
build_fuzzy_name_filter() without requiring full end-to-end API testing.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.datastructures import QueryParams

from app.models.dicom import DicomInstance
from app.routers.dicomweb import _parse_qido_params

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
    db_session: AsyncSession, patient_name: str, study_uid: str
) -> DicomInstance:
    """Create a test DICOM instance in the database."""
    instance = DicomInstance(
        study_instance_uid=study_uid,
        series_instance_uid=f"{study_uid}.1",
        sop_instance_uid=f"{study_uid}.1.1",
        sop_class_uid="1.2.840.10008.5.1.4.1.1.2",
        patient_name=patient_name,
        patient_id=f"PAT-{study_uid[:8]}",
        study_date="20260215",
        modality="CT",
        dicom_json={},
        file_path=f"/tmp/{study_uid}.dcm",
        file_size=1024,
    )
    db_session.add(instance)
    await db_session.commit()
    await db_session.refresh(instance)
    return instance


@pytest.mark.asyncio
async def test_parse_qido_params_with_fuzzy_matching(db_session):
    """Test _parse_qido_params() integrates with build_fuzzy_name_filter().

    This test verifies:
    - _parse_qido_params() correctly calls build_fuzzy_name_filter()
    - Fuzzy matching works when fuzzymatching=true
    - Filters are correctly applied to database queries
    """
    # Create test instances
    john_doe = await create_test_instance(db_session, "John^Doe", "1.2.3.1")
    jane_smith = await create_test_instance(db_session, "Jane^Smith", "1.2.3.2")
    doe_john = await create_test_instance(db_session, "Doe^John", "1.2.3.3")

    # Build filters with fuzzy matching enabled
    params = QueryParams({"PatientName": "joh", "fuzzymatching": "true"})
    filters = _parse_qido_params(params, fuzzymatching=True)

    # Apply filters to query
    from sqlalchemy import and_

    query = select(DicomInstance).where(and_(*filters)) if filters else select(DicomInstance)
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify fuzzy matching worked
    assert len(instances) == 2
    instance_uids = {inst.study_instance_uid for inst in instances}
    assert "1.2.3.1" in instance_uids  # John^Doe
    assert "1.2.3.3" in instance_uids  # Doe^John
    assert "1.2.3.2" not in instance_uids  # Jane^Smith


@pytest.mark.asyncio
async def test_parse_qido_params_without_fuzzy_matching(db_session):
    """Test _parse_qido_params() uses exact match when fuzzymatching=false.

    This test verifies:
    - Default behavior (fuzzymatching=false) uses exact matching
    - Partial matches are NOT returned
    """
    # Create test instances
    john_doe = await create_test_instance(db_session, "John^Doe", "1.2.3.1")
    jane_smith = await create_test_instance(db_session, "Jane^Smith", "1.2.3.2")

    # Build filters with fuzzy matching disabled
    params = QueryParams({"PatientName": "joh"})
    filters = _parse_qido_params(params, fuzzymatching=False)

    # Apply filters to query
    from sqlalchemy import and_

    query = select(DicomInstance).where(and_(*filters)) if filters else select(DicomInstance)
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should not match with exact search
    assert len(instances) == 0


@pytest.mark.asyncio
async def test_parse_qido_params_wildcard_takes_precedence(db_session):
    """Test wildcard matching takes precedence over fuzzy matching.

    This test verifies:
    - Wildcard (*) matching is used when present
    - Fuzzy matching is ignored when wildcards are present
    """
    # Create test instances
    john_doe = await create_test_instance(db_session, "John^Doe", "1.2.3.1")
    jane_smith = await create_test_instance(db_session, "Jane^Smith", "1.2.3.2")

    # Build filters with wildcard (should match both J* names)
    params = QueryParams({"PatientName": "J*", "fuzzymatching": "true"})
    filters = _parse_qido_params(params, fuzzymatching=True)

    # Apply filters to query
    from sqlalchemy import and_

    query = select(DicomInstance).where(and_(*filters)) if filters else select(DicomInstance)
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match both (wildcard takes precedence)
    assert len(instances) == 2


@pytest.mark.asyncio
async def test_parse_qido_params_multiple_word_fuzzy(db_session):
    """Test _parse_qido_params() handles multiple word fuzzy matching.

    This test verifies:
    - Multiple words in search term work correctly
    - Each word is treated as independent prefix match
    """
    # Create test instances
    john_doe = await create_test_instance(db_session, "John^Doe", "1.2.3.1")
    jane_dobson = await create_test_instance(db_session, "Jane^Dobson", "1.2.3.2")
    johnson_smith = await create_test_instance(db_session, "Johnson^Smith", "1.2.3.3")
    smith_jane = await create_test_instance(db_session, "Smith^Jane", "1.2.3.4")

    # Build filters with multiple words
    params = QueryParams({"PatientName": "joh do", "fuzzymatching": "true"})
    filters = _parse_qido_params(params, fuzzymatching=True)

    # Apply filters to query
    from sqlalchemy import and_

    query = select(DicomInstance).where(and_(*filters)) if filters else select(DicomInstance)
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match all with "joh" OR "do" prefix
    assert len(instances) == 3
    instance_uids = {inst.study_instance_uid for inst in instances}
    assert "1.2.3.1" in instance_uids  # John^Doe
    assert "1.2.3.2" in instance_uids  # Jane^Dobson
    assert "1.2.3.3" in instance_uids  # Johnson^Smith
    assert "1.2.3.4" not in instance_uids  # Smith^Jane (no match)


@pytest.mark.asyncio
async def test_parse_qido_params_referring_physician_fuzzy(db_session):
    """Test fuzzy matching works for ReferringPhysicianName too.

    This test verifies:
    - Fuzzy matching is applied to referring_physician_name column
    - Not just patient_name
    """
    # Create test instances with referring physician names
    instance1 = DicomInstance(
        study_instance_uid="1.2.3.1",
        series_instance_uid="1.2.3.1.1",
        sop_instance_uid="1.2.3.1.1.1",
        sop_class_uid="1.2.840.10008.5.1.4.1.1.2",
        patient_name="Patient^One",
        referring_physician_name="Dr^Smith",
        patient_id="PAT-001",
        study_date="20260215",
        modality="CT",
        dicom_json={},
        file_path="/tmp/1.dcm",
        file_size=1024,
    )
    instance2 = DicomInstance(
        study_instance_uid="1.2.3.2",
        series_instance_uid="1.2.3.2.1",
        sop_instance_uid="1.2.3.2.1.1",
        sop_class_uid="1.2.840.10008.5.1.4.1.1.2",
        patient_name="Patient^Two",
        referring_physician_name="Dr^Johnson",
        patient_id="PAT-002",
        study_date="20260215",
        modality="CT",
        dicom_json={},
        file_path="/tmp/2.dcm",
        file_size=1024,
    )
    db_session.add(instance1)
    db_session.add(instance2)
    await db_session.commit()

    # Build filters for referring physician with fuzzy matching
    params = QueryParams({"ReferringPhysicianName": "smi", "fuzzymatching": "true"})
    filters = _parse_qido_params(params, fuzzymatching=True)

    # Apply filters to query
    from sqlalchemy import and_

    query = select(DicomInstance).where(and_(*filters)) if filters else select(DicomInstance)
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match Dr^Smith
    assert len(instances) == 1
    assert instances[0].study_instance_uid == "1.2.3.1"
