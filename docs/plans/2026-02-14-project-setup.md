# Project Setup and Containerization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform existing Python files into a properly structured, containerized application with Docker Compose support

**Architecture:** Multi-stage Dockerfile with uv for dependency management, FastAPI app in proper package structure, PostgreSQL backend

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (async), PostgreSQL, pydicom, uv, Docker

---

## Task 1: Create Project Structure

**Files:**
- Create: `app/__init__.py`
- Create: `app/models/__init__.py`
- Create: `app/routers/__init__.py`
- Create: `app/services/__init__.py`

**Step 1: Create app directory and subdirectories**

```bash
mkdir -p app/models app/routers app/services
```

Expected: Directories created successfully

**Step 2: Create __init__.py files**

```bash
touch app/__init__.py app/models/__init__.py app/routers/__init__.py app/services/__init__.py
```

Expected: Empty __init__.py files created

**Step 3: Verify structure**

```bash
tree app/
```

Expected output:
```
app/
├── __init__.py
├── models/
│   └── __init__.py
├── routers/
│   └── __init__.py
└── services/
    └── __init__.py
```

**Step 4: Commit**

```bash
git add app/
git commit -m "feat: create app package structure

- Add app/ directory with models, routers, services subdirectories
- Initialize Python packages with __init__.py files

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 2: Move Existing Files to App Structure

**Files:**
- Move: `dicom.py` → `app/models/dicom.py`
- Move: `dicom_engine.py` → `app/services/dicom_engine.py`
- Move: `dicomweb.py` → `app/routers/dicomweb.py`

**Step 1: Move files**

```bash
git mv dicom.py app/models/dicom.py
git mv dicom_engine.py app/services/dicom_engine.py
git mv dicomweb.py app/routers/dicomweb.py
```

Expected: Files moved, git tracks the rename

**Step 2: Verify moves**

```bash
ls -la app/models/dicom.py app/services/dicom_engine.py app/routers/dicomweb.py
```

Expected: All three files exist in new locations

**Step 3: Commit**

```bash
git commit -m "refactor: reorganize files into app package structure

- Move dicom.py to app/models/
- Move dicom_engine.py to app/services/
- Move dicomweb.py to app/routers/

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 3: Create Database Module

**Files:**
- Create: `app/database.py`

**Step 1: Write database.py**

Create `app/database.py`:

```python
"""Database configuration and session management."""

import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://emulator:emulator@postgres:5432/dicom_emulator"
)

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)
Base = declarative_base()


async def get_db():
    """Dependency for FastAPI routes to get database session."""
    async with AsyncSessionLocal() as session:
        yield session
```

**Step 2: Verify syntax**

```bash
python -m py_compile app/database.py
```

Expected: No syntax errors

**Step 3: Test import**

```bash
python -c "from app.database import engine, Base, get_db; print('Imports successful')"
```

