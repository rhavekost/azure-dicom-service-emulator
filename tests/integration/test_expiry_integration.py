"""Integration tests for expiry workflow (Phase 3, Task 6)."""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models.dicom import DicomStudy
from app.services.expiry import delete_expired_studies

pytestmark = pytest.mark.integration


# Test database setup
@pytest.fixture
async def db_session(tmp_path):
    """Create a temporary test database."""
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    # Create tables
    from app.database import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create session
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session

    # Cleanup
    await engine.dispose()


@pytest.mark.asyncio
async def test_expiry_workflow_end_to_end(db_session, tmp_path):
    """Test complete expiry workflow from upload to deletion.

    This test verifies the end-to-end expiry workflow:
    1. Study is created with expiry timestamp
    2. Expiry timestamp is properly set in database
    3. After expiry time elapses, cleanup detects expired study
    4. Cleanup successfully deletes expired study
    5. Database confirms study is removed

    Note: This is a placeholder test. Full integration with PUT endpoint
    will be added when STOW-RS expiry header parsing is implemented.
    """
    # 1. Create study with expiry
    study = DicomStudy(
        study_instance_uid="1.2.3",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=1)
    )
    db_session.add(study)
    await db_session.commit()

    # 2. Verify expiry set
    assert study.expires_at is not None

    # 3. Wait for expiry
    await asyncio.sleep(2)

    # 4. Run cleanup
    deleted_count = await delete_expired_studies(db_session, str(tmp_path))
    assert deleted_count == 1

    # 5. Verify deleted
    result = await db_session.execute(select(DicomStudy))
    assert result.scalar_one_or_none() is None
