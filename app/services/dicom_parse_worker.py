"""Subprocess-friendly DICOM parsing worker.

This module is invoked from a :class:`concurrent.futures.ProcessPoolExecutor`
to push pydicom parsing and DICOM JSON conversion off the main asyncio
event loop and onto every available CPU core.

Design constraints:

* The worker function and everything it returns **must be picklable**
  so the executor can serialise the result back across the process
  boundary.  Concretely, that means we never return a :class:`pydicom.Dataset`
  (those carry heavy internal state); we return a plain
  :class:`ParsedPartRecord` dataclass of basic Python types only.
* Imports here must be cheap (re-importing them per worker on
  ``spawn`` start-up adds latency to the first task).  In particular,
  we deliberately do **not** import FastAPI or SQLAlchemy here — only
  the DICOM helpers we need.
* The worker catches every exception and converts it to a record with
  ``ok=False``; raising into the executor would surface as a
  ``BrokenProcessPool`` and tear down the whole pool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.dicom_engine import (
    dataset_to_dicom_json,
    extract_searchable_metadata,
    parse_dicom,
    validate_required_attributes,
    validate_searchable_attributes,
)


@dataclass(slots=True)
class ParsedPartRecord:
    """Result of parsing one multipart DICOM part in a worker process.

    All fields are plain pickle-friendly types so the record can cross
    a process boundary cheaply.

    When ``ok`` is False the caller should record a failure entry; the
    other UID/metadata fields are best-effort and may be empty strings.
    """

    ok: bool
    sop_class_uid: str = ""
    transfer_syntax_uid: str | None = None
    study_uid: str = ""
    series_uid: str = ""
    sop_uid: str = ""
    dicom_json: dict[str, Any] = field(default_factory=dict)
    searchable: dict[str, Any] = field(default_factory=dict)
    required_errors: list[str] = field(default_factory=list)
    searchable_warnings: list[str] = field(default_factory=list)
    parse_error: str | None = None


def parse_part_to_record(data: bytes) -> ParsedPartRecord:
    """Parse one DICOM part's bytes and return a picklable digest.

    Runs in a subprocess (or, in tests, the calling thread).  Performs:

    1. ``pydicom.dcmread(BytesIO(data))`` to construct a Dataset.
    2. Required-attribute validation — fatal for both POST and PUT;
       the caller maps these into a STOW failure entry.
    3. Searchable-attribute validation — non-fatal (Azure v2 warning).
    4. UID extraction (study/series/SOP, SOP Class, transfer syntax).
    5. DICOM JSON conversion + flat searchable-metadata extraction.

    All steps are guarded.  Any unexpected exception turns into
    ``ParsedPartRecord(ok=False, parse_error=...)`` so that the caller
    can degrade the affected part to a failure rather than crashing
    the worker.
    """
    try:
        ds = parse_dicom(data)
    except Exception as e:
        return ParsedPartRecord(ok=False, parse_error=f"parse_dicom failed: {e}")

    try:
        required_errors = validate_required_attributes(ds)
        if required_errors:
            sop_uid = str(getattr(ds, "SOPInstanceUID", "")) or ""
            sop_class_uid = str(getattr(ds, "SOPClassUID", "")) or ""
            return ParsedPartRecord(
                ok=False,
                sop_class_uid=sop_class_uid,
                sop_uid=sop_uid,
                required_errors=required_errors,
            )

        searchable_warnings = validate_searchable_attributes(ds)

        sop_uid = str(ds.SOPInstanceUID)
        study_uid = str(ds.StudyInstanceUID)
        series_uid = str(ds.SeriesInstanceUID)
        sop_class_uid = str(getattr(ds, "SOPClassUID", ""))
        transfer_syntax_uid: str | None = None
        if hasattr(ds, "file_meta"):
            ts = getattr(ds.file_meta, "TransferSyntaxUID", None)
            if ts is not None:
                transfer_syntax_uid = str(ts)

        # ``bulkdata_base`` is the per-instance retrieve URL prefix; the
        # JSON converter appends ``/bulkdata/{tag}`` to it for binary VRs
        # so the WADO-RS endpoint can stream the bytes on demand.  This
        # is built in the worker so the main asyncio task never has to
        # touch the parsed Dataset.
        bulkdata_base = (
            f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}"
        )
        dicom_json = dataset_to_dicom_json(ds, bulkdata_base=bulkdata_base)
        searchable = extract_searchable_metadata(ds)

        return ParsedPartRecord(
            ok=True,
            sop_class_uid=sop_class_uid,
            transfer_syntax_uid=transfer_syntax_uid,
            study_uid=study_uid,
            series_uid=series_uid,
            sop_uid=sop_uid,
            dicom_json=dicom_json,
            searchable=searchable,
            searchable_warnings=searchable_warnings,
        )
    except Exception as e:
        return ParsedPartRecord(ok=False, parse_error=f"metadata extraction failed: {e}")
