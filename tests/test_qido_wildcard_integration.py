"""Integration tests for QIDO-RS wildcard matching (Phase 4, Task 3).

These tests verify that wildcard matching works through _parse_qido_params().
"""

import pytest
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from starlette.datastructures import QueryParams

from app.models.dicom import DicomInstance
from app.routers.dicomweb import _parse_qido_params


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
    patient_id: str,
    study_description: str | None = None,
    accession_number: str | None = None,
    study_uid: str | None = None,
) -> DicomInstance:
    """Create a test DICOM instance in the database."""
    if not study_uid:
        import uuid
        study_uid = f"1.2.3.{uuid.uuid4().int}"

    instance = DicomInstance(
        study_instance_uid=study_uid,
        series_instance_uid=f"{study_uid}.1",
        sop_instance_uid=f"{study_uid}.1.1",
        sop_class_uid="1.2.840.10008.5.1.4.1.1.2",
        patient_id=patient_id,
        patient_name=f"Patient^{patient_id}",
        study_date="20260215",
        modality="CT",
        study_description=study_description,
        accession_number=accession_number,
        dicom_json={},
        file_path=f"/tmp/{study_uid}.dcm",
        file_size=1024,
    )
    db_session.add(instance)
    await db_session.commit()
    await db_session.refresh(instance)
    return instance


@pytest.mark.asyncio
async def test_qido_wildcard_patient_id_asterisk(db_session):
    """Test QIDO-RS with asterisk wildcard in PatientID."""
    # Create test instances
    await create_test_instance(db_session, "PAT123", study_uid="1.2.3.1")
    await create_test_instance(db_session, "PATIENT", study_uid="1.2.3.2")
    await create_test_instance(db_session, "PAT12", study_uid="1.2.3.3")
    await create_test_instance(db_session, "OTHER123", study_uid="1.2.3.4")

    # Build filters with wildcard: PAT*
    params = QueryParams({"PatientID": "PAT*"})
    filters = _parse_qido_params(params, fuzzymatching=False)

    # Apply filters to query
    query = select(DicomInstance).where(and_(*filters)) if filters else select(DicomInstance)
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match PAT123, PATIENT, PAT12 (but not OTHER123)
    assert len(instances) == 3
    patient_ids = {inst.patient_id for inst in instances}
    assert "PAT123" in patient_ids
    assert "PATIENT" in patient_ids
    assert "PAT12" in patient_ids
    assert "OTHER123" not in patient_ids


@pytest.mark.asyncio
async def test_qido_wildcard_patient_id_question_mark(db_session):
    """Test QIDO-RS with question mark wildcard in PatientID."""
    # Create test instances
    await create_test_instance(db_session, "PAT123", study_uid="1.2.3.1")
    await create_test_instance(db_session, "PAT12", study_uid="1.2.3.2")
    await create_test_instance(db_session, "PAT1234", study_uid="1.2.3.3")

    # Build filters with wildcard: PAT??? (exactly 6 chars)
    params = QueryParams({"PatientID": "PAT???"})
    filters = _parse_qido_params(params, fuzzymatching=False)

    # Apply filters to query
    query = select(DicomInstance).where(and_(*filters)) if filters else select(DicomInstance)
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should only match PAT123 (exactly 6 chars)
    assert len(instances) == 1
    assert instances[0].patient_id == "PAT123"


@pytest.mark.asyncio
async def test_qido_wildcard_study_description(db_session):
    """Test QIDO-RS with wildcard in StudyDescription."""
    # Create test instances
    await create_test_instance(db_session, "PAT1", study_description="Chest CT", study_uid="1.2.3.1")
    await create_test_instance(db_session, "PAT2", study_description="Chest PE Protocol", study_uid="1.2.3.2")
    await create_test_instance(db_session, "PAT3", study_description="Abdomen CT", study_uid="1.2.3.3")

    # Build filters with wildcard: Chest*
    params = QueryParams({"StudyDescription": "Chest*"})
    filters = _parse_qido_params(params, fuzzymatching=False)

    # Apply filters to query
    query = select(DicomInstance).where(and_(*filters)) if filters else select(DicomInstance)
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match "Chest CT" and "Chest PE Protocol"
    assert len(instances) == 2
    descriptions = {inst.study_description for inst in instances}
    assert "Chest CT" in descriptions
    assert "Chest PE Protocol" in descriptions
    assert "Abdomen CT" not in descriptions


