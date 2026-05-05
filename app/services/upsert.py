"""
Upsert service for PUT STOW-RS operations.

Provides two layers of create-or-replace:

* :func:`upsert_instance` — the legacy one-instance-at-a-time API.  Still
  used by direct callers (tests, smaller scripts) that don't need the
  bulk path.  Internally it now defers to the bulk helper so we have a
  single source of truth for the SQL shape.
* :func:`upsert_instances_bulk` — the high-throughput batch path used by
  the streaming STOW router.  One ``SELECT … WHERE sop IN (…)`` to learn
  which SOPs are replaces (and capture their old file paths), then one
  Core ``INSERT … ON CONFLICT (sop_instance_uid) DO UPDATE`` for the
  whole batch.  Replaces N round-trips per part with three.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import dialect_insert
from app.models.dicom import DicomInstance


@dataclass(slots=True, frozen=True)
class BulkUpsertRecord:
    """Input to :func:`upsert_instances_bulk` — one parsed DICOM instance.

    All fields are plain values so the same struct can be built from a
    :class:`pydicom.Dataset`, a :class:`ParsedPartRecord` (process-pool
    worker output), or a manually-constructed dict in tests.  ``file_data``
    is the raw DCM bytes that get written to disk.
    """

    sop_uid: str
    study_uid: str
    series_uid: str
    sop_class_uid: str | None
    transfer_syntax_uid: str | None
    dicom_json: dict[str, Any]
    file_data: bytes
    metadata: dict[str, Any]


@dataclass(slots=True, frozen=True)
class BulkUpsertOutcome:
    """Per-record outcome from :func:`upsert_instances_bulk`."""

    sop_uid: str
    action: str  # "created" or "replaced"
    file_path: str  # absolute path of the freshly-written .dcm file
    old_file_path: str | None  # for "replaced" outcomes, path of the previous file
    instance_id: uuid.UUID  # primary key of the persisted DicomInstance


def _try_unlink(path: Path) -> None:
    """Unlink a file, ignoring FileNotFoundError (EAFP)."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _try_rmdir(path: Path) -> None:
    """Remove a directory if empty, ignoring OSError (EAFP, atomic check)."""
    try:
        path.rmdir()  # atomic: raises OSError if non-empty or missing
    except OSError:
        pass


async def upsert_instance(
    db: AsyncSession,
    study_uid: str,
    series_uid: str,
    sop_uid: str,
    dcm_data: dict[str, Any],
    storage_dir: str,
) -> tuple[str, str]:
    """Create or replace a single DICOM instance.

    Thin wrapper around :func:`upsert_instances_bulk` that keeps the
    legacy one-shot API working for direct callers (unit tests, scripts,
    individual REST endpoints).  The session is left uncommitted so the
    caller can include additional writes (e.g. ``ChangeFeedEntry``) in
    the same atomic transaction.

    Args:
        db: Database session
        study_uid: Study Instance UID
        series_uid: Series Instance UID
        sop_uid: SOP Instance UID
        dcm_data: Dictionary containing:
            - file_data: Raw DICOM bytes
            - dicom_json: DICOM JSON metadata
            - metadata: Extracted searchable metadata
        storage_dir: Base directory for DICOM file storage

    Returns:
        ``(action, file_path)`` where:
            * ``action`` is ``"created"`` for a new instance or
              ``"replaced"`` when an existing instance was overwritten.
            * ``file_path`` is the absolute path to the freshly-written
              ``.dcm`` file.  Callers track this path so the file can be
              unlinked if the surrounding DB transaction is rolled back.

    Note:
        Caller is responsible for committing the session.
    """
    metadata = dcm_data.get("metadata", {})
    record = BulkUpsertRecord(
        sop_uid=sop_uid,
        study_uid=study_uid,
        series_uid=series_uid,
        sop_class_uid=metadata.get("sop_class_uid"),
        transfer_syntax_uid=metadata.get("transfer_syntax_uid"),
        dicom_json=dcm_data["dicom_json"],
        file_data=dcm_data["file_data"],
        metadata=metadata,
    )
    outcomes = await upsert_instances_bulk(db, [record], storage_dir)
    outcome = outcomes[0]

    # Single-shot callers expect the legacy file-cleanup behaviour: when the
    # replacement targets a different on-disk path, unlink the old file and
    # collapse empty ancestor directories.  The bulk path defers cleanup to
    # the STOW orchestrator (post-commit) — for the one-shot path we run it
    # inline so callers don't have to manage the deferred-cleanup contract.
    if outcome.action == "replaced" and outcome.old_file_path is not None:
        old_path = Path(outcome.old_file_path)
        if old_path != Path(outcome.file_path):
            await asyncio.to_thread(_try_unlink, old_path)
            instance_dir = old_path.parent
            series_dir = instance_dir.parent
            study_dir = series_dir.parent
            await asyncio.to_thread(_try_rmdir, instance_dir)
            await asyncio.to_thread(_try_rmdir, series_dir)
            await asyncio.to_thread(_try_rmdir, study_dir)

    return outcome.action, outcome.file_path


