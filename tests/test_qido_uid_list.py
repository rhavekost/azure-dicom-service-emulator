"""Integration tests for UID list matching in QIDO-RS search (Phase 4, Task 4)."""

import pytest
from datetime import datetime, timezone
from io import BytesIO

import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models.dicom import DicomInstance
from app.services.dicom_engine import extract_searchable_metadata, dataset_to_dicom_json


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
    patient_id: str = "PAT001",
    study_uid: str | None = None,
    series_uid: str | None = None,
    sop_uid: str | None = None,
) -> tuple[Dataset, str, str, str]:
    """Create a minimal DICOM dataset for testing.

    Returns:
        Tuple of (dataset, study_uid, series_uid, sop_uid)
    """
    study_uid = study_uid or generate_uid()
    series_uid = series_uid or generate_uid()
    sop_uid = sop_uid or generate_uid()

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

    return ds, study_uid, series_uid, sop_uid


async def store_test_instance(
    db_session: AsyncSession,
    patient_id: str = "PAT001",
    study_uid: str | None = None,
    series_uid: str | None = None,
    sop_uid: str | None = None,
) -> DicomInstance:
    """Helper to store a test DICOM instance."""
    ds, study_uid, series_uid, sop_uid = create_test_dicom(
        patient_id, study_uid, series_uid, sop_uid
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
#  parse_uid_list() Unit Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_parse_uid_list_comma_separated():
    """Test comma-separated UID list parsing."""
    from app.services.search_utils import parse_uid_list

    result = parse_uid_list("1.2.3,4.5.6,7.8.9")
    assert result == ["1.2.3", "4.5.6", "7.8.9"]


def test_parse_uid_list_backslash_separated():
    """Test backslash-separated UID list parsing."""
    from app.services.search_utils import parse_uid_list

    result = parse_uid_list("1.2.3\\4.5.6\\7.8.9")
    assert result == ["1.2.3", "4.5.6", "7.8.9"]


def test_parse_uid_list_mixed_separators():
    """Test mixed comma and backslash separators (normalized to comma)."""
    from app.services.search_utils import parse_uid_list

    result = parse_uid_list("1.2.3,4.5.6\\7.8.9")
    assert result == ["1.2.3", "4.5.6", "7.8.9"]


def test_parse_uid_list_with_whitespace():
    """Test UID list with whitespace (should be stripped)."""
    from app.services.search_utils import parse_uid_list

    result = parse_uid_list("1.2.3 , 4.5.6 , 7.8.9")
    assert result == ["1.2.3", "4.5.6", "7.8.9"]


def test_parse_uid_list_single_uid():
    """Test single UID (no separators)."""
    from app.services.search_utils import parse_uid_list

    result = parse_uid_list("1.2.3.4.5")
    assert result == ["1.2.3.4.5"]


def test_parse_uid_list_empty_components():
    """Test UID list with empty components (should be filtered out)."""
    from app.services.search_utils import parse_uid_list

    result = parse_uid_list("1.2.3,,4.5.6")
    assert result == ["1.2.3", "4.5.6"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  StudyInstanceUID List Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_study_uid_list_comma_separated(db_session):
    """Test comma-separated StudyInstanceUID list.

    Should match studies with any of the UIDs in the list.
    """
    # Create test instances with different study UIDs
    study1_uid = "1.2.840.113619.2.1.1.1"
    study2_uid = "1.2.840.113619.2.1.1.2"
    study3_uid = "1.2.840.113619.2.1.1.3"

    inst1 = await store_test_instance(db_session, "PAT1", study_uid=study1_uid)
    inst2 = await store_test_instance(db_session, "PAT2", study_uid=study2_uid)
    inst3 = await store_test_instance(db_session, "PAT3", study_uid=study3_uid)

    from app.services.search_utils import parse_uid_list

    # Simulate UID list search (comma-separated)
    uid_param = f"{study1_uid},{study3_uid}"
    uids = parse_uid_list(uid_param)
    query = select(DicomInstance).where(DicomInstance.study_instance_uid.in_(uids))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches
    assert len(instances) == 2
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert inst1.sop_instance_uid in instance_uids
    assert inst3.sop_instance_uid in instance_uids
    assert inst2.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_study_uid_list_backslash_separated(db_session):
    """Test backslash-separated StudyInstanceUID list.

    Should match studies with any of the UIDs in the list.
    """
    # Create test instances with different study UIDs
    study1_uid = "1.2.840.113619.2.1.1.1"
    study2_uid = "1.2.840.113619.2.1.1.2"
    study3_uid = "1.2.840.113619.2.1.1.3"

    inst1 = await store_test_instance(db_session, "PAT1", study_uid=study1_uid)
    inst2 = await store_test_instance(db_session, "PAT2", study_uid=study2_uid)
    inst3 = await store_test_instance(db_session, "PAT3", study_uid=study3_uid)

    from app.services.search_utils import parse_uid_list

    # Simulate UID list search (backslash-separated)
    uid_param = f"{study1_uid}\\{study2_uid}"
    uids = parse_uid_list(uid_param)
    query = select(DicomInstance).where(DicomInstance.study_instance_uid.in_(uids))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches
    assert len(instances) == 2
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert inst1.sop_instance_uid in instance_uids
    assert inst2.sop_instance_uid in instance_uids
    assert inst3.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_study_uid_single_exact_match(db_session):
    """Test single StudyInstanceUID uses exact match (no list parsing)."""
    # Create test instances
    study1_uid = "1.2.840.113619.2.1.1.1"
    study2_uid = "1.2.840.113619.2.1.1.2"

    inst1 = await store_test_instance(db_session, "PAT1", study_uid=study1_uid)
    inst2 = await store_test_instance(db_session, "PAT2", study_uid=study2_uid)

    # Exact match (no comma or backslash)
    query = select(DicomInstance).where(DicomInstance.study_instance_uid == study1_uid)
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should only match exactly
    assert len(instances) == 1
    assert instances[0].sop_instance_uid == inst1.sop_instance_uid


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SeriesInstanceUID List Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_series_uid_list_comma_separated(db_session):
    """Test comma-separated SeriesInstanceUID list."""
    # Create test instances with different series UIDs (same study)
    study_uid = "1.2.840.113619.2.1.1.1"
    series1_uid = "1.2.840.113619.2.1.2.1"
    series2_uid = "1.2.840.113619.2.1.2.2"
    series3_uid = "1.2.840.113619.2.1.2.3"

    inst1 = await store_test_instance(db_session, "PAT1", study_uid=study_uid, series_uid=series1_uid)
    inst2 = await store_test_instance(db_session, "PAT1", study_uid=study_uid, series_uid=series2_uid)
    inst3 = await store_test_instance(db_session, "PAT1", study_uid=study_uid, series_uid=series3_uid)

    from app.services.search_utils import parse_uid_list

    # Simulate UID list search (comma-separated)
    uid_param = f"{series1_uid},{series3_uid}"
    uids = parse_uid_list(uid_param)
    query = select(DicomInstance).where(DicomInstance.series_instance_uid.in_(uids))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches
    assert len(instances) == 2
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert inst1.sop_instance_uid in instance_uids
    assert inst3.sop_instance_uid in instance_uids
    assert inst2.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_series_uid_list_backslash_separated(db_session):
    """Test backslash-separated SeriesInstanceUID list."""
    # Create test instances with different series UIDs
    study_uid = "1.2.840.113619.2.1.1.1"
    series1_uid = "1.2.840.113619.2.1.2.1"
    series2_uid = "1.2.840.113619.2.1.2.2"

    inst1 = await store_test_instance(db_session, "PAT1", study_uid=study_uid, series_uid=series1_uid)
    inst2 = await store_test_instance(db_session, "PAT1", study_uid=study_uid, series_uid=series2_uid)

    from app.services.search_utils import parse_uid_list

    # Simulate UID list search (backslash-separated)
    uid_param = f"{series1_uid}\\{series2_uid}"
    uids = parse_uid_list(uid_param)
    query = select(DicomInstance).where(DicomInstance.series_instance_uid.in_(uids))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches
    assert len(instances) == 2


@pytest.mark.asyncio
async def test_series_uid_single_exact_match(db_session):
    """Test single SeriesInstanceUID uses exact match."""
    # Create test instances
    study_uid = "1.2.840.113619.2.1.1.1"
    series1_uid = "1.2.840.113619.2.1.2.1"
    series2_uid = "1.2.840.113619.2.1.2.2"

    inst1 = await store_test_instance(db_session, "PAT1", study_uid=study_uid, series_uid=series1_uid)
    inst2 = await store_test_instance(db_session, "PAT1", study_uid=study_uid, series_uid=series2_uid)

    # Exact match (no comma or backslash)
    query = select(DicomInstance).where(DicomInstance.series_instance_uid == series1_uid)
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should only match exactly
    assert len(instances) == 1
    assert instances[0].sop_instance_uid == inst1.sop_instance_uid


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SOPInstanceUID List Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_sop_uid_list_comma_separated(db_session):
    """Test comma-separated SOPInstanceUID list."""
    # Create test instances with different SOP UIDs
    sop1_uid = "1.2.840.113619.2.1.3.1"
    sop2_uid = "1.2.840.113619.2.1.3.2"
    sop3_uid = "1.2.840.113619.2.1.3.3"

    inst1 = await store_test_instance(db_session, "PAT1", sop_uid=sop1_uid)
    inst2 = await store_test_instance(db_session, "PAT2", sop_uid=sop2_uid)
    inst3 = await store_test_instance(db_session, "PAT3", sop_uid=sop3_uid)

    from app.services.search_utils import parse_uid_list

    # Simulate UID list search (comma-separated)
    uid_param = f"{sop1_uid},{sop2_uid}"
    uids = parse_uid_list(uid_param)
    query = select(DicomInstance).where(DicomInstance.sop_instance_uid.in_(uids))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches
    assert len(instances) == 2
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert inst1.sop_instance_uid in instance_uids
    assert inst2.sop_instance_uid in instance_uids
    assert inst3.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_sop_uid_list_backslash_separated(db_session):
    """Test backslash-separated SOPInstanceUID list."""
    # Create test instances with different SOP UIDs
    sop1_uid = "1.2.840.113619.2.1.3.1"
    sop2_uid = "1.2.840.113619.2.1.3.2"
    sop3_uid = "1.2.840.113619.2.1.3.3"

    inst1 = await store_test_instance(db_session, "PAT1", sop_uid=sop1_uid)
    inst2 = await store_test_instance(db_session, "PAT2", sop_uid=sop2_uid)
    inst3 = await store_test_instance(db_session, "PAT3", sop_uid=sop3_uid)

    from app.services.search_utils import parse_uid_list

    # Simulate UID list search (backslash-separated)
    uid_param = f"{sop2_uid}\\{sop3_uid}"
    uids = parse_uid_list(uid_param)
    query = select(DicomInstance).where(DicomInstance.sop_instance_uid.in_(uids))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches
    assert len(instances) == 2
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert inst2.sop_instance_uid in instance_uids
    assert inst3.sop_instance_uid in instance_uids
    assert inst1.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_sop_uid_single_exact_match(db_session):
    """Test single SOPInstanceUID uses exact match."""
    # Create test instances
    sop1_uid = "1.2.840.113619.2.1.3.1"
    sop2_uid = "1.2.840.113619.2.1.3.2"

    inst1 = await store_test_instance(db_session, "PAT1", sop_uid=sop1_uid)
    inst2 = await store_test_instance(db_session, "PAT2", sop_uid=sop2_uid)

    # Exact match (no comma or backslash)
    query = select(DicomInstance).where(DicomInstance.sop_instance_uid == sop1_uid)
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should only match exactly
    assert len(instances) == 1
    assert instances[0].sop_instance_uid == inst1.sop_instance_uid


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Combined Tests (UID list + other filters)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_uid_list_with_other_filters(db_session):
    """Test UID list matching combined with other search filters.

    UID list should work alongside PatientID, Modality, etc.
    """
    # Create test instances
    study1_uid = "1.2.840.113619.2.1.1.1"
    study2_uid = "1.2.840.113619.2.1.1.2"

    inst1 = await store_test_instance(db_session, "PAT001", study_uid=study1_uid)
    inst2 = await store_test_instance(db_session, "PAT002", study_uid=study2_uid)
    inst3 = await store_test_instance(db_session, "PAT001", study_uid=study2_uid)

    from app.services.search_utils import parse_uid_list
    from sqlalchemy import and_

    # UID list + PatientID filter
    uid_param = f"{study1_uid},{study2_uid}"
    uids = parse_uid_list(uid_param)
    query = select(DicomInstance).where(
        and_(
            DicomInstance.study_instance_uid.in_(uids),
            DicomInstance.patient_id == "PAT001",
        )
    )
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match only instances with both criteria
    assert len(instances) == 2
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert inst1.sop_instance_uid in instance_uids
    assert inst3.sop_instance_uid in instance_uids
    assert inst2.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_uid_list_does_not_break_wildcard_matching(db_session):
    """Test that UID list matching doesn't interfere with wildcard matching.

    When StudyInstanceUID has comma/backslash, use list matching.
    When PatientID has wildcards, use wildcard matching.
    """
    # Create test instances
    study1_uid = "1.2.840.113619.2.1.1.1"
    study2_uid = "1.2.840.113619.2.1.1.2"

    inst1 = await store_test_instance(db_session, "PAT001", study_uid=study1_uid)
    inst2 = await store_test_instance(db_session, "PAT002", study_uid=study2_uid)

    from app.services.search_utils import parse_uid_list, translate_wildcards
    from sqlalchemy import and_

    # UID list + wildcard PatientID
    uid_param = f"{study1_uid},{study2_uid}"
    uids = parse_uid_list(uid_param)
    patient_pattern = translate_wildcards("PAT*")

    query = select(DicomInstance).where(
        and_(
            DicomInstance.study_instance_uid.in_(uids),
            DicomInstance.patient_id.like(patient_pattern),
        )
    )
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should match all (both study UIDs + wildcard patient)
    assert len(instances) == 2
