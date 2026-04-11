"""Pydantic response models for the Change Feed API."""

from typing import Any

from pydantic import BaseModel


class ChangeFeedEntryResponse(BaseModel):
    """A single change feed entry as returned by Azure DICOM Service v2."""

    Sequence: int
    StudyInstanceUID: str
    SeriesInstanceUID: str | None = None
    SOPInstanceUID: str | None = None
    Action: str
    Timestamp: str
    State: str
    Metadata: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class ChangeFeedLatestResponse(BaseModel):
    """Response from GET /changefeed/latest — may be just a sequence number if empty."""

    Sequence: int
    StudyInstanceUID: str | None = None
    SeriesInstanceUID: str | None = None
    SOPInstanceUID: str | None = None
    Action: str | None = None
    Timestamp: str | None = None
    State: str | None = None
    Metadata: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}
