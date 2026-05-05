"""
STOW-RS Router — Store DICOM instances.

Endpoints:
- POST /v2/studies                        (store new instances)
- POST /v2/studies/{study_instance_uid}   (store into a specific study)
- PUT  /v2/studies                        (upsert instances with optional expiry)
- PUT  /v2/studies/{study_instance_uid}   (upsert into a specific study)
- POST /v2/studies/$bulkUpdate            (bulk-update metadata across studies)

The store paths use a streaming multipart parser plus a ProcessPoolExecutor
for pydicom parsing so large STOW requests (thousands of instances per
request) don't pin the asyncio event loop or buffer the entire body in
memory.  See app/services/multipart.py and app/services/dicom_parse_worker.py
for the pieces that make that work.
"""

import asyncio
import logging
import os
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Optional, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, bindparam, select
from sqlalchemy import update as sql_update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DICOM_PARSE_INFLIGHT
from app.database import dialect_insert, get_db
from app.models.dicom import ChangeFeedEntry, DicomInstance, DicomStudy, Operation
from app.models.events import DicomEvent
from app.routers._shared import (
    DICOM_STORAGE_DIR,
    _json_dumps,
)
from app.schemas.stow import StowResponse  # noqa: F401 — imported for OpenAPI schema registration
from app.services.dicom_engine import (
    build_store_response,
    dataset_to_dicom_json,
    extract_searchable_metadata,
    parse_dicom,
    store_instance,
    store_instance_bytes,
    validate_required_attributes,
    validate_searchable_attributes,
)
from app.services.dicom_parse_worker import ParsedPartRecord, parse_part_to_record
from app.services.multipart import (
    MultipartPart,
    iter_multipart_related,
    parse_multipart_related,
)
from app.services.upsert import (
    BulkUpsertOutcome,
    BulkUpsertRecord,
    _try_rmdir,
    _try_unlink,
    upsert_instance,
    upsert_instances_bulk,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ── DICOM Failure Reason Codes ─────────────────────────────────────
# Used in STOW-RS FailedSOPSequence responses (tag 0x00081197)
FAILURE_REASON_UNABLE_TO_PROCESS = 0xC000  # Processing failure
FAILURE_REASON_UID_MISMATCH = 0xC409  # StudyInstanceUID mismatch
FAILURE_REASON_INSTANCE_ALREADY_EXISTS = 45070  # Instance already exists (0xB00E hex)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Bulk Update — POST /studies/$bulkUpdate
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Tags in changeDataset that map to indexed columns on DicomInstance.
_BULK_UPDATE_TAG_TO_COLUMN: dict[str, str] = {
    "00100020": "patient_id",
    "00100010": "patient_name",
    "00080020": "study_date",
    "00080050": "accession_number",
    "00081030": "study_description",
    "00080060": "modality",
}

# Subset of the above that also exist as columns on DicomStudy.
_BULK_UPDATE_TAG_TO_STUDY_COLUMN: dict[str, str] = {
    "00100020": "patient_id",
}


class BulkUpdateRequest(BaseModel):
    """Request body for POST /v2/studies/$bulkUpdate."""

    study_instance_uids: list[str] | None = Field(default=None, alias="studyInstanceUids")
    change_dataset: dict | None = Field(default=None, alias="changeDataset")

    model_config = {"populate_by_name": True}


def _extract_scalar_value(tag_entry: dict) -> str | None:
    """Extract a simple string value from a DICOM JSON tag entry.

    Returns the first Value element as a string when it is a plain scalar,
    or None when the Value list is empty or the entry has no Value key.

    Note: Integer VR values (IS, DS) are not supported — returns None for
    non-string, non-PN values.  Only use this helper with string-valued DICOM
    tags (LO, LT, SH, DA, CS, etc.).
    """
    values = tag_entry.get("Value")
    if not values:
        return None
    first = values[0]
    if isinstance(first, str):
        return first
    # Person name object — return Alphabetic component
    if isinstance(first, dict):
        return first.get("Alphabetic")
    return None


def _apply_bulk_update_to_json(
    existing_json: dict[str, Any] | None,
    change_dataset: dict[str, Any],
) -> dict[str, Any]:
    """Merge a bulk-update ``changeDataset`` into an instance's ``dicom_json``.

    The merge preserves any ``BulkDataURI`` placeholders that already live on
    the existing record — the round-2 store path emits ``BulkDataURI`` for
    binary VRs instead of inline base64, and a bulk update of unrelated tags
    must not trample those references.  When the caller's ``change_dataset``
    explicitly supplies a tag entry (binary or otherwise) we honour it; when
    the existing entry already has a ``BulkDataURI`` and the change_dataset
    does not target that tag, the URI is preserved verbatim.
    """
    base = dict(existing_json or {})
    for tag, new_entry in change_dataset.items():
        if tag in base:
            current = base[tag]
            if isinstance(current, dict) and "BulkDataURI" in current and isinstance(new_entry, dict):
                # Caller intentionally overwrites a binary tag — keep the
                # provided ``BulkDataURI`` if present, otherwise drop the URI.
                merged = {**current, **new_entry}
                if "BulkDataURI" not in new_entry and "Value" in new_entry:
                    merged.pop("BulkDataURI", None)
                base[tag] = merged
                continue
        base[tag] = new_entry
    return base


# NOTE: This route must appear before POST /studies/{study_instance_uid} (STOW-RS)
# so FastAPI matches the literal "$bulkUpdate" segment before treating it as a path param.
@router.post(
    "/studies/$bulkUpdate",
    status_code=202,
    response_class=Response,
    responses=cast(
        dict[int | str, dict[str, Any]],
        {
            202: {
                "description": "Bulk update started. Location header points to the operation.",
                "headers": {
                    "Location": {
                        "description": "URL of the created operation",
                        "schema": {"type": "string"},
                    }
                },
            }
        },
    ),
)
async def bulk_update_studies(
    request: Request,
    body: BulkUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Bulk-update DICOM metadata across all instances in the specified studies.

    Merges ``changeDataset`` tags into each instance's ``dicom_json``.
    Also updates indexed columns for known tags.
    Creates a ``ChangeFeedEntry`` (state=replaced) per updated instance.
    Returns 202 with a Location header pointing to the created operation.

    Azure v2 behaviour:
    - Study UIDs that do not exist are silently skipped.
    - 400 if ``studyInstanceUids`` is absent/empty.
    - 400 if ``changeDataset`` is absent/empty.
    """
    if not body.study_instance_uids:
        raise HTTPException(status_code=400, detail="studyInstanceUids must not be empty")
    if not body.change_dataset:
        raise HTTPException(status_code=400, detail="changeDataset must not be empty")

    # Validate changeDataset structure: each tag entry must be a DICOM JSON object
    # containing a "vr" key (e.g. {"vr": "LO", "Value": [...]}).
    for tag_key, tag_value in body.change_dataset.items():
        if not isinstance(tag_value, dict) or "vr" not in tag_value:
            raise HTTPException(
                status_code=400,
                detail=f"changeDataset tag '{tag_key}' must be a DICOM JSON object with a 'vr' key",
            )

    operation_id = uuid.uuid4()

    # Create the operation record up front (will be committed with instance updates)
    operation = Operation(
        id=operation_id,
        type="bulk-update",
        status="running",
        percent_complete=0,
    )
    db.add(operation)

    updated_count = 0
    studies_updated_count = 0

    try:
        # Pre-compute the indexed-column overrides — these come straight
        # from ``change_dataset`` and don't vary per instance, so building
        # them once lets the executemany loop skip the per-row work.
        indexed_column_updates: dict[str, str | None] = {}
        for tag, column in _BULK_UPDATE_TAG_TO_COLUMN.items():
            if tag in body.change_dataset:
                indexed_column_updates[column] = _extract_scalar_value(
                    body.change_dataset[tag]
                )

        for study_uid in body.study_instance_uids:
            # Pull just the columns we need to merge — full ORM objects
            # would force expire-after-commit refreshes for thousands of
            # rows.  ``select(...c.id, ...c.dicom_json)`` stays in Core.
            result = await db.execute(
                select(
                    DicomInstance.__table__.c.id,
                    DicomInstance.__table__.c.dicom_json,
                    DicomInstance.__table__.c.series_instance_uid,
                    DicomInstance.__table__.c.sop_instance_uid,
                ).where(DicomInstance.__table__.c.study_instance_uid == study_uid)
            )
            rows = result.all()
            if not rows:
                continue

            studies_updated_count += 1
            now = datetime.now(timezone.utc)

            # Build the executemany payload for the dicom_instances UPDATE.
            # Each row gets its own merged ``dicom_json`` (instance-scoped)
            # plus the shared ``indexed_column_updates`` for searchable cols.
            dicom_instance_updates = []
            change_feed_rows: list[dict[str, Any]] = []
            for row in rows:
                merged_json = _apply_bulk_update_to_json(row.dicom_json, body.change_dataset)
                update_row: dict[str, Any] = {
                    "_id": row.id,
                    "dicom_json": merged_json,
                    "updated_at": now,
                    **indexed_column_updates,
                }
                dicom_instance_updates.append(update_row)
                change_feed_rows.append(
                    {
                        "study_instance_uid": study_uid,
                        "series_instance_uid": row.series_instance_uid,
                        "sop_instance_uid": row.sop_instance_uid,
                        "action": "update",
                        "state": "replaced",
                        "timestamp": now,
                        "dicom_metadata": merged_json,
                    }
                )

            # One executemany UPDATE for every instance in this study.  The
            # `_id` bind parameter feeds the WHERE clause; the rest become
            # the SET targets.  This replaces N round-trips per study with
            # one regardless of instance count.
            update_stmt = (
                sql_update(DicomInstance.__table__)
                .where(DicomInstance.__table__.c.id == bindparam("_id"))
                .values(
                    {
                        "dicom_json": bindparam("dicom_json"),
                        "updated_at": bindparam("updated_at"),
                        **{col: bindparam(col) for col in indexed_column_updates},
                    }
                )
            )
            await db.execute(update_stmt, dicom_instance_updates)
            updated_count += len(dicom_instance_updates)

            # Bulk INSERT change_feed rows — one per updated instance.  No
            # RETURNING needed here; bulk_update doesn't fan out events.
            await db.execute(
                dialect_insert(db, ChangeFeedEntry.__table__).values(change_feed_rows)
            )

            # Update (or create) the DicomStudy row for this study.  The
            # study-level columns are a much smaller set, so a single ORM
            # round-trip per study is fine.
            study_result = await db.execute(
                select(DicomStudy).where(DicomStudy.study_instance_uid == study_uid)
            )
            dicom_study = study_result.scalar_one_or_none()
            if dicom_study is None:
                dicom_study = DicomStudy(study_instance_uid=study_uid)
                db.add(dicom_study)
            for tag, column in _BULK_UPDATE_TAG_TO_STUDY_COLUMN.items():
                if tag in body.change_dataset:
                    new_value = _extract_scalar_value(body.change_dataset[tag])
                    setattr(dicom_study, column, new_value)

        # Mark operation as succeeded
        operation.status = "succeeded"
        operation.percent_complete = 100
        operation.results = {
            "studiesUpdated": studies_updated_count,
            "instancesUpdated": updated_count,
        }

        await db.commit()
    except Exception:
        await db.rollback()
        operation.status = "failed"
        try:
            db.add(operation)  # re-attach after rollback cleared pending objects
            await db.commit()  # best-effort to save the failed status
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Bulk update failed")

    location = str(request.base_url).rstrip("/") + f"/v2/operations/{operation_id}"
    return Response(
        status_code=202,
        headers={"Location": location},
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STOW-RS — Shared per-instance processing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class StowResult:
    """Collected results from processing a batch of DICOM multipart parts.

    ``written_files`` tracks the absolute path of every ``.dcm`` file
    written to disk during this request so the orchestrator can roll
    the filesystem back if the DB transaction is later rolled back.
    Without this, a partial-failure rollback leaves orphan files on
    disk that have no DB row and no change-feed entry.

    ``post_commit_unlinks`` holds old-file paths from successful PUT
    replacements where the new path differs from the old one.  These
    can only be unlinked AFTER ``db.commit()`` succeeds — doing it
    sooner would corrupt the still-live old instance if the bulk
    INSERT later raises.  The route handler drains this list once
    the commit returns.
    """

    stored: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    has_warnings: bool = False
    effective_study_uid: str | None = None
    # Pairs of (instance, feed_entry) for post-commit event publishing.
    # The instance/feed_entry are lightweight facade objects holding only
    # the fields ``DicomEvent.from_instance_created`` reads; we no longer
    # carry full ORM objects across the request boundary because the bulk
    # path inserts via Core and never materialises them.
    instances_to_publish: list[tuple] = field(default_factory=list)
    # File paths written to disk during this request (for rollback cleanup)
    written_files: list[str] = field(default_factory=list)
    # Old file paths to unlink AFTER db.commit() succeeds (PUT replacements
    # whose new path differs from the old path).
    post_commit_unlinks: list[str] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class _PublishableInstance:
    """Subset of :class:`DicomInstance` fields the event payload reads.

    The bulk INSERT path never materialises full ORM objects, so we hand
    a tiny attribute-access facade to ``DicomEvent.from_instance_created``
    instead.  Frozen + slotted to keep the per-instance allocation cheap.
    """

    study_instance_uid: str
    series_instance_uid: str
    sop_instance_uid: str


@dataclass(slots=True, frozen=True)
class _PublishableFeedEntry:
    """Subset of :class:`ChangeFeedEntry` the event payload reads."""

    sequence: int


@dataclass(slots=True)
class _StagedRecord:
    """A parsed part that has passed pre-validation and is ready for DB persistence.

    ``file_path`` is the absolute path of the freshly-written ``.dcm``
    file; ``file_size`` is the byte length the bulk insert needs.
    """

    record: ParsedPartRecord
    part_data: bytes
    file_path: str
    file_size: int


# ── Worker dispatch ────────────────────────────────────────────────


async def _parse_in_pool(
    pool: ProcessPoolExecutor | None,
    data: bytes,
) -> ParsedPartRecord:
    """Run :func:`parse_part_to_record` in the parse pool (or inline if disabled).

    When the pool is None we fall back to ``loop.run_in_executor(None, ...)``
    which uses the default thread executor.  This keeps the asyncio event
    loop responsive for unit tests that intentionally disable the pool
    via ``DICOM_PARSE_WORKERS=0`` to avoid spinning up subprocesses.
    """
    loop = asyncio.get_running_loop()
    if pool is None:
        return await loop.run_in_executor(None, parse_part_to_record, data)
    return await loop.run_in_executor(pool, parse_part_to_record, data)


async def _gather_parts_with_records(
    source: AsyncIterator[MultipartPart] | Iterable[MultipartPart],
    pool: ProcessPoolExecutor | None,
) -> list[tuple[bytes, ParsedPartRecord]]:
    """Drain a multipart source, dispatching parsing to the pool with bounded concurrency.

    Returns ordered ``(part_bytes, record)`` pairs so the caller can do
    DB / filesystem work on the main asyncio task in the same order the
    parts arrived on the wire.

    Concurrency is bounded by :data:`app.config.DICOM_PARSE_INFLIGHT` so
    large STOW requests don't keep an unbounded number of parts in
    flight at once.
    """
    sem = asyncio.Semaphore(DICOM_PARSE_INFLIGHT)

    async def _parse_one(data: bytes) -> tuple[bytes, ParsedPartRecord]:
        async with sem:
            record = await _parse_in_pool(pool, data)
        return data, record

    pending: list[asyncio.Task[tuple[bytes, ParsedPartRecord]]] = []

    if hasattr(source, "__aiter__"):
        async for part in source:  # type: ignore[union-attr]
            pending.append(asyncio.create_task(_parse_one(part.data)))
    else:
        for part in source:  # type: ignore[assignment]
            pending.append(asyncio.create_task(_parse_one(part.data)))

    if not pending:
        return []
    return list(await asyncio.gather(*pending))


# ── File-rollback helper ───────────────────────────────────────────


async def _unlink_files(paths: Iterable[str]) -> None:
    """Best-effort unlink of ``.dcm`` files written during a now-rolled-back STOW.

    Each unlink is dispatched to a worker thread (file-system metadata
    operations release the GIL) and ``FileNotFoundError`` is swallowed
    so partially-completed cleanups stay idempotent.
    """
    async def _one(p: str) -> None:
        try:
            await asyncio.to_thread(os.remove, p)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("Failed to unlink orphan DICOM file %s: %s", p, e)

    await asyncio.gather(*(_one(p) for p in paths))


async def _post_commit_cleanup(paths: Iterable[str]) -> None:
    """Unlink replaced PUT files and collapse empty ancestor directories.

    Runs AFTER the STOW transaction has committed.  At this point any
    rollback risk is gone, so it's safe to remove the old DCM blob and
    its now-orphan instance/series/study directories.  ``_try_rmdir``
    is atomic and a no-op for non-empty directories, so concurrent
    sibling unlinks don't race each other.
    """
    for p in paths:
        old_path = Path(p)
        await asyncio.to_thread(_try_unlink, old_path)
        instance_dir = old_path.parent
        series_dir = instance_dir.parent
        study_dir = series_dir.parent
        await asyncio.to_thread(_try_rmdir, instance_dir)
        await asyncio.to_thread(_try_rmdir, series_dir)
        await asyncio.to_thread(_try_rmdir, study_dir)


# ── Persistence (record → DB row + .dcm file) ──────────────────────


async def _stage_record(
    *,
    record: ParsedPartRecord,
    part_data: bytes,
    upsert: bool,
    study_instance_uid: str | None,
    result: StowResult,
) -> _StagedRecord | None:
    """Validate one parsed part, write its file, and stage it for bulk persistence.

    All early-exit paths (worker-side parse failure, study-UID mismatch,
    per-part disk write failure) append the appropriate failure / warning
    entry to ``result`` and return ``None``.  When the part survives both
    validation and the disk write, returns a :class:`_StagedRecord` that
    the bulk INSERT picks up later.

    A failed disk write is treated as a 0xC000 partial-success failure for
    that part only — it does NOT abort the batch.  Sibling parts that
    already passed validation continue on into the bulk INSERT.
    """
    # Worker-side parse / required-attribute failure
    if not record.ok:
        if record.required_errors:
            result.failures.append(
                {
                    "00081150": {"vr": "UI", "Value": [record.sop_class_uid]},
                    "00081155": {"vr": "UI", "Value": [record.sop_uid]},
                    "00081197": {"vr": "US", "Value": [FAILURE_REASON_UNABLE_TO_PROCESS]},
                }
            )
        else:
            result.failures.append(
                {
                    "00081197": {"vr": "US", "Value": [FAILURE_REASON_UNABLE_TO_PROCESS]},
                    "00081196": {
                        "vr": "LO",
                        "Value": [record.parse_error or "parse failed"],
                    },
                }
            )
        return None

    # POST-only: searchable warnings → 202 with WarningReason (Azure v2)
    if not upsert and record.searchable_warnings:
        result.has_warnings = True
        result.warnings.append({})
        logger.warning(
            "Searchable attribute warnings for %s: %s",
            record.sop_uid,
            record.searchable_warnings,
        )

    # Study-UID-in-URL must match every instance's StudyInstanceUID
    if study_instance_uid and record.study_uid != study_instance_uid:
        result.failures.append(
            {
                "00081150": {"vr": "UI", "Value": [record.sop_class_uid]},
                "00081155": {"vr": "UI", "Value": [record.sop_uid]},
                "00081197": {"vr": "US", "Value": [FAILURE_REASON_UID_MISMATCH]},
            }
        )
        return None

    if result.effective_study_uid is None:
        result.effective_study_uid = record.study_uid

    # ``store_instance_bytes`` is referenced via this module so the in-loop
    # disk-failure regression test can monkey-patch it.
    try:
        file_path = await store_instance_bytes(
            part_data, record.study_uid, record.series_uid, record.sop_uid
        )
    except Exception as e:
        # Disk-write failure for this part is partial-success: the batch
        # keeps going, this part lands in FailedSOPSequence with 0xC000.
        result.failures.append(
            {
                "00081150": {"vr": "UI", "Value": [record.sop_class_uid]},
                "00081155": {"vr": "UI", "Value": [record.sop_uid]},
                "00081197": {"vr": "US", "Value": [FAILURE_REASON_UNABLE_TO_PROCESS]},
                "00081196": {"vr": "LO", "Value": [str(e)]},
            }
        )
        result.has_warnings = True
        return None

    result.written_files.append(file_path)
    return _StagedRecord(
        record=record,
        part_data=part_data,
        file_path=file_path,
        file_size=len(part_data),
    )


def _build_dicom_instance_row(staged: _StagedRecord, *, now: datetime) -> dict[str, Any]:
    """Build the column-dict for one bulk-INSERT row into ``dicom_instances``.

    Pulled into a helper so the POST and PUT bulk paths share exactly the
    same row shape, avoiding accidental column drift between the two.
    """
    record = staged.record
    return {
        "id": uuid.uuid4(),
        "study_instance_uid": record.study_uid,
        "series_instance_uid": record.series_uid,
        "sop_instance_uid": record.sop_uid,
        "sop_class_uid": record.sop_class_uid,
        "transfer_syntax_uid": record.transfer_syntax_uid,
        "patient_id": record.searchable.get("patient_id"),
        "patient_name": record.searchable.get("patient_name"),
        "study_date": record.searchable.get("study_date"),
        "study_time": record.searchable.get("study_time"),
        "accession_number": record.searchable.get("accession_number"),
        "study_description": record.searchable.get("study_description"),
        "modality": record.searchable.get("modality"),
        "series_description": record.searchable.get("series_description"),
        "series_number": record.searchable.get("series_number"),
        "instance_number": record.searchable.get("instance_number"),
        "referring_physician_name": record.searchable.get("referring_physician_name"),
        "dicom_json": record.dicom_json,
        "file_path": staged.file_path,
        "file_size": staged.file_size,
        "created_at": now,
        "updated_at": now,
    }


def _stored_entry(record: ParsedPartRecord) -> dict:
    """Build one ``ReferencedSOPSequence`` entry for the STOW response."""
    return {
        "00081150": {"vr": "UI", "Value": [record.sop_class_uid]},
        "00081155": {"vr": "UI", "Value": [record.sop_uid]},
        "00081190": {
            "vr": "UR",
            "Value": [
                f"/v2/studies/{record.study_uid}/series/{record.series_uid}"
                f"/instances/{record.sop_uid}"
            ],
        },
    }


async def _bulk_insert_post(staged: list[_StagedRecord], db: AsyncSession) -> set[str]:
    """Bulk-INSERT new ``DicomInstance`` rows for the POST path.

    Uses ``ON CONFLICT (sop_instance_uid) DO NOTHING RETURNING`` so a
    duplicate SOP UID from a concurrent or repeated submission is handled
    atomically — the duplicate row is silently skipped and the caller
    learns about it via the absence of the SOP UID from the returned set.
    Returns the set of SOP UIDs that were freshly inserted (winners of
    the dedup race).
    """
    if not staged:
        return set()

    now = datetime.now(timezone.utc)
    rows = [_build_dicom_instance_row(s, now=now) for s in staged]

    insert_stmt = (
        dialect_insert(db, DicomInstance.__table__)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=[DicomInstance.__table__.c.sop_instance_uid],
        )
        .returning(DicomInstance.__table__.c.sop_instance_uid)
    )
    inserted_rows = (await db.execute(insert_stmt)).all()
    return {row[0] for row in inserted_rows}


async def _bulk_insert_change_feed(
    db: AsyncSession,
    feed_rows: list[dict[str, Any]],
) -> dict[str, int]:
    """Bulk-INSERT change feed rows; return {sop_uid: sequence}."""
    if not feed_rows:
        return {}

    insert_stmt = (
        dialect_insert(db, ChangeFeedEntry.__table__)
        .values(feed_rows)
        .returning(
            ChangeFeedEntry.__table__.c.sequence,
            ChangeFeedEntry.__table__.c.sop_instance_uid,
        )
    )
    rows = (await db.execute(insert_stmt)).all()
    return {row[1]: row[0] for row in rows}


async def _persist_post(staged: list[_StagedRecord], db: AsyncSession, result: StowResult) -> None:
    """Persist a batch of parsed records on the POST (create-only) path.

    One bulk INSERT to ``dicom_instances`` with ``ON CONFLICT DO NOTHING``,
    one bulk INSERT to ``change_feed`` for the dedup winners.  Losers
    (returning SOP UIDs that were already present) become 45070 warnings.
    """
    if not staged:
        return

    inserted_sops = await _bulk_insert_post(staged, db)

    feed_rows: list[dict[str, Any]] = []
    winners: list[_StagedRecord] = []
    now = datetime.now(timezone.utc)
    for s in staged:
        if s.record.sop_uid in inserted_sops:
            winners.append(s)
            feed_rows.append(
                {
                    "study_instance_uid": s.record.study_uid,
                    "series_instance_uid": s.record.series_uid,
                    "sop_instance_uid": s.record.sop_uid,
                    "action": "create",
                    "state": "current",
                    "timestamp": now,
                    "dicom_metadata": {},
                }
            )
        else:
            # Dedup loser — Azure v2 returns 45070 as a warning, not a hard failure.
            result.failures.append(
                {
                    "00081150": {"vr": "UI", "Value": [s.record.sop_class_uid]},
                    "00081155": {"vr": "UI", "Value": [s.record.sop_uid]},
                    "00081197": {
                        "vr": "US",
                        "Value": [FAILURE_REASON_INSTANCE_ALREADY_EXISTS],
                    },
                }
            )

    if not winners:
        return

    seq_by_sop = await _bulk_insert_change_feed(db, feed_rows)

    for s in winners:
        sop_uid = s.record.sop_uid
        result.stored.append(_stored_entry(s.record))
        sequence = seq_by_sop.get(sop_uid)
        if sequence is None:
            # RETURNING did not include this row — should never happen on
            # PG/SQLite for a row we just inserted, but guard anyway.
            logger.warning(
                "change_feed sequence missing for inserted SOP %s; event skipped",
                sop_uid,
            )
            continue
        result.instances_to_publish.append(
            (
                _PublishableInstance(
                    study_instance_uid=s.record.study_uid,
                    series_instance_uid=s.record.series_uid,
                    sop_instance_uid=sop_uid,
                ),
                _PublishableFeedEntry(sequence=sequence),
            )
        )


async def _persist_put(staged: list[_StagedRecord], db: AsyncSession, result: StowResult) -> None:
    """Persist a batch of parsed records on the PUT (upsert) path.

    Single ``SELECT … WHERE sop IN (…)`` learns which rows are replaces
    (and captures their old file paths).  Single bulk
    ``INSERT … ON CONFLICT DO UPDATE`` lands every row.  One bulk UPDATE
    flips previously-current change_feed rows for replaced SOPs to
    "replaced", then one bulk INSERT lands the new "current" feed rows
    and yields their sequence numbers.
    """
    if not staged:
        return

    sops = [s.record.sop_uid for s in staged]
    existing_q = await db.execute(
        select(DicomInstance.sop_instance_uid, DicomInstance.file_path).where(
            DicomInstance.sop_instance_uid.in_(sops)
        )
    )
    existing_map: dict[str, str] = {row[0]: row[1] for row in existing_q.all()}

    now = datetime.now(timezone.utc)
    rows = [_build_dicom_instance_row(s, now=now) for s in staged]

    insert_stmt = dialect_insert(db, DicomInstance.__table__).values(rows)
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

    # Mark all previously-current change_feed entries for replaced SOPs
    # as "replaced" in a single bulk UPDATE — replaces the per-row
    # ``_mark_previous_feed_entries`` call we used to make in the loop.
    if existing_map:
        await db.execute(
            sql_update(ChangeFeedEntry)
            .where(
                and_(
                    ChangeFeedEntry.sop_instance_uid.in_(existing_map.keys()),
                    ChangeFeedEntry.state == "current",
                )
            )
            .values(state="replaced")
        )

    feed_rows = [
        {
            "study_instance_uid": s.record.study_uid,
            "series_instance_uid": s.record.series_uid,
            "sop_instance_uid": s.record.sop_uid,
            "action": "replaced" if s.record.sop_uid in existing_map else "create",
            "state": "current",
            "timestamp": now,
            "dicom_metadata": {},
        }
        for s in staged
    ]
    seq_by_sop = await _bulk_insert_change_feed(db, feed_rows)

    # Stage old-file unlinks for after the commit succeeds.  Doing it
    # before commit would corrupt the still-live old instance if a later
    # constraint failure rolls the bulk insert back.
    for s in staged:
        sop_uid = s.record.sop_uid
        old_path = existing_map.get(sop_uid)
        if old_path is not None and old_path != s.file_path:
            result.post_commit_unlinks.append(old_path)

        result.stored.append(_stored_entry(s.record))
        sequence = seq_by_sop.get(sop_uid)
        if sequence is None:
            logger.warning(
                "change_feed sequence missing for upserted SOP %s; event skipped",
                sop_uid,
            )
            continue
        result.instances_to_publish.append(
            (
                _PublishableInstance(
                    study_instance_uid=s.record.study_uid,
                    series_instance_uid=s.record.series_uid,
                    sop_instance_uid=sop_uid,
                ),
                _PublishableFeedEntry(sequence=sequence),
            )
        )


# ── High-level orchestration ──────────────────────────────────────


async def _process_stow_records(
    records: list[tuple[bytes, ParsedPartRecord]],
    db: AsyncSession,
    *,
    study_instance_uid: str | None = None,
    upsert: bool = False,
) -> StowResult:
    """Process pre-parsed records into DB rows + ``.dcm`` files on disk.

    Two phases:

    * **Stage** — per-part validation + disk write.  Per-part disk write
      failures land as 0xC000 entries in ``result.failures`` and do NOT
      abort the batch (true partial success).  Worker-side parse errors
      and study-UID mismatches land as conventional failure entries.
    * **Persist** — one bulk ``INSERT … ON CONFLICT`` for every staged
      record, plus one bulk INSERT to ``change_feed`` for the survivors.
      A constraint failure here aborts the entire batch (rollback +
      file-cleanup) since at this point the only failure mode is real
      data corruption (FK violation, malformed JSON).
    """
    result = StowResult(effective_study_uid=study_instance_uid)

    staged: list[_StagedRecord] = []
    for part_data, record in records:
        rp = await _stage_record(
            record=record,
            part_data=part_data,
            upsert=upsert,
            study_instance_uid=study_instance_uid,
            result=result,
        )
        if rp is not None:
            staged.append(rp)

    if not staged:
        return result

    try:
        if upsert:
            await _persist_put(staged, db, result)
        else:
            await _persist_post(staged, db, result)
    except Exception as e:
        # Bulk INSERT failed — corruption-class issue.  Roll back the
        # transaction and unlink every file we wrote in this request so
        # disk and DB stay in sync.  A single 0xC000 envelope failure
        # tells the client the batch as a whole did not commit.
        await db.rollback()
        files_to_clean = list(result.written_files)
        result.stored.clear()
        result.instances_to_publish.clear()
        result.written_files.clear()
        result.post_commit_unlinks.clear()
        if files_to_clean:
            await _unlink_files(files_to_clean)
        result.failures.append(
            {
                "00081197": {"vr": "US", "Value": [0xC000]},
                "00081196": {"vr": "LO", "Value": [str(e)]},
            }
        )
        result.has_warnings = True

    return result


async def _process_stow_instances(
    parts: list,
    db: AsyncSession,
    *,
    study_instance_uid: str | None = None,
    upsert: bool = False,
) -> StowResult:
    """Eager-list compatibility shim for callers that already have parsed parts.

    The streaming STOW route calls :func:`_process_stow_records` directly;
    this wrapper keeps a path open for synthesis tests and other places
    that build a list of :class:`MultipartPart` up front.  Parsing runs
    inline (no process pool) since callers supplying a list typically
    don't have a parse pool to hand.
    """
    records: list[tuple[bytes, ParsedPartRecord]] = []
    for part in parts:
        record = await _parse_in_pool(None, part.data)
        records.append((part.data, record))
    return await _process_stow_records(
        records,
        db,
        study_instance_uid=study_instance_uid,
        upsert=upsert,
    )


# ── Background event publishing ────────────────────────────────────


async def _publish_stow_events(
    instances_to_publish: list[tuple],
    service_url: str,
) -> None:
    """Publish DicomImageCreated events for successfully stored instances.

    Runs as a background task scheduled by :func:`_spawn_publish` so that
    the STOW response is returned to the client as soon as the database
    transaction commits.  Errors are logged but never re-raised so they
    can't take down the lifespan-shutdown drain.

    The events are sent as a single :meth:`EventManager.publish_batch`
    call — providers that support batching (e.g.
    :class:`InMemoryEventProvider`, :class:`FileEventProvider`,
    :class:`AzureStorageQueueProvider`, :class:`WebhookEventProvider`)
    handle the whole batch in one shot.

    On a successful ``publish_batch`` we stamp the matching
    ``change_feed.event_published_at`` rows so the startup outbox sweeper
    skips them on the next boot.  The stamp uses a fresh
    :func:`outbox_session_factory` session because the originating
    request session is already closed by the time this background task
    runs.
    """
    if not instances_to_publish:
        return
    events: list[DicomEvent] = []
    published_sequences: list[int] = []
    for instance, feed_entry in instances_to_publish:
        events.append(
            DicomEvent.from_instance_created(
                study_uid=instance.study_instance_uid,
                series_uid=instance.series_instance_uid,
                instance_uid=instance.sop_instance_uid,
                sequence_number=feed_entry.sequence,
                service_url=service_url,
            )
        )
        if feed_entry.sequence is not None:
            published_sequences.append(feed_entry.sequence)
    try:
        # Late import to avoid a circular dependency at module load time
        # (stow.py is included via app.routers, which is imported from
        # main.py before the event manager is initialised).
        from app.dependencies import get_event_manager

        event_manager = get_event_manager()
        await event_manager.publish_batch(events)
    except Exception as e:
        logger.error("Failed to publish STOW events for batch (n=%d): %s", len(events), e)
        return

    # Outbox stamp: only after publish_batch succeeds do we mark these
    # change_feed rows as published.  A hard process kill between this
    # point and the UPDATE leaves the rows unstamped, which the startup
    # sweeper will replay on next boot — at-least-once delivery.
    if not published_sequences:
        return
    try:
        await _stamp_event_published(published_sequences)
    except Exception as e:
        # Stamping failure is non-fatal — the events were delivered, the
        # sweeper will simply replay them once.  Consumers must already
        # be idempotent on (study, series, sop) to handle that case.
        logger.warning(
            "Outbox stamp failed for %d sequence(s); sweeper will replay: %s",
            len(published_sequences),
            e,
        )


async def _stamp_event_published(sequences: list[int]) -> None:
    """Mark ``change_feed.event_published_at = now()`` for the given sequences.

    Opens a fresh :class:`AsyncSession` via :func:`outbox_session_factory`
    so the work is independent of the request-scoped session that
    originated the publish.  Issues a single bulk ``UPDATE`` and commits.
    """
    from app.database import outbox_session_factory

    now = datetime.now(timezone.utc)
    async with outbox_session_factory() as session:
        await session.execute(
            sql_update(ChangeFeedEntry)
            .where(ChangeFeedEntry.sequence.in_(sequences))
            .values(event_published_at=now)
        )
        await session.commit()


def _spawn_publish(request: Request, instances_to_publish: list[tuple]) -> None:
    """Schedule fire-and-forget event publishing as a tracked asyncio task.

    The task is appended to ``app.state.pending_event_tasks`` (a set
    initialised in :func:`main.lifespan`) so that lifespan shutdown can
    drain in-flight publishes before exiting.  ``add_done_callback``
    removes finished tasks from the set so it doesn't grow unbounded.

    Captures ``service_url`` *before* spawning the task — ``request``
    itself shouldn't be referenced by a task that outlives the request
    lifecycle.
    """
    if not instances_to_publish:
        return

    service_url = str(request.base_url).rstrip("/")
    pending: set[asyncio.Task] | None = getattr(request.app.state, "pending_event_tasks", None)
    if pending is None:
        # Defensive: if lifespan didn't initialise the set (e.g. a test
        # FastAPI app), publish inline rather than dropping events.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.create_task(_publish_stow_events(instances_to_publish, service_url))
        return

    task = asyncio.create_task(_publish_stow_events(instances_to_publish, service_url))
    pending.add(task)
    task.add_done_callback(pending.discard)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STOW-RS — Store Instances
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


_STOW_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "All instances stored successfully",
        "content": {
            "application/dicom+json": {"schema": {"$ref": "#/components/schemas/StowResponse"}}
        },
    },
    202: {
        "description": "Instances stored with warnings or partial failure",
        "content": {
            "application/dicom+json": {"schema": {"$ref": "#/components/schemas/StowResponse"}}
        },
    },
    409: {
        "description": "All instances failed to store",
        "content": {
            "application/dicom+json": {"schema": {"$ref": "#/components/schemas/StowResponse"}}
        },
    },
}


def _get_parse_pool(request: Request) -> ProcessPoolExecutor | None:
    """Return the app-scoped parse pool (or None when disabled / in tests)."""
    return getattr(request.app.state, "parse_pool", None)


async def _consume_parts_into_records(
    request: Request, content_type: str
) -> list[tuple[bytes, ParsedPartRecord]]:
    """Stream the request body, parsing parts in the pool with bounded concurrency.

    All pydicom parsing happens in :data:`request.app.state.parse_pool`
    (a :class:`ProcessPoolExecutor`).  This keeps the asyncio event loop
    free to keep draining the request socket and to let the database
    engine work on the side, even while we're parsing thousands of
    instances per request.
    """
    pool = _get_parse_pool(request)
    parts_iter = iter_multipart_related(request.stream(), content_type)
    return await _gather_parts_with_records(parts_iter, pool)


@router.post("/studies", response_class=Response, responses=_STOW_RESPONSES)
@router.post("/studies/{study_instance_uid}", response_class=Response, responses=_STOW_RESPONSES)
async def stow_rs(
    request: Request,
    study_instance_uid: Optional[str] = None,
    content_type: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    """
    STOW-RS: Store DICOM instances.

    Accepts multipart/related with application/dicom parts.
    Azure v2 behavior: only fails on missing required attributes;
    searchable attribute issues return HTTP 202 with warnings.
    """
    records = await _consume_parts_into_records(request, content_type)

    result = await _process_stow_records(
        records, db, study_instance_uid=study_instance_uid, upsert=False
    )

    try:
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        # Files written for parts that survived per-part validation are
        # now orphans: their DB rows just got rolled back.  Remove them
        # before re-raising so disk and DB stay in sync.
        if result.written_files:
            await _unlink_files(result.written_files)
            result.written_files.clear()
            result.instances_to_publish.clear()
            result.post_commit_unlinks.clear()
        raise

    if result.post_commit_unlinks:
        await _post_commit_cleanup(result.post_commit_unlinks)
        result.post_commit_unlinks.clear()

    _spawn_publish(request, result.instances_to_publish)

    effective_study_uid = result.effective_study_uid or "unknown"
    response_body = build_store_response(
        effective_study_uid, result.stored, result.warnings, result.failures
    )

    # Azure v2: 200 if all succeed, 202 if warnings or partial success, 409 if all fail.
    # 45070 (0xB00E) is a DICOM *warning* code — an all-duplicate POST must return 202,
    # not 409. Only hard failures (0xC-range codes) should drive a 409.
    hard_failures = [
        f
        for f in result.failures
        if f.get("00081197", {}).get("Value", [None])[0] != FAILURE_REASON_INSTANCE_ALREADY_EXISTS
    ]
    if hard_failures and not result.stored:
        status_code = 409
    elif result.has_warnings or result.warnings or result.failures:
        status_code = 202
    else:
        status_code = 200

    return Response(
        content=_json_dumps(response_body),
        status_code=status_code,
        media_type="application/dicom+json",
    )


@router.put("/studies", response_class=Response, responses=_STOW_RESPONSES)
@router.put("/studies/{study_instance_uid}", response_class=Response, responses=_STOW_RESPONSES)
async def stow_rs_put(
    request: Request,
    study_instance_uid: Optional[str] = None,
    content_type: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    """
    STOW-RS with PUT (Upsert): Store or update DICOM instances with optional expiry.

    Supports expiry headers for automatic study deletion:
    - msdicom-expiry-time-milliseconds: Time until expiry in milliseconds
    - msdicom-expiry-option: Must be "RelativeToNow"
    - msdicom-expiry-level: Must be "Study"

    Azure v2 behavior: Unlike POST, PUT does not emit 45070 warnings for upsert.
    """
    expiry_ms = request.headers.get("msdicom-expiry-time-milliseconds")
    expiry_option = request.headers.get("msdicom-expiry-option")
    expiry_level = request.headers.get("msdicom-expiry-level")

    expires_at = None
    if expiry_ms and expiry_option == "RelativeToNow" and expiry_level == "Study":
        expiry_seconds = int(expiry_ms) / 1000
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expiry_seconds)

    records = await _consume_parts_into_records(request, content_type)

    result = await _process_stow_records(
        records, db, study_instance_uid=study_instance_uid, upsert=True
    )

    # Create/update DicomStudy with expiry (PUT-only feature)
    if result.effective_study_uid and expires_at:
        study_result = await db.execute(
            select(DicomStudy).where(DicomStudy.study_instance_uid == result.effective_study_uid)
        )
        study = study_result.scalar_one_or_none()
        if not study:
            study = DicomStudy(study_instance_uid=result.effective_study_uid)
            db.add(study)
        study.expires_at = expires_at

    try:
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        if result.written_files:
            await _unlink_files(result.written_files)
            result.written_files.clear()
            result.instances_to_publish.clear()
            result.post_commit_unlinks.clear()
        raise

    if result.post_commit_unlinks:
        await _post_commit_cleanup(result.post_commit_unlinks)
        result.post_commit_unlinks.clear()

    _spawn_publish(request, result.instances_to_publish)

    effective_study_uid = result.effective_study_uid or "unknown"

    # No warnings for upsert — Azure v2 behavior
    response_body = build_store_response(effective_study_uid, result.stored, [], result.failures)

    if result.failures and not result.stored:
        status_code = 409
    elif result.has_warnings:
        status_code = 202
    else:
        status_code = 200

    return Response(
        content=_json_dumps(response_body),
        status_code=status_code,
        media_type="application/dicom+json",
    )
