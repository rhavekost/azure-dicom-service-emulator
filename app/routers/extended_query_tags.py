"""Azure Extended Query Tags API router."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.dicom import ExtendedQueryTag, Operation

router = APIRouter()


class ExtendedQueryTagInput(BaseModel):
    """Input model for creating extended query tags."""

    path: str
    vr: str
    private_creator: str | None = None
    level: str


class ExtendedQueryTagsRequest(BaseModel):
    """Request body for adding extended query tags."""

    tags: list[ExtendedQueryTagInput]


@router.get("/extendedquerytags")
async def list_extended_query_tags(
    db: AsyncSession = Depends(get_db),
):
    """List all extended query tags."""
    query = select(ExtendedQueryTag)
    result = await db.execute(query)
    tags = result.scalars().all()

    return [
        {
            "Path": tag.path,
            "VR": tag.vr,
            "PrivateCreator": tag.private_creator,
            "Level": tag.level,
            "Status": tag.status,
            "QueryStatus": tag.query_status,
            "ErrorsCount": tag.errors_count,
        }
        for tag in tags
    ]


@router.post("/extendedquerytags", status_code=status.HTTP_202_ACCEPTED)
async def add_extended_query_tags(
    request: ExtendedQueryTagsRequest,
    db: AsyncSession = Depends(get_db),
):
    """Add new extended query tags."""
    operation_id = uuid.uuid4()

    # Create operation
    operation = Operation(
        id=operation_id,
        type="add-extended-query-tag",
        status="succeeded",  # Synchronous for now
        percent_complete=100,
    )
    db.add(operation)

    # Create tags
    for tag_input in request.tags:
        # Check if tag already exists
        existing = await db.execute(
            select(ExtendedQueryTag).where(ExtendedQueryTag.path == tag_input.path)
        )
        if existing.scalar_one_or_none():
            await db.rollback()
            raise HTTPException(status_code=409, detail=f"Tag {tag_input.path} already exists")

        tag = ExtendedQueryTag(
            path=tag_input.path,
            vr=tag_input.vr,
            private_creator=tag_input.private_creator,
            level=tag_input.level,
            status="Ready",  # Mark as Ready immediately (minimal impl)
            query_status="Enabled",
            operation_id=operation_id,
        )
        db.add(tag)

    await db.commit()

    # Return operation status (Azure v2 returns 202 with operation)
    return {
        "id": str(operation_id),
        "status": "succeeded",
        "percentComplete": 100,
        "type": "add-extended-query-tag",
    }


@router.get("/extendedquerytags/{path}")
async def get_extended_query_tag(
    path: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific extended query tag."""
    query = select(ExtendedQueryTag).where(ExtendedQueryTag.path == path)
    result = await db.execute(query)
    tag = result.scalar_one_or_none()

    if not tag:
        raise HTTPException(status_code=404, detail=f"Tag {path} not found")

    return {
        "Path": tag.path,
        "VR": tag.vr,
        "PrivateCreator": tag.private_creator,
        "Level": tag.level,
        "Status": tag.status,
        "QueryStatus": tag.query_status,
        "ErrorsCount": tag.errors_count,
    }


@router.delete("/extendedquerytags/{path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_extended_query_tag(
    path: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete an extended query tag."""
    query = select(ExtendedQueryTag).where(ExtendedQueryTag.path == path)
    result = await db.execute(query)
    tag = result.scalar_one_or_none()

    if not tag:
        raise HTTPException(status_code=404, detail=f"Tag {path} not found")

    await db.delete(tag)
    await db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
