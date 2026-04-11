"""Pydantic response models for STOW-RS (store) endpoints."""

from typing import Any

from pydantic import BaseModel


class DicomTagValue(BaseModel):
    """A single DICOM JSON tag entry with VR and optional Value."""

    vr: str
    Value: list[Any] | None = None

    model_config = {"populate_by_name": True}


class StowInstanceResult(BaseModel):
    """Per-instance result in a STOW-RS response sequence."""

    # 00081150 — ReferencedSOPClassUID
    # 00081155 — ReferencedSOPInstanceUID
    # 00081190 — RetrieveURL (on success)
    # 00081197 — FailureReason (on failure)
    # 00081196 — WarningReason (on warning)

    model_config = {"extra": "allow", "populate_by_name": True}


class StowResponse(BaseModel):
    """Top-level STOW-RS response body (application/dicom+json).

    Maps to the PS3.18 STOW-RS response dataset:
    - 00081190: RetrieveURL
    - 00081199: ReferencedSOPSequence (succeeded instances)
    - 00081198: FailedSOPSequence (failed instances)
    - 00081196: ReferencedSOPSequence with warnings (warning instances)
    """

    # Tag keys are 8-char hex strings; values are DICOM JSON objects
    model_config = {"extra": "allow", "populate_by_name": True}
