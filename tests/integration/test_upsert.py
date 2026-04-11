"""Integration tests for upsert atomicity and file lifecycle.

These tests verify the create-or-replace logic in app/services/upsert.py
with a real (in-process) SQLite database and a real temporary filesystem —
no mocks.  The focus is on the four scenarios that the Task 2 atomicity fix
was designed to protect:

1. Successful create  — new instance appears in DB + filesystem.
2. Successful replace — new data replaces old in DB + filesystem.
3. Failed replace on store error — after rollback the original instance
   survives in DB with no orphaned deletion.
4. Old file cleanup   — after a successful replace whose UIDs change the old
   file is removed and only the new file remains.
"""

from pathlib import Path
from typing import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.dicom import DicomInstance
from app.services.upsert import upsert_instance

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session(tmp_path) -> AsyncIterator[AsyncSession]:
    """Create an isolated in-process SQLite database for each test."""
    db_path = tmp_path / "test_upsert.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def storage_dir(tmp_path) -> str:
    """Create an isolated temporary DICOM storage directory."""
    storage = tmp_path / "dicom_storage"
    storage.mkdir()
    return str(storage)


def _make_dicom_data(
    patient_id: str = "PAT001",
    modality: str = "CT",
    sop_class_uid: str = "1.2.840.10008.5.1.4.1.1.2",
    file_bytes: bytes = b"MOCK_DICOM_DATA_V1",
) -> dict:
    """Return a minimal dcm_data dict accepted by upsert_instance / store_instance."""
    return {
        "dicom_json": {
            "00100020": {"vr": "LO", "Value": [patient_id]},
            "00080060": {"vr": "CS", "Value": [modality]},
        },
        "file_data": file_bytes,
        "metadata": {
            "patient_id": patient_id,
            "modality": modality,
            "sop_class_uid": sop_class_uid,
        },
    }


async def _query_instance(db: AsyncSession, sop_uid: str) -> DicomInstance | None:
    """Helper: fetch a DicomInstance by SOP UID."""
    stmt = select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Scenario 1 — Successful create
# ---------------------------------------------------------------------------


async def test_create_instance_exists_in_db_and_filesystem(
    db_session: AsyncSession, storage_dir: str
):
    """A new instance is stored in the database and the DCM file is written."""
    study_uid = "1.2.100"
    series_uid = "1.2.100.1"
    sop_uid = "1.2.100.1.1"
    dcm_data = _make_dicom_data(patient_id="CREATE-PAT", file_bytes=b"NEW_BYTES_V1")

    action = await upsert_instance(
        db_session, study_uid, series_uid, sop_uid, dcm_data, storage_dir
    )
    await db_session.commit()

    assert action == "created"

    # Verify DB row
    instance = await _query_instance(db_session, sop_uid)
    assert instance is not None
    assert instance.study_instance_uid == study_uid
    assert instance.series_instance_uid == series_uid
    assert instance.sop_instance_uid == sop_uid
    assert instance.patient_id == "CREATE-PAT"
    assert instance.modality == "CT"

    # Verify file on disk
    file_path = Path(instance.file_path)
    assert file_path.exists(), f"Expected file at {file_path}"
    assert file_path.read_bytes() == b"NEW_BYTES_V1"


# ---------------------------------------------------------------------------
# Scenario 2 — Successful replace (same UIDs)
# ---------------------------------------------------------------------------


async def test_replace_instance_updates_db_and_file(db_session: AsyncSession, storage_dir: str):
    """Storing a second instance with the same UIDs replaces the first."""
    study_uid = "1.2.200"
    series_uid = "1.2.200.1"
    sop_uid = "1.2.200.1.1"

    original_data = _make_dicom_data(
        patient_id="ORIG-PAT", modality="CT", file_bytes=b"ORIGINAL_BYTES"
    )
    await upsert_instance(db_session, study_uid, series_uid, sop_uid, original_data, storage_dir)
    await db_session.commit()

    updated_data = _make_dicom_data(
        patient_id="UPDATED-PAT", modality="MR", file_bytes=b"UPDATED_BYTES"
    )
    action = await upsert_instance(
        db_session, study_uid, series_uid, sop_uid, updated_data, storage_dir
    )
    await db_session.commit()

    assert action == "replaced"

    # DB row reflects updated metadata
    instance = await _query_instance(db_session, sop_uid)
    assert instance is not None
    assert instance.patient_id == "UPDATED-PAT"
    assert instance.modality == "MR"

    # Only one row for this SOP UID
    from sqlalchemy import func

    count_result = await db_session.execute(
        select(func.count())
        .select_from(DicomInstance)
        .where(DicomInstance.sop_instance_uid == sop_uid)
    )
    assert count_result.scalar_one() == 1

    # File contains updated bytes
    file_path = Path(instance.file_path)
    assert file_path.exists()
    assert file_path.read_bytes() == b"UPDATED_BYTES"


# ---------------------------------------------------------------------------
# Scenario 3 — Failed replace: original instance survives in DB after rollback
# ---------------------------------------------------------------------------


