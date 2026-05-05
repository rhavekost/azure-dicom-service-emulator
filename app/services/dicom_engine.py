"""
DICOM Engine — parsing, metadata extraction, and JSON conversion.

Uses pydicom to handle DICOM file parsing and converts to the DICOM JSON
model (PS3.18 F.2) used by DICOMweb APIs.
"""

import asyncio
import logging
import os
import re
from typing import Any

import aiofiles
import pydicom
from pydicom.dataset import Dataset

from app.config import DICOM_STORAGE_DIR as STORAGE_DIR

logger = logging.getLogger(__name__)

# ── UID Validation (Security) ──────────────────────────────────────
# DICOM UIDs must only contain digits and dots to prevent directory traversal
_UID_PATTERN = re.compile(r"^[0-9.]+$")


def _validate_uid(uid: str) -> bool:
    """
    Validate DICOM UID format to prevent directory traversal attacks.

    Args:
        uid: DICOM UID to validate

    Returns:
        True if UID is valid (only contains digits and dots)

    Raises:
        ValueError: If UID contains invalid characters
    """
    if not _UID_PATTERN.match(uid):
        raise ValueError(
            f"Invalid DICOM UID format: '{uid}'. "
            f"UIDs must only contain digits and dots (0-9, .)"
        )
    return True


# DICOM VR types that produce string values in JSON
_STRING_VRS = {
    "AE",
    "AS",
    "CS",
    "DA",
    "DS",
    "DT",
    "IS",
    "LO",
    "LT",
    "PN",
    "SH",
    "ST",
    "TM",
    "UC",
    "UI",
    "UR",
    "UT",
}

# Tags we extract for searchable columns
SEARCHABLE_TAGS = {
    "PatientID": "patient_id",
    "PatientName": "patient_name",
    "StudyDate": "study_date",
    "StudyTime": "study_time",
    "AccessionNumber": "accession_number",
    "StudyDescription": "study_description",
    "Modality": "modality",
    "SeriesDescription": "series_description",
    "SeriesNumber": "series_number",
    "InstanceNumber": "instance_number",
    "ReferringPhysicianName": "referring_physician_name",
}


def ensure_storage_dir():
    """Create storage directory if it doesn't exist."""
    os.makedirs(STORAGE_DIR, exist_ok=True)


def parse_dicom(data: bytes) -> Dataset:
    """Parse raw bytes into a pydicom Dataset."""
    from io import BytesIO

    ds = pydicom.dcmread(BytesIO(data), force=True)
    return ds


def dataset_to_dicom_json(
    ds: Dataset,
    *,
    bulkdata_base: str | None = None,
) -> dict[str, Any]:
    """Convert a pydicom Dataset to DICOM JSON model (PS3.18 F.2).

    Each tag becomes a key like "00100010" with a dict containing
    ``vr`` and ``Value`` (array).

    Binary VRs (``OB``, ``OD``, ``OF``, ``OL``, ``OW``, ``UN``) are
    represented as a ``BulkDataURI`` reference rather than inline
    base64 (which used to bloat the ``dicom_json`` JSONB column by 33%
    *per pixel-overlay byte*).  When ``bulkdata_base`` is supplied the
    URI points back into the WADO-RS bulkdata endpoint:

        ``{bulkdata_base}/bulkdata/{tag}``

    where ``bulkdata_base`` is typically the per-instance retrieve URL
    (``/v2/studies/{s}/series/{ser}/instances/{inst}``).  When
    ``bulkdata_base`` is None (e.g. ``bulk_update_studies`` rebuilding
    JSON without instance context), the binary entry is dropped from
    the JSON entirely — the on-disk DCM file remains the canonical
    source for those bytes.
    """
    result = {}
    for elem in ds:
        if elem.tag.is_private:
            continue
        if elem.tag == pydicom.tag.Tag(0x7FE0, 0x0010):
            # Skip pixel data — not included in metadata
            continue

        tag_str = f"{elem.tag.group:04X}{elem.tag.element:04X}"
        entry: dict[str, Any] = {"vr": elem.VR}

        if elem.VR == "SQ":
            if elem.value:
                entry["Value"] = [
                    dataset_to_dicom_json(item, bulkdata_base=bulkdata_base)
                    for item in elem.value
                ]
        elif elem.VR == "PN":
            if elem.value:
                name = str(elem.value)
                entry["Value"] = [{"Alphabetic": name}]
        elif elem.VR in _STRING_VRS:
            if elem.value is not None and str(elem.value).strip():
                val = str(elem.value)
                entry["Value"] = [val]
        elif elem.VR in ("FL", "FD", "SL", "SS", "UL", "US", "SV", "UV"):
            if elem.value is not None:
                if elem.VM > 1:
                    entry["Value"] = list(elem.value)
                else:
                    entry["Value"] = [elem.value]
        elif elem.VR in ("OB", "OD", "OF", "OL", "OW", "UN"):
            # Binary VR — emit a BulkDataURI reference (DICOMweb PS3.18
            # standard) instead of inline base64.  Skipping the entry
            # entirely when ``bulkdata_base`` is None keeps callers like
            # ``bulk_update_studies`` from emitting URIs that point at
            # nothing.
            if elem.value is not None and bulkdata_base is not None:
                entry["BulkDataURI"] = f"{bulkdata_base}/bulkdata/{tag_str}"
            else:
                # No bulkdata base and no inline body — emit just the VR
                # so consumers know the element exists; bytes are in the
                # DCM blob on disk.
                pass
        else:
            if elem.value is not None and str(elem.value).strip():
                entry["Value"] = [str(elem.value)]

        result[tag_str] = entry

    return result