Expected: "Imports successful" (note: will fail until dependencies installed, that's ok for now)

**Step 4: Commit**

```bash
git add app/database.py
git commit -m "feat: add database module with SQLAlchemy async setup

- Create async engine with asyncpg driver
- Configure session maker for dependency injection
- Add get_db dependency for FastAPI routes

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 4: Create Multipart Parser Service

**Files:**
- Create: `app/services/multipart.py`

**Step 1: Write multipart.py**

Create `app/services/multipart.py`:

```python
"""Multipart/related parser for DICOM uploads (STOW-RS)."""

import re
from typing import NamedTuple


class MultipartPart(NamedTuple):
    """Single part from multipart/related message."""
    content_type: str
    data: bytes


def parse_multipart_related(body: bytes, content_type: str) -> list[MultipartPart]:
    """
    Parse multipart/related message and extract DICOM parts.

    Args:
        body: Raw multipart message body
        content_type: Content-Type header value (contains boundary)

    Returns:
        List of MultipartPart with content_type and binary data
    """
    # Extract boundary from Content-Type header
    boundary_match = re.search(r'boundary=([^\s;]+)', content_type)
    if not boundary_match:
        raise ValueError("No boundary found in Content-Type header")

    boundary = boundary_match.group(1).strip('"')
    boundary_bytes = f"--{boundary}".encode()

    # Split body by boundary
    parts = body.split(boundary_bytes)

    result = []
    for part in parts:
        if not part or part == b'--\r\n' or part == b'--':
            continue

        # Split headers from body
        if b'\r\n\r\n' in part:
            headers, data = part.split(b'\r\n\r\n', 1)
        elif b'\n\n' in part:
            headers, data = part.split(b'\n\n', 1)
        else:
            continue

        # Remove trailing CRLF
        data = data.rstrip(b'\r\n')

        # Extract Content-Type
        part_content_type = "application/dicom"  # default
        headers_str = headers.decode('utf-8', errors='ignore')
        ct_match = re.search(r'Content-Type:\s*([^\r\n]+)', headers_str, re.IGNORECASE)
        if ct_match:
            part_content_type = ct_match.group(1).strip()

        if data:
            result.append(MultipartPart(part_content_type, data))

    return result


def build_multipart_response(parts: list[tuple[str, bytes]], boundary: str) -> bytes:
    """
    Build multipart/related response for WADO-RS.

    Args:
        parts: List of (content_type, data) tuples
        boundary: Boundary string to use

    Returns:
        Complete multipart/related message as bytes
    """
    body_parts = []

    for content_type, data in parts:
        part = (
            f"--{boundary}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"\r\n"
        ).encode()
        part += data + b"\r\n"
        body_parts.append(part)

    body_parts.append(f"--{boundary}--\r\n".encode())

    return b"".join(body_parts)
```

**Step 2: Verify syntax**

```bash
python -m py_compile app/services/multipart.py
```

Expected: No syntax errors

**Step 3: Commit**

```bash
git add app/services/multipart.py
git commit -m "feat: add multipart/related parser for DICOM uploads

- Parse multipart/related messages for STOW-RS
- Extract boundary and DICOM parts
- Build multipart responses for WADO-RS

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 5: Create Change Feed Router

**Files:**
- Create: `app/routers/changefeed.py`

**Step 1: Write changefeed.py**

Create `app/routers/changefeed.py`:

```python
"""Azure Change Feed API router."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.dicom import ChangeFeedEntry

router = APIRouter()


@router.get("/changefeed")
async def get_change_feed(
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    start_time: Optional[str] = Query(None, alias="startTime"),
    end_time: Optional[str] = Query(None, alias="endTime"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get change feed entries with optional time-window filtering.

    Azure DICOM Service v2 compatible change feed.
    """
    query = select(ChangeFeedEntry)

    # Apply time filters if provided
    if start_time:
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        query = query.where(ChangeFeedEntry.timestamp >= start_dt)

    if end_time:
        end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        query = query.where(ChangeFeedEntry.timestamp <= end_dt)

    # Order by sequence and apply pagination
    query = query.order_by(ChangeFeedEntry.sequence).offset(offset).limit(limit)

    result = await db.execute(query)
    entries = result.scalars().all()

    # Format response to match Azure API
    return [
        {
            "Sequence": entry.sequence,
            "StudyInstanceUID": entry.study_instance_uid,
            "SeriesInstanceUID": entry.series_instance_uid,
            "SOPInstanceUID": entry.sop_instance_uid,
            "Action": entry.action,
            "Timestamp": entry.timestamp.isoformat(),
            "State": entry.state,
            "Metadata": entry.metadata,
        }
        for entry in entries
    ]


@router.get("/changefeed/latest")
async def get_latest_change_feed(
    db: AsyncSession = Depends(get_db),
):
    """Get the latest change feed entry."""
    query = select(ChangeFeedEntry).order_by(ChangeFeedEntry.sequence.desc()).limit(1)
    result = await db.execute(query)
    entry = result.scalar_one_or_none()

    if not entry:
        return {"Sequence": 0}

    return {
        "Sequence": entry.sequence,
        "StudyInstanceUID": entry.study_instance_uid,
        "SeriesInstanceUID": entry.series_instance_uid,
        "SOPInstanceUID": entry.sop_instance_uid,
        "Action": entry.action,
        "Timestamp": entry.timestamp.isoformat(),
        "State": entry.state,
        "Metadata": entry.metadata,
    }
```

**Step 2: Verify syntax**

```bash
python -m py_compile app/routers/changefeed.py
```

Expected: No syntax errors

**Step 3: Commit**

```bash
git add app/routers/changefeed.py
git commit -m "feat: add change feed API router

- GET /changefeed with time-window filtering
- GET /changefeed/latest for most recent entry
- Azure DICOM Service v2 compatible format

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 6: Create Extended Query Tags Router

**Files:**
- Create: `app/routers/extended_query_tags.py`

**Step 1: Write extended_query_tags.py**

Create `app/routers/extended_query_tags.py`:

```python
"""Azure Extended Query Tags API router."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete as sql_delete
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


@router.post("/extendedquerytags")
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
            raise HTTPException(
                status_code=409,
                detail=f"Tag {tag_input.path} already exists"
            )

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


@router.delete("/extendedquerytags/{path}")
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

    return {"status": "deleted"}
```

**Step 2: Verify syntax**

```bash
python -m py_compile app/routers/extended_query_tags.py
```

Expected: No syntax errors

**Step 3: Commit**

```bash
git add app/routers/extended_query_tags.py
git commit -m "feat: add extended query tags router

- List, create, get, and delete extended query tags
- Operation tracking for async tag creation
- Azure DICOM Service v2 compatible

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 7: Create Operations Router

**Files:**
- Create: `app/routers/operations.py`

**Step 1: Write operations.py**

Create `app/routers/operations.py`:

```python
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
```

**Step 2: Verify syntax**

```bash
python -m py_compile app/routers/operations.py
```

Expected: No syntax errors

**Step 3: Commit**

```bash
git add app/routers/operations.py
git commit -m "feat: add operations status router

- GET /operations/{id} for async operation tracking
- Returns operation status, progress, results, and errors

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 8: Update Router Imports

**Files:**
- Modify: `app/routers/__init__.py`

**Step 1: Update __init__.py**

Edit `app/routers/__init__.py`:

```python
"""Router modules for API endpoints."""

from app.routers import dicomweb, changefeed, extended_query_tags, operations

__all__ = ["dicomweb", "changefeed", "extended_query_tags", "operations"]
```

**Step 2: Verify imports**

```bash
python -c "from app.routers import dicomweb, changefeed, extended_query_tags, operations; print('All routers imported')"
```

Expected: "All routers imported" (may fail until dependencies installed)

**Step 3: Commit**

```bash
git add app/routers/__init__.py
git commit -m "feat: export all routers from routers package

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 9: Create Python Configuration Files

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`

**Step 1: Create pyproject.toml**

Create `pyproject.toml`:

```toml
[project]
name = "azure-dicom-service-emulator"
version = "0.1.0"
description = "Local emulator for Azure Health Data Services DICOM Service"
readme = "README.md"
requires-python = ">=3.12"
license = {text = "MIT"}
authors = [
    {name = "Rob", email = "rob@kostlabs.com"}
]

dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30.0",
    "pydicom>=3.0.1",
    "httpx>=0.28.0",
]

[project.optional-dependencies]
dev = [
    "ruff>=0.8.0",
    "mypy>=1.13.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W"]
ignore = ["E501"]  # Line too long (handled by formatter)
```

**Step 2: Create .python-version**

Create `.python-version`:

```
3.12
```

**Step 3: Verify files**

```bash
cat pyproject.toml | head -5
cat .python-version
```

Expected: File contents displayed correctly

**Step 4: Commit**

```bash
git add pyproject.toml .python-version
git commit -m "feat: add Python project configuration

- Add pyproject.toml with dependencies for uv
- Pin Python version to 3.12
- Configure ruff for linting

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 10: Create Dockerfile

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

**Step 1: Create Dockerfile**

Create `Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1

# ── Builder Stage ──────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml .
COPY .python-version .

# Install dependencies
RUN uv sync --frozen --no-dev

# ── Runtime Stage ──────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY app/ /app/app/
COPY main.py /app/

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV DICOM_STORAGE_DIR=/data/dicom

# Create storage directory
RUN mkdir -p /data/dicom

# Health check
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
  CMD curl -f http://localhost:8080/health || exit 1

# Expose port
EXPOSE 8080

# Run application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

**Step 2: Create .dockerignore**

Create `.dockerignore`:

```
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
*.egg-info/
dist/
build/

# Virtual environments
venv/
env/
.venv/

# IDEs
.vscode/
.idea/
*.swp
*.swo
*~

# Git
.git/
.gitignore

# Docker
.dockerignore
Dockerfile
docker-compose.yml

# Documentation
docs/
*.md

# Tests
tests/
smoke_test.py

# Misc
.DS_Store
.mypy_cache/
.pytest_cache/
.ruff_cache/
```

**Step 3: Verify syntax**

```bash
cat Dockerfile | head -10
cat .dockerignore | head -5
```

Expected: File contents displayed

**Step 4: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "feat: add multi-stage Dockerfile with uv

- Multi-stage build for fast dependency caching
- Python 3.12-slim base image
- uv for fast dependency installation
- Health check endpoint configured
- Optimized .dockerignore

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 11: Install Dependencies with uv

**Files:**
- Create: `uv.lock` (generated)

**Step 1: Install uv (if not already installed)**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Expected: uv installed or already present

**Step 2: Sync dependencies**

```bash
uv sync
```

Expected: Dependencies installed, uv.lock created

**Step 3: Verify installation**

```bash
uv run python -c "import fastapi, sqlalchemy, pydicom; print('Core dependencies installed')"
```

Expected: "Core dependencies installed"

**Step 4: Commit lock file**

```bash
git add uv.lock
git commit -m "chore: add uv.lock for reproducible builds

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 12: Verify Imports

**Files:**
- No file changes

**Step 1: Test database import**

```bash
uv run python -c "from app.database import engine, Base, get_db; print('✓ Database module')"
```

Expected: "✓ Database module"

**Step 2: Test router imports**

```bash
uv run python -c "from app.routers import dicomweb, changefeed, extended_query_tags, operations; print('✓ All routers')"
```

Expected: "✓ All routers"

**Step 3: Test service imports**

```bash
uv run python -c "from app.services.dicom_engine import parse_dicom; from app.services.multipart import parse_multipart_related; print('✓ Services')"
```

Expected: "✓ Services"

**Step 4: Test model imports**

```bash
uv run python -c "from app.models.dicom import DicomInstance, ChangeFeedEntry; print('✓ Models')"
```

Expected: "✓ Models"

---

## Task 13: Build Docker Image

**Files:**
- No file changes

**Step 1: Build image**

```bash
docker buildx build --platform linux/amd64 -t azure-dicom-emulator:latest .
```

Expected: Build succeeds in <2 minutes

**Step 2: Verify image**

```bash
docker images azure-dicom-emulator:latest
```

Expected: Image size ~300-400MB

**Step 3: Inspect image**

```bash
docker run --rm azure-dicom-emulator:latest python --version
```

Expected: "Python 3.12.x"

---

## Task 14: Start Services with Docker Compose

**Files:**
- No file changes

**Step 1: Start services**

```bash
docker compose up -d
```

Expected: Both emulator and postgres containers start

**Step 2: Wait for health checks**

```bash
sleep 15
docker compose ps
```

Expected: Both services show "healthy" status

**Step 3: Verify emulator logs**

```bash
docker compose logs emulator | tail -20
```

Expected: Uvicorn started, no errors

**Step 4: Test health endpoint**

```bash
curl http://localhost:8080/health
```

Expected: `{"status":"healthy","service":"azure-healthcare-workspace-emulator"}`

---

## Task 15: Run Smoke Tests

**Files:**
- No file changes

**Step 1: Install test dependencies**

```bash
uv pip install pydicom httpx
```

Expected: Dependencies installed

**Step 2: Run smoke tests**

```bash
python smoke_test.py http://localhost:8080
```

Expected: All 8 test sections pass

**Step 3: Check for failures**

If tests fail:
- Check `docker compose logs emulator` for errors
- Verify database connection
- Check individual API endpoints manually

**Step 4: Document results**

Create a test results summary in terminal output

---

## Task 16: Create .gitignore Updates

**Files:**
- Modify: `.gitignore`

**Step 1: Check if .gitignore exists**

```bash
test -f .gitignore && echo "exists" || echo "create new"
```

**Step 2: Add Python/Docker ignores**

Append to `.gitignore` (or create if doesn't exist):

```
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
*.egg-info/
dist/
build/
.venv/
venv/
env/

# uv
uv.lock

# IDEs
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store

# Database
*.db
*.sqlite

# Cache
.mypy_cache/
.pytest_cache/
.ruff_cache/

# Docker volumes
.worktrees/
```

**Step 3: Verify gitignore**

```bash
git check-ignore .venv __pycache__ .DS_Store
```

Expected: All three paths shown (are ignored)

**Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: update gitignore for Python and Docker

- Ignore Python cache and virtual environments
- Ignore IDE and OS files
- Ignore Docker volumes and worktrees

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 17: Final Verification and Cleanup

**Files:**
- No file changes

**Step 1: Stop services**

```bash
docker compose down
```

Expected: Containers stopped and removed

**Step 2: Rebuild from scratch**

```bash
docker compose build --no-cache
docker compose up -d
```

Expected: Clean build succeeds, services start

**Step 3: Run smoke tests again**

```bash
python smoke_test.py http://localhost:8080
```

Expected: ALL TESTS PASSED

**Step 4: Create summary**

List all new/modified files:

```bash
git status
git log --oneline -10
```

Expected: Clean working directory, ~16 commits

---

## Success Criteria

- ✅ All files in proper `app/` structure
- ✅ All imports resolve correctly
- ✅ Docker image builds in <2 minutes
- ✅ Both containers start and pass health checks
- ✅ Smoke tests pass all 8 sections
- ✅ Clean git history with atomic commits

## Troubleshooting

**Import errors:**
- Ensure all `__init__.py` files exist
- Check that moved files are in correct locations
- Verify `uv sync` completed successfully

**Docker build failures:**
- Check Dockerfile syntax
- Ensure pyproject.toml is valid TOML
- Verify uv.lock exists

**Container startup failures:**
- Check `docker compose logs emulator`
- Verify DATABASE_URL environment variable
- Ensure PostgreSQL is healthy first

**Smoke test failures:**
- Verify endpoints individually with curl
- Check database migrations ran (tables created)
- Review FastAPI docs at http://localhost:8080/docs