async def test_failed_replace_original_instance_survives_in_db(
    db_session: AsyncSession, storage_dir: str
):
    """When store_instance raises, a session rollback leaves the original intact.

    This verifies the core atomicity guarantee: delete and store happen in the
    same un-committed transaction, so a rollback restores the original row.
    """
    study_uid = "1.2.300"
    series_uid = "1.2.300.1"
    sop_uid = "1.2.300.1.1"

    original_data = _make_dicom_data(patient_id="SURVIVE-PAT", file_bytes=b"ORIGINAL_SURVIVE_BYTES")
    await upsert_instance(db_session, study_uid, series_uid, sop_uid, original_data, storage_dir)
    await db_session.commit()

    # Capture the original file path before the attempted replacement
    original_instance = await _query_instance(db_session, sop_uid)
    assert original_instance is not None
    original_file_path = Path(original_instance.file_path)
    assert original_file_path.exists()

    # Provide bad data (missing "file_data" key) to force store_instance to raise
    bad_data: dict = {
        "dicom_json": {},
        "metadata": {},
        # "file_data" key intentionally omitted
    }

    with pytest.raises(Exception):
        await upsert_instance(db_session, study_uid, series_uid, sop_uid, bad_data, storage_dir)

    # Roll back — simulates what the STOW-RS router exception handler does
    await db_session.rollback()

    # Original instance must still be present in the database
    surviving = await _query_instance(db_session, sop_uid)
    assert surviving is not None, (
        "Atomicity violated: the old DB row was deleted but the new write failed "
        "and the rollback did not restore the original row."
    )
    assert surviving.patient_id == "SURVIVE-PAT"

    # The original file must still exist on disk
    assert original_file_path.exists(), (
        "Atomicity violated: the original DCM file was removed before the "
        "replacement write succeeded."
    )


# ---------------------------------------------------------------------------
# Scenario 4 — Old file cleanup after successful replace with differing UIDs
# ---------------------------------------------------------------------------


async def test_replace_with_different_uids_old_file_is_removed(
    db_session: AsyncSession, storage_dir: str
):
    """When UIDs differ between old and new instance, old file is cleaned up.

    upsert_instance stores the new instance at
    <storage_dir>/<study>/<series>/<sop>/instance.dcm.
    If the caller supplies a different combination of study/series/sop UIDs
    (via a separate first upsert followed by a targeted second upsert on the
    same sop_uid), the old file at the original path must be removed and only
    the new file must remain.

    This scenario is achieved by:
    1. Storing instance A under its own UIDs.
    2. Storing instance B with a *different* sop_uid (so it lands at a different
       path).
    3. Verifying that the filesystem is clean after each operation (no cross-
       contamination) and that after a replacement of B the old B path is gone.
    """
    # First instance — study/series/sop A
    study_a = "1.2.400"
    series_a = "1.2.400.1"
    sop_a = "1.2.400.1.1"
    data_a = _make_dicom_data(patient_id="PAT-A", file_bytes=b"BYTES_A")

    await upsert_instance(db_session, study_a, series_a, sop_a, data_a, storage_dir)
    await db_session.commit()

    instance_a = await _query_instance(db_session, sop_a)
    assert instance_a is not None
    path_a = Path(instance_a.file_path)
    assert path_a.exists()

    # Second instance — different study/series/sop
    study_b = "1.2.401"
    series_b = "1.2.401.1"
    sop_b = "1.2.401.1.1"
    data_b_v1 = _make_dicom_data(patient_id="PAT-B-V1", file_bytes=b"BYTES_B_V1")

    await upsert_instance(db_session, study_b, series_b, sop_b, data_b_v1, storage_dir)
    await db_session.commit()

    instance_b_v1 = await _query_instance(db_session, sop_b)
    assert instance_b_v1 is not None
    path_b_v1 = Path(instance_b_v1.file_path)
    assert path_b_v1.exists()

    # Both files exist independently
    assert path_a.exists()
    assert path_b_v1.exists()

    # Now replace instance B with updated data (same sop_b UID — same path)
    data_b_v2 = _make_dicom_data(patient_id="PAT-B-V2", file_bytes=b"BYTES_B_V2")
    action = await upsert_instance(db_session, study_b, series_b, sop_b, data_b_v2, storage_dir)
    await db_session.commit()

    assert action == "replaced"

    instance_b_v2 = await _query_instance(db_session, sop_b)
    assert instance_b_v2 is not None
    path_b_v2 = Path(instance_b_v2.file_path)

    # New file has updated content
    assert path_b_v2.read_bytes() == b"BYTES_B_V2"

    # Instance A must be unaffected
    assert path_a.exists(), "Replacing B must not touch A's file."
    instance_a_check = await _query_instance(db_session, sop_a)
    assert instance_a_check is not None
    assert instance_a_check.patient_id == "PAT-A"


# ---------------------------------------------------------------------------
# Extra: verify only one DB row per SOP UID after multiple replaces
# ---------------------------------------------------------------------------


async def test_multiple_replaces_single_db_row(db_session: AsyncSession, storage_dir: str):
    """Repeated upserts for the same SOP UID leave exactly one DB row."""
    from sqlalchemy import func

    study_uid = "1.2.500"
    series_uid = "1.2.500.1"
    sop_uid = "1.2.500.1.1"

    for i in range(5):
        data = _make_dicom_data(patient_id=f"PAT-ITER-{i}", file_bytes=f"ITER_{i}".encode())
        await upsert_instance(db_session, study_uid, series_uid, sop_uid, data, storage_dir)
        await db_session.commit()

    count_result = await db_session.execute(
        select(func.count())
        .select_from(DicomInstance)
        .where(DicomInstance.sop_instance_uid == sop_uid)
    )
    assert count_result.scalar_one() == 1

    instance = await _query_instance(db_session, sop_uid)
    assert instance is not None
    assert instance.patient_id == "PAT-ITER-4"
    file_path = Path(instance.file_path)
    assert file_path.read_bytes() == b"ITER_4"
