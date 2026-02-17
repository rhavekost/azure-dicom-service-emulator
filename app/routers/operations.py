"""Azure Operations API router."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.dicom import Operation

router = APIRouter()


@router.get("/operations/{operation_id}")
async def get_operation_status(
    operation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the status of an async operation."""
    # Convert string UUID to UUID object for database query
    try:
        operation_uuid = uuid.UUID(operation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid operation ID: {operation_id}")

    query = select(Operation).where(Operation.id == operation_uuid)
    result = await db.execute(query)
    operation = result.scalar_one_or_none()

    if not operation:
        raise HTTPException(status_code=404, detail=f"Operation {operation_id} not found")

    return {
        "id": str(operation.id),
        "type": operation.type,
        "status": operation.status,
        "percentComplete": operation.percent_complete,
        "createdTime": operation.created_at.isoformat(),
        "lastUpdatedTime": operation.updated_at.isoformat(),
        "results": operation.results,
        "errors": operation.errors,
    }