def extract_searchable_metadata(ds: Dataset) -> dict[str, Any]:
    """Extract searchable tag values into a flat dict for DB columns."""
    meta: dict[str, Any] = {}
    for dicom_keyword, db_column in SEARCHABLE_TAGS.items():
        value = getattr(ds, dicom_keyword, None)
        if value is not None:
            if dicom_keyword in ("SeriesNumber", "InstanceNumber"):
                try:
                    meta[db_column] = int(value)
                except (ValueError, TypeError):
                    meta[db_column] = None
            elif dicom_keyword in ("PatientName", "ReferringPhysicianName"):
                meta[db_column] = str(value)
            else:
                meta[db_column] = str(value).strip()
        else:
            meta[db_column] = None
    return meta


async def store_instance_bytes(
    data: bytes, study_uid: str, series_uid: str, sop_uid: str
) -> str:
    """Store a DICOM instance to the filesystem given its UIDs.

    Used by the streaming STOW path, which receives UIDs from the
    process-pool worker record and never materialises a full
    :class:`pydicom.Dataset` on the main asyncio thread.

    Returns the absolute file path.
    """
    ensure_storage_dir()

    _validate_uid(study_uid)
    _validate_uid(series_uid)
    _validate_uid(sop_uid)

    instance_dir = os.path.join(STORAGE_DIR, study_uid, series_uid, sop_uid)
    await asyncio.to_thread(os.makedirs, instance_dir, exist_ok=True)

    file_path = os.path.join(instance_dir, "instance.dcm")
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(data)

    return file_path


async def store_instance(data: bytes, ds: Dataset) -> str:
    """Store a DICOM instance to the filesystem.

    Backwards-compatible wrapper around :func:`store_instance_bytes` for
    callers that still hold a parsed :class:`pydicom.Dataset` (e.g. the
    legacy non-streaming code path and rollback regression tests).
    """
    return await store_instance_bytes(
        data,
        str(ds.StudyInstanceUID),
        str(ds.SeriesInstanceUID),
        str(ds.SOPInstanceUID),
    )


async def read_instance(file_path: str) -> bytes:
    """Read a stored DICOM instance from disk."""
    async with aiofiles.open(file_path, "rb") as f:
        return await f.read()


async def delete_instance_file(file_path: str) -> None:
    """Remove a DICOM instance file from disk."""
    try:
        await asyncio.to_thread(os.remove, file_path)
    except FileNotFoundError:
        pass


def validate_required_attributes(ds: Dataset) -> list[str]:
    """
    Validate required DICOM attributes per Azure DICOM Service v2 behavior.

    V2 only fails on required attribute validation failures, not on
    searchable attribute issues (those return warnings with HTTP 202).
    """
    errors = []
    required = [
        ("StudyInstanceUID", "0020000D"),
        ("SeriesInstanceUID", "0020000E"),
        ("SOPInstanceUID", "00080018"),
        ("SOPClassUID", "00080016"),
    ]
    for keyword, tag in required:
        if not hasattr(ds, keyword) or getattr(ds, keyword) is None:
            errors.append(f"Missing required attribute: {keyword} ({tag})")
    return errors


def validate_searchable_attributes(ds: Dataset) -> list[str]:
    """
    Validate searchable DICOM attributes.

    Returns list of warning messages for missing/invalid searchable attributes.
    Azure v2 behavior: Warnings for searchable attributes, errors only for required.

    Args:
        ds: DICOM dataset to validate

    Returns:
        List of warning messages (empty if all searchable attributes are valid)
    """
    warnings = []

    # Check PatientID (searchable, should be present)
    if not hasattr(ds, "PatientID") or not ds.PatientID:
        warnings.append("Missing or empty PatientID (0010,0020)")

    # Check Modality (searchable, should be present)
    if not hasattr(ds, "Modality") or not ds.Modality:
        warnings.append("Missing or empty Modality (0008,0060)")

    # Check Study Date (searchable)
    if not hasattr(ds, "StudyDate") or not ds.StudyDate:
        warnings.append("Missing or empty StudyDate (0008,0020)")

    return warnings


def build_store_response(
    study_uid: str,
    stored: list[dict],
    warnings: list[dict],
    failures: list[dict],
) -> dict:
    """
    Build STOW-RS response per DICOM PS3.18.

    Returns a DICOM JSON dataset representing the store response.
    """
    response: dict[str, Any] = {
        "00081190": {  # RetrieveURL
            "vr": "UR",
            "Value": [f"/v2/studies/{study_uid}"],
        },
    }

    if stored:
        response["00081199"] = {  # ReferencedSOPSequence (success)
            "vr": "SQ",
            "Value": stored,
        }

    if failures:
        response["00081198"] = {  # FailedSOPSequence
            "vr": "SQ",
            "Value": failures,
        }

    if warnings:
        response["00081196"] = {  # WarningReason — Azure v2 specific
            "vr": "US",
            "Value": [0xB000],  # Coercion of Data Elements
        }

    return response
