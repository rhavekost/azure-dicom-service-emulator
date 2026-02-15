"""Azure Operations API router."""

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
    query = select(Operation).where(Operation.id == operation_id)
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
