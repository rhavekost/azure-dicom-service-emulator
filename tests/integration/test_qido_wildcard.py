"""Integration tests for wildcard matching in QIDO-RS search (Phase 4, Task 3)."""

from io import BytesIO

import pydicom
import pytest
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.dicom import DicomInstance
from app.services.dicom_engine import dataset_to_dicom_json, extract_searchable_metadata

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


def create_test_dicom(
    patient_id: str,
    study_description: str | None = None,
    accession_number: str | None = None,
) -> tuple[Dataset, str, str, str]:
    """Create a minimal DICOM dataset for testing.

    Returns:
        Tuple of (dataset, study_uid, series_uid, sop_uid)
    """
    study_uid = generate_uid()
    series_uid = generate_uid()
    sop_uid = generate_uid()

    file_meta = pydicom.Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT Image
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(
        filename_or_obj=BytesIO(),
        dataset=Dataset(),
        file_meta=file_meta,
        preamble=b"\x00" * 128,
    )

    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.PatientID = patient_id
    ds.PatientName = f"Patient^{patient_id}"
    ds.StudyDate = "20260215"
    ds.StudyTime = "120000"
    ds.Modality = "CT"

    if study_description:
        ds.StudyDescription = study_description

    if accession_number:
        ds.AccessionNumber = accession_number

    return ds, study_uid, series_uid, sop_uid