async def upsert_instances_bulk(
    db: AsyncSession,
    records: list[BulkUpsertRecord],
    storage_dir: str,
) -> list[BulkUpsertOutcome]:
    """Create-or-replace a batch of DICOM instances in three round-trips.

    Replaces the legacy "SELECT-then-DELETE-then-INSERT" loop (three
    round-trips *per record*) with:

    1. One ``SELECT sop_instance_uid, file_path FROM dicom_instances WHERE
       sop_instance_uid IN (:sops)`` to learn which SOPs already exist
       (replaces) and what their file paths are.
    2. Per-record file write to disk (sequential — file-system I/O is
       cheap relative to DB round-trips).
    3. One Core ``INSERT … ON CONFLICT (sop_instance_uid) DO UPDATE``
       that creates or updates every row in the batch atomically.

    The session is left uncommitted so the STOW router can include the
    matching ``change_feed`` writes in the same transaction.  Callers are
    responsible for unlinking the ``old_file_path`` reported in each
    outcome AFTER ``db.commit()`` succeeds — doing it sooner would leak
    files when a later constraint failure rolls the bulk insert back.

    Returns one :class:`BulkUpsertOutcome` per input record, in the same
    order, so callers can pair them up with whatever per-record metadata
    they need (response entries, change-feed action codes, etc.).
    """
    if not records:
        return []

    sops = [r.sop_uid for r in records]
    existing_q = await db.execute(
        select(DicomInstance.sop_instance_uid, DicomInstance.file_path).where(
            DicomInstance.sop_instance_uid.in_(sops)
        )
    )
    existing_map: dict[str, str] = {row[0]: row[1] for row in existing_q.all()}

    # Generate a UUID per record up front so we can return it in the
    # outcome (callers building events / change-feed rows need it).
    instance_ids: list[uuid.UUID] = [uuid.uuid4() for _ in records]
    file_paths: list[str] = []
    for record in records:
        path = await _write_record_to_disk(record, storage_dir)
        file_paths.append(path)

    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for record, instance_id, file_path in zip(records, instance_ids, file_paths):
        rows.append(
            {
                "id": instance_id,
                "study_instance_uid": record.study_uid,
                "series_instance_uid": record.series_uid,
                "sop_instance_uid": record.sop_uid,
                "sop_class_uid": record.sop_class_uid,
                "transfer_syntax_uid": record.transfer_syntax_uid,
                "patient_id": record.metadata.get("patient_id"),
                "patient_name": record.metadata.get("patient_name"),
                "study_date": record.metadata.get("study_date"),
                "study_time": record.metadata.get("study_time"),
                "accession_number": record.metadata.get("accession_number"),
                "study_description": record.metadata.get("study_description"),
                "modality": record.metadata.get("modality"),
                "series_description": record.metadata.get("series_description"),
                "series_number": record.metadata.get("series_number"),
                "instance_number": record.metadata.get("instance_number"),
                "referring_physician_name": record.metadata.get("referring_physician_name"),
                "dicom_json": record.dicom_json,
                "file_path": file_path,
                "file_size": len(record.file_data),
                "created_at": now,
                "updated_at": now,
            }
        )

    insert_stmt = dialect_insert(db, DicomInstance.__table__).values(rows)
    # ON CONFLICT DO UPDATE — on a duplicate sop_instance_uid we replace
    # every column EXCEPT the synthetic primary key and the original
    # ``created_at`` timestamp.  ``excluded`` resolves to the row that
    # would have been inserted, which is the new data.
    excluded = insert_stmt.excluded
    update_set = {
        col.name: excluded[col.name]
        for col in DicomInstance.__table__.columns
        if col.name not in ("id", "sop_instance_uid", "created_at")
    }
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=[DicomInstance.__table__.c.sop_instance_uid],
        set_=update_set,
    )
    await db.execute(upsert_stmt)

    # The Core UPDATE bypasses SQLAlchemy's identity map, so any
    # ``DicomInstance`` ORM rows the caller already loaded for the same
    # ``sop_instance_uid`` still hold pre-upsert state.  Expire them so
    # follow-up ``select(...)`` calls re-fetch the freshly-written
    # columns instead of returning stale cached attributes.  Limited to
    # the SOPs we just touched to avoid invalidating unrelated rows.
    if existing_map:
        identity_map = db.identity_map
        stale_objects = [
            obj
            for obj in list(identity_map.values())
            if isinstance(obj, DicomInstance) and obj.sop_instance_uid in existing_map
        ]
        for obj in stale_objects:
            db.expire(obj)

    outcomes: list[BulkUpsertOutcome] = []
    for record, instance_id, file_path in zip(records, instance_ids, file_paths):
        old_path = existing_map.get(record.sop_uid)
        if old_path is not None:
            outcomes.append(
                BulkUpsertOutcome(
                    sop_uid=record.sop_uid,
                    action="replaced",
                    file_path=file_path,
                    old_file_path=old_path,
                    instance_id=instance_id,
                )
            )
        else:
            outcomes.append(
                BulkUpsertOutcome(
                    sop_uid=record.sop_uid,
                    action="created",
                    file_path=file_path,
                    old_file_path=None,
                    instance_id=instance_id,
                )
            )
    return outcomes


