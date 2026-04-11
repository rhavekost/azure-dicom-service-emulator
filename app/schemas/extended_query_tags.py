"""Pydantic response models for the Extended Query Tags API."""

from pydantic import BaseModel, ConfigDict, Field


class ExtendedQueryTagResponse(BaseModel):
    """A single extended query tag entry as returned by Azure DICOM Service v2."""

    model_config = ConfigDict(populate_by_name=True)

    Path: str
    VR: str
    PrivateCreator: str | None = None
    Level: str
    Status: str
    QueryStatus: str
    ErrorsCount: int = 0


class AddExtendedQueryTagsOperationResponse(BaseModel):
    """Response from POST /extendedquerytags — returns the created operation."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    status: str
    percent_complete: int = Field(alias="percentComplete")
    type: str
