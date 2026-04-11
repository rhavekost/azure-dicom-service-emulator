"""Pydantic response models for the Operations API."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OperationResponse(BaseModel):
    """Status of an async operation as returned by Azure DICOM Service v2."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    type: str
    status: str
    percent_complete: int = Field(alias="percentComplete")
    created_time: str = Field(alias="createdTime")
    last_updated_time: str = Field(alias="lastUpdatedTime")
    results: dict[str, Any] | None = None
    errors: list[Any] | None = None
