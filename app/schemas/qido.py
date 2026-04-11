"""Pydantic response models for QIDO-RS (search) endpoints.

QIDO-RS returns arrays of DICOM JSON objects. Each object is a dict of
8-character hex tag keys mapping to DICOM JSON tag entries of the form
{"vr": "<VR>", "Value": [...]}.

Because the full DICOM JSON structure is highly dynamic (any tag can appear),
we model the response as a list of open dicts rather than enumerating every
possible DICOM tag.
"""

from typing import Any

from pydantic import BaseModel


class DicomJsonTagEntry(BaseModel):
    """A DICOM JSON attribute: {"vr": "LO", "Value": [...]}."""

    vr: str
    Value: list[Any] | None = None

    model_config = {"populate_by_name": True}


class DicomJsonObject(BaseModel):
    """A DICOM JSON object: a mapping of 8-char hex tag keys to DicomJsonTagEntry values.

    Because any DICOM tag can appear, extra fields are allowed.
    """

    model_config = {"extra": "allow", "populate_by_name": True}