async def store_test_instance(
    db_session: AsyncSession,
    patient_id: str,
    study_description: str | None = None,
    accession_number: str | None = None,
) -> DicomInstance:
    """Helper to store a test DICOM instance."""
    ds, study_uid, series_uid, sop_uid = create_test_dicom(
        patient_id, study_description, accession_number
    )

    # Extract metadata
    searchable = extract_searchable_metadata(ds)
    dicom_json = dataset_to_dicom_json(ds)

    # Create instance
    instance = DicomInstance(
        study_instance_uid=study_uid,
        series_instance_uid=series_uid,
        sop_instance_uid=sop_uid,
        sop_class_uid=str(ds.SOPClassUID),
        dicom_json=dicom_json,
        file_path=f"/tmp/{sop_uid}.dcm",
        file_size=1024,
        **searchable,
    )
    db_session.add(instance)
    await db_session.commit()
    await db_session.refresh(instance)

    return instance


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PatientID Wildcard Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_wildcard_search_asterisk_patient_id(db_session):
    """Test asterisk wildcard in PatientID field.

    Wildcard: PAT* should match PAT123, PATIENT, etc.
    """
    # Create test instances
    pat123 = await store_test_instance(db_session, "PAT123")
    patient = await store_test_instance(db_session, "PATIENT")
    pat12 = await store_test_instance(db_session, "PAT12")
    other = await store_test_instance(db_session, "OTHER123")

    from app.services.search_utils import translate_wildcards

    # Simulate QIDO-RS search with wildcard
    pattern = translate_wildcards("PAT*")
    query = select(DicomInstance).where(DicomInstance.patient_id.like(pattern))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches
    assert len(instances) == 3
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert pat123.sop_instance_uid in instance_uids
    assert patient.sop_instance_uid in instance_uids
    assert pat12.sop_instance_uid in instance_uids
    assert other.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_wildcard_search_question_mark_patient_id(db_session):
    """Test question mark wildcard in PatientID field.

    Wildcard: PAT??? should match PAT123 (exactly 6 chars) but not PAT12 or PAT1234.
    """
    # Create test instances
    pat123 = await store_test_instance(db_session, "PAT123")
    pat12 = await store_test_instance(db_session, "PAT12")
    pat1234 = await store_test_instance(db_session, "PAT1234")
    patxyz = await store_test_instance(db_session, "PATXYZ")

    from app.services.search_utils import translate_wildcards

    # Simulate QIDO-RS search with wildcard
    pattern = translate_wildcards("PAT???")
    query = select(DicomInstance).where(DicomInstance.patient_id.like(pattern))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches (only exact length)
    assert len(instances) == 2
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert pat123.sop_instance_uid in instance_uids
    assert patxyz.sop_instance_uid in instance_uids
    assert pat12.sop_instance_uid not in instance_uids
    assert pat1234.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_wildcard_search_mixed_patient_id(db_session):
    """Test mixed wildcards in PatientID field.

    Wildcard: PAT*123? should match PATIENT1234, PAT1234, etc.
    """
    # Create test instances
    patient1234 = await store_test_instance(db_session, "PATIENT1234")
    pat1234 = await store_test_instance(db_session, "PAT1234")
    pat123 = await store_test_instance(db_session, "PAT123")
    patxxx1235 = await store_test_instance(db_session, "PATXXX1235")

    from app.services.search_utils import translate_wildcards

    # Simulate QIDO-RS search with wildcard
    pattern = translate_wildcards("PAT*123?")
    query = select(DicomInstance).where(DicomInstance.patient_id.like(pattern))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches
    assert len(instances) == 3
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert patient1234.sop_instance_uid in instance_uids
    assert pat1234.sop_instance_uid in instance_uids
    assert patxxx1235.sop_instance_uid in instance_uids
    assert pat123.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_wildcard_does_not_affect_exact_match_patient_id(db_session):
    """Test exact match when no wildcards present in PatientID."""
    # Create test instances
    pat123 = await store_test_instance(db_session, "PAT123")
    patient = await store_test_instance(db_session, "PATIENT")

    # Exact match (no wildcards)
    query = select(DicomInstance).where(DicomInstance.patient_id == "PAT123")
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should only match exactly
    assert len(instances) == 1
    assert instances[0].sop_instance_uid == pat123.sop_instance_uid


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  StudyDescription Wildcard Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_wildcard_search_asterisk_study_description(db_session):
    """Test asterisk wildcard in StudyDescription field."""
    # Create test instances
    chest = await store_test_instance(db_session, "PAT1", study_description="Chest CT")
    chest_pe = await store_test_instance(db_session, "PAT2", study_description="Chest PE Protocol")
    abdomen = await store_test_instance(db_session, "PAT3", study_description="Abdomen CT")

    from app.services.search_utils import translate_wildcards

    # Search for "Chest*"
    pattern = translate_wildcards("Chest*")
    query = select(DicomInstance).where(DicomInstance.study_description.like(pattern))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches
    assert len(instances) == 2
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert chest.sop_instance_uid in instance_uids
    assert chest_pe.sop_instance_uid in instance_uids
    assert abdomen.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_wildcard_search_question_mark_study_description(db_session):
    """Test question mark wildcard in StudyDescription field."""
    # Create test instances
    ct1 = await store_test_instance(db_session, "PAT1", study_description="CT1")
    ct2 = await store_test_instance(db_session, "PAT2", study_description="CT2")
    ct10 = await store_test_instance(db_session, "PAT3", study_description="CT10")

    from app.services.search_utils import translate_wildcards

    # Search for "CT?" (exactly 3 chars)
    pattern = translate_wildcards("CT?")
    query = select(DicomInstance).where(DicomInstance.study_description.like(pattern))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches
    assert len(instances) == 2
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert ct1.sop_instance_uid in instance_uids
    assert ct2.sop_instance_uid in instance_uids
    assert ct10.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_wildcard_does_not_affect_exact_match_study_description(db_session):
    """Test exact match when no wildcards present in StudyDescription."""
    # Create test instances
    chest = await store_test_instance(db_session, "PAT1", study_description="Chest CT")
    abdomen = await store_test_instance(db_session, "PAT2", study_description="Abdomen CT")

    # Exact match (no wildcards)
    query = select(DicomInstance).where(DicomInstance.study_description == "Chest CT")
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should only match exactly
    assert len(instances) == 1
    assert instances[0].sop_instance_uid == chest.sop_instance_uid


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AccessionNumber Wildcard Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_wildcard_search_asterisk_accession_number(db_session):
    """Test asterisk wildcard in AccessionNumber field."""
    # Create test instances
    acc123 = await store_test_instance(db_session, "PAT1", accession_number="ACC123")
    acc456 = await store_test_instance(db_session, "PAT2", accession_number="ACC456")
    other = await store_test_instance(db_session, "PAT3", accession_number="XYZ789")

    from app.services.search_utils import translate_wildcards

    # Search for "ACC*"
    pattern = translate_wildcards("ACC*")
    query = select(DicomInstance).where(DicomInstance.accession_number.like(pattern))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches
    assert len(instances) == 2
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert acc123.sop_instance_uid in instance_uids
    assert acc456.sop_instance_uid in instance_uids
    assert other.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_wildcard_search_question_mark_accession_number(db_session):
    """Test question mark wildcard in AccessionNumber field."""
    # Create test instances
    acc123 = await store_test_instance(db_session, "PAT1", accession_number="ACC123")
    acc12 = await store_test_instance(db_session, "PAT2", accession_number="ACC12")
    acc1234 = await store_test_instance(db_session, "PAT3", accession_number="ACC1234")

    from app.services.search_utils import translate_wildcards

    # Search for "ACC???" (exactly 6 chars)
    pattern = translate_wildcards("ACC???")
    query = select(DicomInstance).where(DicomInstance.accession_number.like(pattern))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches
    assert len(instances) == 1
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert acc123.sop_instance_uid in instance_uids
    assert acc12.sop_instance_uid not in instance_uids
    assert acc1234.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_wildcard_does_not_affect_exact_match_accession_number(db_session):
    """Test exact match when no wildcards present in AccessionNumber."""
    # Create test instances
    acc123 = await store_test_instance(db_session, "PAT1", accession_number="ACC123")
    acc456 = await store_test_instance(db_session, "PAT2", accession_number="ACC456")

    # Exact match (no wildcards)
    query = select(DicomInstance).where(DicomInstance.accession_number == "ACC123")
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should only match exactly
    assert len(instances) == 1
    assert instances[0].sop_instance_uid == acc123.sop_instance_uid


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Special Cases
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_wildcard_escapes_sql_wildcards(db_session):
    """Test that existing SQL wildcards are escaped.

    The translate_wildcards function should escape % and _ characters
    so they're treated as literals, not SQL wildcards.
    """
    from app.services.search_utils import translate_wildcards

    # Verify escaping logic
    assert translate_wildcards("PAT_100%*") == r"PAT\_100\%%"
    assert translate_wildcards("100%") == r"100\%"
    assert translate_wildcards("PAT_") == r"PAT\_"

    # Note: Actual SQL escaping behavior varies by database.
    # SQLite requires ESCAPE clause, PostgreSQL uses backslash by default.
    # For this test, we verify the translation is correct.
    # Integration testing will verify actual database behavior.


@pytest.mark.asyncio
async def test_wildcard_at_start(db_session):
    """Test wildcard at the start of search term."""
    # Create test instances
    pat123 = await store_test_instance(db_session, "PAT123")
    xyz123 = await store_test_instance(db_session, "XYZ123")
    abc123 = await store_test_instance(db_session, "ABC123")

    from app.services.search_utils import translate_wildcards

    # Search for "*123" (ends with 123)
    pattern = translate_wildcards("*123")
    query = select(DicomInstance).where(DicomInstance.patient_id.like(pattern))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match all ending in 123
    assert len(instances) == 3


@pytest.mark.asyncio
async def test_wildcard_only_asterisk(db_session):
    """Test asterisk-only wildcard (match all)."""
    # Create test instances
    pat1 = await store_test_instance(db_session, "PAT1")
    pat2 = await store_test_instance(db_session, "PAT2")

    from app.services.search_utils import translate_wildcards

    # Search for "*" (match all)
    pattern = translate_wildcards("*")
    query = select(DicomInstance).where(DicomInstance.patient_id.like(pattern))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match all
    assert len(instances) == 2
