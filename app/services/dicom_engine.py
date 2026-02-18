"""
DICOM Engine — parsing, metadata extraction, and JSON conversion.

Uses pydicom to handle DICOM file parsing and converts to the DICOM JSON
model (PS3.18 F.2) used by DICOMweb APIs.
"""

import base64
import os
import re
from typing import Any

import pydicom
from pydicom.dataset import Dataset

STORAGE_DIR = os.getenv("DICOM_STORAGE_DIR", "/data/dicom")

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


def dataset_to_dicom_json(ds: Dataset) -> dict[str, Any]:
    """
    Convert a pydicom Dataset to DICOM JSON model (PS3.18 F.2).

    Each tag becomes a key like "00100010" with a dict containing
    "vr" and "Value" (array).
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
                entry["Value"] = [dataset_to_dicom_json(item) for item in elem.value]
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
            # Binary data — encode as Base64 in InlineBinary field
            if elem.value is not None:
                try:
                    # Get raw bytes from the element
                    if isinstance(elem.value, bytes):
                        raw_bytes = elem.value
                    else:
                        raw_bytes = (
                            elem.value.tobytes()
                            if hasattr(elem.value, "tobytes")
                            else bytes(elem.value)
                        )

                    # Encode as Base64
                    encoded = base64.b64encode(raw_bytes).decode("ascii")
                    entry["InlineBinary"] = encoded
                except Exception:
                    # If encoding fails, skip this element
                    pass
        else:
            if elem.value is not None and str(elem.value).strip():
                entry["Value"] = [str(elem.value)]

        result[tag_str] = entry

    return result


def extract_searchable_metadata(ds: Dataset) -> dict[str, Any]:
    """Extract searchable tag values into a flat dict for DB columns."""
    meta = {}
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


def store_instance(data: bytes, ds: Dataset) -> str:
    """
    Store a DICOM instance to the filesystem.
    Returns the file path.
    """
    ensure_storage_dir()

    study_uid = str(ds.StudyInstanceUID)
    series_uid = str(ds.SeriesInstanceUID)
    sop_uid = str(ds.SOPInstanceUID)

    # Validate UIDs to prevent directory traversal attacks
    _validate_uid(study_uid)
    _validate_uid(series_uid)
    _validate_uid(sop_uid)

    # Create instance directory: {storage}/{study}/{series}/{sop}/
    instance_dir = os.path.join(STORAGE_DIR, study_uid, series_uid, sop_uid)
    os.makedirs(instance_dir, exist_ok=True)

    # Store as instance.dcm (consistent with frame_cache expectations)
    file_path = os.path.join(instance_dir, "instance.dcm")
    with open(file_path, "wb") as f:
        f.write(data)

    return file_path


def read_instance(file_path: str) -> bytes:
    """Read a stored DICOM instance from disk."""
    with open(file_path, "rb") as f:
        return f.read()


def delete_instance_file(file_path: str):
    """Remove a DICOM instance file from disk."""
    if os.path.exists(file_path):
        os.remove(file_path)


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
    response = {
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