@pytest.mark.asyncio
async def test_qido_wildcard_accession_number(db_session):
    """Test QIDO-RS with wildcard in AccessionNumber."""
    # Create test instances
    await create_test_instance(db_session, "PAT1", accession_number="ACC123", study_uid="1.2.3.1")
    await create_test_instance(db_session, "PAT2", accession_number="ACC456", study_uid="1.2.3.2")
    await create_test_instance(db_session, "PAT3", accession_number="XYZ789", study_uid="1.2.3.3")

    # Build filters with wildcard: ACC*
    params = QueryParams({"AccessionNumber": "ACC*"})
    filters = _parse_qido_params(params, fuzzymatching=False)

    # Apply filters to query
    query = select(DicomInstance).where(and_(*filters)) if filters else select(DicomInstance)
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match ACC123 and ACC456
    assert len(instances) == 2
    accession_numbers = {inst.accession_number for inst in instances}
    assert "ACC123" in accession_numbers
    assert "ACC456" in accession_numbers
    assert "XYZ789" not in accession_numbers


@pytest.mark.asyncio
async def test_qido_exact_match_without_wildcard(db_session):
    """Test QIDO-RS exact match when no wildcards present."""
    # Create test instances
    await create_test_instance(db_session, "PAT123", study_uid="1.2.3.1")
    await create_test_instance(db_session, "PATIENT", study_uid="1.2.3.2")

    # Build filters without wildcard: PAT123 (exact match)
    params = QueryParams({"PatientID": "PAT123"})
    filters = _parse_qido_params(params, fuzzymatching=False)

    # Apply filters to query
    query = select(DicomInstance).where(and_(*filters)) if filters else select(DicomInstance)
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should only match exact "PAT123"
    assert len(instances) == 1
    assert instances[0].patient_id == "PAT123"


@pytest.mark.asyncio
async def test_qido_wildcard_takes_precedence_over_fuzzy(db_session):
    """Test that wildcard matching takes precedence over fuzzy matching.

    When wildcards are present, they should be used even if fuzzymatching=true.
    """
    # Create test instances
    await create_test_instance(db_session, "PAT1", study_uid="1.2.3.1")
    await create_test_instance(db_session, "PAT2", study_uid="1.2.3.2")
    await create_test_instance(db_session, "XYZ1", study_uid="1.2.3.3")

    # Build filters with wildcard AND fuzzy matching enabled
    # Wildcard should take precedence
    params = QueryParams({"PatientID": "PAT*", "fuzzymatching": "true"})
    filters = _parse_qido_params(params, fuzzymatching=True)

    # Apply filters to query
    query = select(DicomInstance).where(and_(*filters)) if filters else select(DicomInstance)
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match PAT1 and PAT2 only (wildcard match, not fuzzy)
    assert len(instances) == 2
    patient_ids = {inst.patient_id for inst in instances}
    assert "PAT1" in patient_ids
    assert "PAT2" in patient_ids
    assert "XYZ1" not in patient_ids


@pytest.mark.asyncio
async def test_qido_wildcard_mixed_asterisk_and_question(db_session):
    """Test QIDO-RS with mixed wildcards (* and ?)."""
    # Create test instances
    await create_test_instance(db_session, "PATIENT1234", study_uid="1.2.3.1")
    await create_test_instance(db_session, "PAT1234", study_uid="1.2.3.2")
    await create_test_instance(db_session, "PATXXX1235", study_uid="1.2.3.3")
    await create_test_instance(db_session, "PAT123", study_uid="1.2.3.4")

    # Build filters with mixed wildcard: PAT*123?
    params = QueryParams({"PatientID": "PAT*123?"})
    filters = _parse_qido_params(params, fuzzymatching=False)

    # Apply filters to query
    query = select(DicomInstance).where(and_(*filters)) if filters else select(DicomInstance)
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match PATIENT1234, PAT1234, PATXXX1235
    assert len(instances) == 3
    patient_ids = {inst.patient_id for inst in instances}
    assert "PATIENT1234" in patient_ids
    assert "PAT1234" in patient_ids
    assert "PATXXX1235" in patient_ids
    assert "PAT123" not in patient_ids  # Doesn't match - needs one more char after 123
