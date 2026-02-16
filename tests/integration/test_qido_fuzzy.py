"""Integration tests for fuzzy matching in QIDO-RS search (Phase 4, Task 2)."""

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


def create_test_dicom(patient_name: str) -> tuple[Dataset, str, str, str]:
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
    ds.PatientID = f"PAT-{sop_uid[:8]}"
    ds.PatientName = patient_name
    ds.StudyDate = "20260215"
    ds.StudyTime = "120000"
    ds.Modality = "CT"
    ds.AccessionNumber = f"ACC-{sop_uid[:8]}"

    return ds, study_uid, series_uid, sop_uid


async def store_test_instance(db_session: AsyncSession, patient_name: str) -> DicomInstance:
    """Helper to store a test DICOM instance."""
    ds, study_uid, series_uid, sop_uid = create_test_dicom(patient_name)

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


@pytest.mark.asyncio
async def test_fuzzy_search_patient_name(db_session):
    """Test fuzzy search finds patients by name prefix.

    This test verifies:
    - Fuzzy matching enabled via fuzzymatching=true parameter
    - Prefix matching on given name component ("joh" matches "John^Doe")
    - Case-insensitive matching
    """
    # Create test patients
    john_doe = await store_test_instance(db_session, "John^Doe")
    jane_smith = await store_test_instance(db_session, "Jane^Smith")
    doe_john = await store_test_instance(db_session, "Doe^John")

    # Import here to simulate API behavior
    from app.services.search_utils import build_fuzzy_name_filter

    # Test fuzzy search for "joh" (should match "John^Doe" and "Doe^John")
    query = select(DicomInstance).where(build_fuzzy_name_filter("joh", DicomInstance.patient_name))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches
    assert len(instances) == 2
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert john_doe.sop_instance_uid in instance_uids
    assert doe_john.sop_instance_uid in instance_uids
    assert jane_smith.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_fuzzy_search_multiple_words(db_session):
    """Test fuzzy search with multiple words.

    This test verifies:
    - Multiple word matching ("joh do" matches "John^Doe")
    - Each word is treated as independent prefix match
    - OR logic between words
    """
    # Create test patients
    john_doe = await store_test_instance(db_session, "John^Doe")
    jane_dobson = await store_test_instance(db_session, "Jane^Dobson")
    johnson_smith = await store_test_instance(db_session, "Johnson^Smith")

    from app.services.search_utils import build_fuzzy_name_filter

    # Test fuzzy search for "joh do" (should match all three)
    query = select(DicomInstance).where(
        build_fuzzy_name_filter("joh do", DicomInstance.patient_name)
    )
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches
    assert len(instances) == 3
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert john_doe.sop_instance_uid in instance_uids
    assert jane_dobson.sop_instance_uid in instance_uids
    assert johnson_smith.sop_instance_uid in instance_uids


@pytest.mark.asyncio
async def test_fuzzy_search_family_name_only(db_session):
    """Test fuzzy search matches family name component.

    This test verifies:
    - Prefix matching on family name ("do" matches "Doe^John")
    - Matching works on any name component
    """
    # Create test patients
    john_doe = await store_test_instance(db_session, "John^Doe")
    doe_john = await store_test_instance(db_session, "Doe^John")
    smith_jane = await store_test_instance(db_session, "Smith^Jane")

    from app.services.search_utils import build_fuzzy_name_filter

    # Test fuzzy search for "do" (should match "Doe^..." names)
    query = select(DicomInstance).where(build_fuzzy_name_filter("do", DicomInstance.patient_name))
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Verify matches
    assert len(instances) == 2
    instance_uids = {inst.sop_instance_uid for inst in instances}
    assert john_doe.sop_instance_uid in instance_uids
    assert doe_john.sop_instance_uid in instance_uids
    assert smith_jane.sop_instance_uid not in instance_uids


@pytest.mark.asyncio
async def test_exact_search_when_fuzzy_disabled(db_session):
    """Test exact search when fuzzymatching=false.

    This test verifies:
    - Exact matching is used when fuzzymatching is disabled
    - Partial matches are NOT returned
    - Full name must match exactly
    """
    # Create test patients
    john_doe = await store_test_instance(db_session, "John^Doe")
    jane_smith = await store_test_instance(db_session, "Jane^Smith")

    # Test exact search for "joh" (should NOT match "John^Doe")
    query = select(DicomInstance).where(DicomInstance.patient_name == "joh")
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should find nothing
    assert len(instances) == 0

    # Test exact search for "John^Doe" (should match)
    query = select(DicomInstance).where(DicomInstance.patient_name == "John^Doe")
    result = await db_session.execute(query)
    instances = result.scalars().all()

    # Should find exact match
    assert len(instances) == 1
    assert instances[0].sop_instance_uid == john_doe.sop_instance_uid