async def _write_record_to_disk(record: BulkUpsertRecord, storage_dir: str) -> str:
    """Write one DICOM record to disk under ``<storage>/<study>/<series>/<sop>/instance.dcm``.

    Returns the absolute path of the freshly-written file.  The directory
    is created with ``parents=True``; failures bubble up so the caller
    can degrade the affected part to a 0xC000 failure.
    """
    instance_dir = Path(storage_dir) / record.study_uid / record.series_uid / record.sop_uid
    await asyncio.to_thread(instance_dir.mkdir, parents=True, exist_ok=True)
    file_path = instance_dir / "instance.dcm"
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(record.file_data)
    return str(file_path)


async def delete_instance(
    db: AsyncSession, study_uid: str, series_uid: str, sop_uid: str, storage_dir: str
) -> None:
    """
    Delete a DICOM instance from the database only.

    Filesystem cleanup is intentionally omitted here so that the file is
    never removed before the surrounding DB transaction is committed.
    Callers that need to reclaim disk space (e.g. upsert_instance) must
    perform the filesystem unlink themselves *after* the write succeeds.

    Args:
        db: Database session
        study_uid: Study Instance UID
        series_uid: Series Instance UID
        sop_uid: SOP Instance UID
        storage_dir: Base directory for DICOM file storage

    Note:
        Caller is responsible for committing the session.
    """
    # Delete from database
    delete_stmt = sql_delete(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)
    await db.execute(delete_stmt)


async def store_instance(
    db: AsyncSession,
    study_uid: str,
    series_uid: str,
    sop_uid: str,
    dcm_data: dict[str, Any],
    storage_dir: str,
) -> str:
    """Store a DICOM instance in database and filesystem.

    Args:
        db: Database session
        study_uid: Study Instance UID
        series_uid: Series Instance UID
        sop_uid: SOP Instance UID
        dcm_data: Dictionary containing:
            - file_data: Raw DICOM bytes
            - dicom_json: DICOM JSON metadata
            - metadata: Extracted searchable metadata
        storage_dir: Base directory for DICOM file storage

    Returns:
        Absolute path to the freshly-written ``.dcm`` file so callers
        can register it for rollback cleanup.

    Note:
        Caller is responsible for committing the session.
    """
    instance_dir = Path(storage_dir) / study_uid / series_uid / sop_uid
    await asyncio.to_thread(instance_dir.mkdir, parents=True, exist_ok=True)

    file_name = "instance.dcm"  # Consistent with dicom_engine and frame_cache
    file_path = instance_dir / file_name
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(dcm_data["file_data"])

    metadata = dcm_data.get("metadata", {})

    instance = DicomInstance(
        id=uuid.uuid4(),
        study_instance_uid=study_uid,
        series_instance_uid=series_uid,
        sop_instance_uid=sop_uid,
        sop_class_uid=metadata.get("sop_class_uid"),
        transfer_syntax_uid=metadata.get("transfer_syntax_uid"),
        patient_id=metadata.get("patient_id"),
        patient_name=metadata.get("patient_name"),
        study_date=metadata.get("study_date"),
        study_time=metadata.get("study_time"),
        accession_number=metadata.get("accession_number"),
        study_description=metadata.get("study_description"),
        modality=metadata.get("modality"),
        series_description=metadata.get("series_description"),
        series_number=metadata.get("series_number"),
        instance_number=metadata.get("instance_number"),
        referring_physician_name=metadata.get("referring_physician_name"),
        dicom_json=dcm_data["dicom_json"],
        file_path=str(file_path),
        file_size=len(dcm_data["file_data"]),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    db.add(instance)
    return str(file_path)
