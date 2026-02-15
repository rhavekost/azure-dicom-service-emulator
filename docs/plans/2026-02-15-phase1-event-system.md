# Phase 1: Event System Foundation - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Implement Azure Event Grid compatible event propagation system with provider pattern supporting Azurite Storage Queues, webhooks, files, and in-memory providers.

**Architecture:** Provider pattern with abstract base class, event manager for multi-provider publishing, best-effort non-blocking delivery, configuration-driven provider loading.

**Tech Stack:** Python 3.12, FastAPI, Azure Storage Queue SDK, httpx, tenacity (retry), pyyaml

---

## Prerequisites

### Add Dependencies to pyproject.toml

**File:** `pyproject.toml`

**Modify lines 12-19:**

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30.0",
    "pydicom>=3.0.1",
    "httpx>=0.28.0",
    "azure-storage-queue>=12.11.0",
    "pillow>=11.0.0",
    "pillow-jpls>=1.0.0",
    "tenacity>=9.0.0",
    "pyyaml>=6.0.2",
]
```

**Run:** `uv sync`

**Expected:** Dependencies installed successfully

**Commit:**
```bash
git add pyproject.toml uv.lock
git commit -m "deps: add azure-storage-queue, pillow, tenacity, pyyaml

Required for Phase 1 event system implementation.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 1: Event Data Models

**Files:**
- Create: `app/models/events.py`
- Test: `tests/models/test_events.py`

### Step 1: Write failing test for DicomEvent

**Create:** `tests/__init__.py` (empty file)

**Create:** `tests/models/__init__.py` (empty file)

**Create:** `tests/models/test_events.py`

```python
"""Tests for event data models."""

from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.models.events import DicomEvent


def test_dicom_event_creation():
    """Create a DicomEvent with all required fields."""
    event = DicomEvent(
        id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        event_type="Microsoft.HealthcareApis.DicomImageCreated",
        subject="/dicom/studies/1.2.3/series/4.5.6/instances/7.8.9",
        event_time=datetime.now(timezone.utc),
        data_version="1.0",
        metadata_version="1",
        topic="http://localhost:8080",
        data={
            "partitionName": "Microsoft.Default",
            "imageStudyInstanceUid": "1.2.3",
            "imageSeriesInstanceUid": "4.5.6",
            "imageSopInstanceUid": "7.8.9",
            "serviceHostName": "localhost:8080",
            "sequenceNumber": 1,
        },
    )

    assert event.event_type == "Microsoft.HealthcareApis.DicomImageCreated"
    assert event.data["imageStudyInstanceUid"] == "1.2.3"
    # Verify ID is valid UUID
    UUID(event.id)


def test_dicom_event_to_dict():
    """Convert DicomEvent to Azure Event Grid format."""
    event = DicomEvent(
        id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        event_type="Microsoft.HealthcareApis.DicomImageCreated",
        subject="/dicom/studies/1.2.3",
        event_time=datetime(2026, 2, 15, 10, 30, 0, tzinfo=timezone.utc),
        data_version="1.0",
        metadata_version="1",
        topic="http://localhost:8080",
        data={"test": "value"},
    )

    result = event.to_dict()

    assert result["id"] == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    assert result["eventType"] == "Microsoft.HealthcareApis.DicomImageCreated"
    assert result["subject"] == "/dicom/studies/1.2.3"
    assert result["eventTime"] == "2026-02-15T10:30:00+00:00"
    assert result["dataVersion"] == "1.0"
    assert result["metadataVersion"] == "1"
    assert result["topic"] == "http://localhost:8080"
    assert result["data"] == {"test": "value"}


def test_dicom_event_from_instance():
    """Create DicomImageCreated event from instance data."""
    event = DicomEvent.from_instance_created(
        study_uid="1.2.3",
        series_uid="4.5.6",
        instance_uid="7.8.9",
        sequence_number=42,
        service_url="http://localhost:8080",
    )

    assert event.event_type == "Microsoft.HealthcareApis.DicomImageCreated"
    assert event.subject == "/dicom/studies/1.2.3/series/4.5.6/instances/7.8.9"
    assert event.data["imageStudyInstanceUid"] == "1.2.3"
    assert event.data["sequenceNumber"] == 42
    # Verify ID was auto-generated
    UUID(event.id)
```

### Step 2: Run test to verify it fails

**Run:** `pytest tests/models/test_events.py -v`

**Expected:** `ModuleNotFoundError: No module named 'app.models.events'`

### Step 3: Write minimal implementation

**Create:** `app/models/events.py`

```python
"""Event data models for Azure Event Grid compatible events."""

import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class DicomEvent:
    """Azure Event Grid compatible DICOM event."""

    id: str
    event_type: str
    subject: str
    event_time: datetime
    data_version: str
    metadata_version: str
    topic: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to Azure Event Grid JSON format."""
        return {
            "id": self.id,
            "eventType": self.event_type,
            "subject": self.subject,
            "eventTime": self.event_time.isoformat(),
            "dataVersion": self.data_version,
            "metadataVersion": self.metadata_version,
            "topic": self.topic,
            "data": self.data,
        }

    @classmethod
    def from_instance_created(
        cls,
        study_uid: str,
        series_uid: str,
        instance_uid: str,
        sequence_number: int,
        service_url: str,
    ) -> "DicomEvent":
        """Create DicomImageCreated event from instance data."""
        return cls(
            id=str(uuid.uuid4()),
            event_type="Microsoft.HealthcareApis.DicomImageCreated",
            subject=f"/dicom/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}",
            event_time=datetime.now(timezone.utc),
            data_version="1.0",
            metadata_version="1",
            topic=service_url,
            data={
                "partitionName": "Microsoft.Default",
                "imageStudyInstanceUid": study_uid,
                "imageSeriesInstanceUid": series_uid,
                "imageSopInstanceUid": instance_uid,
                "serviceHostName": service_url.replace("http://", "").replace("https://", ""),
                "sequenceNumber": sequence_number,
            },
        )

    @classmethod
    def from_instance_deleted(
        cls,
        study_uid: str,
        series_uid: str,
        instance_uid: str,
        sequence_number: int,
        service_url: str,
    ) -> "DicomEvent":
        """Create DicomImageDeleted event from instance data."""
        return cls(
            id=str(uuid.uuid4()),
            event_type="Microsoft.HealthcareApis.DicomImageDeleted",
            subject=f"/dicom/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}",
            event_time=datetime.now(timezone.utc),
            data_version="1.0",
            metadata_version="1",
            topic=service_url,
            data={
                "partitionName": "Microsoft.Default",
                "imageStudyInstanceUid": study_uid,
                "imageSeriesInstanceUid": series_uid,
                "imageSopInstanceUid": instance_uid,
                "serviceHostName": service_url.replace("http://", "").replace("https://", ""),
                "sequenceNumber": sequence_number,
            },
        )
```

### Step 4: Run test to verify it passes

**Run:** `pytest tests/models/test_events.py -v`

**Expected:** All 3 tests pass

### Step 5: Commit

```bash
git add app/models/events.py tests/
git commit -m "feat: add DicomEvent data model

- Azure Event Grid compatible event schema
- to_dict() for JSON serialization
- Factory methods for Created/Deleted events
- Auto-generate UUIDs and timestamps

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 2: Event Provider Base Class

**Files:**
- Create: `app/services/events/__init__.py`
- Create: `app/services/events/providers.py`
- Test: `tests/services/test_event_providers.py`

### Step 1: Write failing test for EventProvider

**Create:** `tests/services/__init__.py` (empty)

**Create:** `tests/services/test_event_providers.py`

```python
"""Tests for event provider base class."""

import pytest

from app.models.events import DicomEvent
from app.services.events.providers import EventProvider


class MockEventProvider(EventProvider):
    """Mock provider for testing."""

    def __init__(self):
        self.published_events = []
        self.published_batches = []

    async def publish(self, event: DicomEvent) -> None:
        self.published_events.append(event)

    async def publish_batch(self, events: list[DicomEvent]) -> None:
        self.published_batches.append(events)


@pytest.mark.asyncio
async def test_event_provider_abstract():
    """EventProvider is abstract and cannot be instantiated."""
    with pytest.raises(TypeError):
        EventProvider()


@pytest.mark.asyncio
async def test_mock_provider_publish():
    """Mock provider stores published events."""
    provider = MockEventProvider()
    event = DicomEvent.from_instance_created(
        "1.2.3", "4.5.6", "7.8.9", 1, "http://localhost"
    )

    await provider.publish(event)

    assert len(provider.published_events) == 1
    assert provider.published_events[0] == event


@pytest.mark.asyncio
async def test_mock_provider_publish_batch():
    """Mock provider stores batch of events."""
    provider = MockEventProvider()
    events = [
        DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost"),
        DicomEvent.from_instance_created("1.2.3", "4.5.6", "10.11.12", 2, "http://localhost"),
    ]

    await provider.publish_batch(events)

    assert len(provider.published_batches) == 1
    assert provider.published_batches[0] == events


@pytest.mark.asyncio
async def test_provider_health_check_default():
    """Provider health check defaults to True."""
    provider = MockEventProvider()

    result = await provider.health_check()

    assert result is True


@pytest.mark.asyncio
async def test_provider_close_default():
    """Provider close is a no-op by default."""
    provider = MockEventProvider()

    # Should not raise
    await provider.close()
```

### Step 2: Run test to verify it fails

**Run:** `pytest tests/services/test_event_providers.py -v`

**Expected:** `ModuleNotFoundError: No module named 'app.services.events'`

### Step 3: Write minimal implementation

**Create:** `app/services/events/__init__.py`

```python
"""Event publishing services."""

from app.services.events.providers import EventProvider

__all__ = ["EventProvider"]
```

**Create:** `app/services/events/providers.py`

```python
"""Event provider base class and implementations."""

from abc import ABC, abstractmethod

from app.models.events import DicomEvent


class EventProvider(ABC):
    """Base class for event providers."""

    @abstractmethod
    async def publish(self, event: DicomEvent) -> None:
        """Publish a single event."""

    @abstractmethod
    async def publish_batch(self, events: list[DicomEvent]) -> None:
        """Publish multiple events atomically."""

    async def health_check(self) -> bool:
        """Check if provider is healthy and reachable."""
        return True

    async def close(self) -> None:
        """Clean up resources."""
        pass
```

### Step 4: Run test to verify it passes

**Run:** `pytest tests/services/test_event_providers.py -v`

**Expected:** All 6 tests pass

### Step 5: Commit

```bash
git add app/services/events/ tests/services/
git commit -m "feat: add EventProvider abstract base class

- ABC for event providers with publish/publish_batch methods
- Optional health_check and close hooks
- Test coverage with MockEventProvider

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 3: In-Memory Event Provider

**Files:**
- Modify: `app/services/events/providers.py`
- Test: `tests/services/test_in_memory_provider.py`

### Step 1: Write failing test

**Create:** `tests/services/test_in_memory_provider.py`

```python
"""Tests for InMemoryEventProvider."""

import pytest

from app.models.events import DicomEvent
from app.services.events.providers import InMemoryEventProvider


@pytest.mark.asyncio
async def test_in_memory_provider_publish():
    """InMemoryProvider stores events in memory."""
    provider = InMemoryEventProvider()
    event = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")

    await provider.publish(event)

    assert len(provider.events) == 1
    assert provider.events[0] == event


@pytest.mark.asyncio
async def test_in_memory_provider_publish_batch():
    """InMemoryProvider stores batch of events."""
    provider = InMemoryEventProvider()
    events = [
        DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost"),
        DicomEvent.from_instance_created("1.2.3", "4.5.6", "10.11.12", 2, "http://localhost"),
    ]

    await provider.publish_batch(events)

    assert len(provider.events) == 2
    assert provider.events == events


@pytest.mark.asyncio
async def test_in_memory_provider_get_events():
    """InMemoryProvider can retrieve all events."""
    provider = InMemoryEventProvider()
    event1 = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")
    event2 = DicomEvent.from_instance_created("1.2.3", "4.5.6", "10.11.12", 2, "http://localhost")

    await provider.publish(event1)
    await provider.publish(event2)

    all_events = provider.get_events()

    assert len(all_events) == 2
    assert all_events[0] == event1
    assert all_events[1] == event2


@pytest.mark.asyncio
async def test_in_memory_provider_clear():
    """InMemoryProvider can clear all events."""
    provider = InMemoryEventProvider()
    await provider.publish(DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost"))

    provider.clear()

    assert len(provider.events) == 0
```

### Step 2: Run test to verify it fails

**Run:** `pytest tests/services/test_in_memory_provider.py -v`

**Expected:** `ImportError: cannot import name 'InMemoryEventProvider'`

### Step 3: Write minimal implementation

**Modify:** `app/services/events/providers.py` (add at end)

```python
class InMemoryEventProvider(EventProvider):
    """In-memory event provider for testing and debugging."""

    def __init__(self):
        self.events: list[DicomEvent] = []

    async def publish(self, event: DicomEvent) -> None:
        """Store event in memory."""
        self.events.append(event)

    async def publish_batch(self, events: list[DicomEvent]) -> None:
        """Store batch of events in memory."""
        self.events.extend(events)

    def get_events(self) -> list[DicomEvent]:
        """Retrieve all stored events."""
        return self.events.copy()

    def clear(self) -> None:
        """Clear all stored events."""
        self.events.clear()
```

**Modify:** `app/services/events/__init__.py`

```python
"""Event publishing services."""

from app.services.events.providers import EventProvider, InMemoryEventProvider

__all__ = ["EventProvider", "InMemoryEventProvider"]
```

### Step 4: Run test to verify it passes

**Run:** `pytest tests/services/test_in_memory_provider.py -v`

**Expected:** All 4 tests pass

### Step 5: Commit

```bash
git add app/services/events/ tests/services/test_in_memory_provider.py
git commit -m "feat: add InMemoryEventProvider for testing

- Stores events in memory list
- get_events() and clear() methods for testing
- Useful for unit tests and debugging

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 4: File Event Provider

**Files:**
- Modify: `app/services/events/providers.py`
- Test: `tests/services/test_file_provider.py`

### Step 1: Write failing test

**Create:** `tests/services/test_file_provider.py`

```python
"""Tests for FileEventProvider."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.models.events import DicomEvent
from app.services.events.providers import FileEventProvider


@pytest.mark.asyncio
async def test_file_provider_publish():
    """FileProvider appends events to JSON Lines file."""
    with TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "events.jsonl"
        provider = FileEventProvider(str(file_path))

        event = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")
        await provider.publish(event)

        # Read file and verify
        lines = file_path.read_text().strip().split("\n")
        assert len(lines) == 1

        event_data = json.loads(lines[0])
        assert event_data["eventType"] == "Microsoft.HealthcareApis.DicomImageCreated"
        assert event_data["data"]["imageStudyInstanceUid"] == "1.2.3"


@pytest.mark.asyncio
async def test_file_provider_publish_multiple():
    """FileProvider appends multiple events on separate lines."""
    with TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "events.jsonl"
        provider = FileEventProvider(str(file_path))

        event1 = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")
        event2 = DicomEvent.from_instance_created("1.2.3", "4.5.6", "10.11.12", 2, "http://localhost")

        await provider.publish(event1)
        await provider.publish(event2)

        lines = file_path.read_text().strip().split("\n")
        assert len(lines) == 2

        data1 = json.loads(lines[0])
        data2 = json.loads(lines[1])
        assert data1["data"]["sequenceNumber"] == 1
        assert data2["data"]["sequenceNumber"] == 2


@pytest.mark.asyncio
async def test_file_provider_publish_batch():
    """FileProvider appends batch of events."""
    with TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "events.jsonl"
        provider = FileEventProvider(str(file_path))

        events = [
            DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost"),
            DicomEvent.from_instance_created("1.2.3", "4.5.6", "10.11.12", 2, "http://localhost"),
        ]

        await provider.publish_batch(events)

        lines = file_path.read_text().strip().split("\n")
        assert len(lines) == 2


@pytest.mark.asyncio
async def test_file_provider_creates_directory():
    """FileProvider creates parent directory if it doesn't exist."""
    with TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "subdir" / "events.jsonl"
        provider = FileEventProvider(str(file_path))

        event = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")
        await provider.publish(event)

        assert file_path.exists()
        assert file_path.parent.is_dir()
```

### Step 2: Run test to verify it fails

**Run:** `pytest tests/services/test_file_provider.py -v`

**Expected:** `ImportError: cannot import name 'FileEventProvider'`

### Step 3: Write minimal implementation

**Modify:** `app/services/events/providers.py` (add at end)

```python
import json
from pathlib import Path


class FileEventProvider(EventProvider):
    """File-based event provider (JSON Lines format)."""

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)

    async def publish(self, event: DicomEvent) -> None:
        """Append event to JSON Lines file."""
        # Create parent directory if needed
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

        # Append event as JSON line
        with open(self.file_path, "a") as f:
            f.write(json.dumps(event.to_dict()) + "\n")

    async def publish_batch(self, events: list[DicomEvent]) -> None:
        """Append batch of events to JSON Lines file."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.file_path, "a") as f:
            for event in events:
                f.write(json.dumps(event.to_dict()) + "\n")
```

**Modify:** `app/services/events/__init__.py`

```python
"""Event publishing services."""

from app.services.events.providers import (
    EventProvider,
    FileEventProvider,
    InMemoryEventProvider,
)

__all__ = ["EventProvider", "FileEventProvider", "InMemoryEventProvider"]
```

### Step 4: Run test to verify it passes

**Run:** `pytest tests/services/test_file_provider.py -v`

**Expected:** All 4 tests pass

### Step 5: Commit

```bash
git add app/services/events/ tests/services/test_file_provider.py
git commit -m "feat: add FileEventProvider for JSON Lines logging

- Appends events to file in JSON Lines format
- Creates parent directory if needed
- Useful for debugging and audit trails

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 5: Webhook Event Provider with Retry

**Files:**
- Modify: `app/services/events/providers.py`
- Test: `tests/services/test_webhook_provider.py`

### Step 1: Write failing test

**Create:** `tests/services/test_webhook_provider.py`

```python
"""Tests for WebhookEventProvider."""

import pytest
from unittest.mock import AsyncMock, patch

from app.models.events import DicomEvent
from app.services.events.providers import WebhookEventProvider


@pytest.mark.asyncio
async def test_webhook_provider_publish_success():
    """WebhookProvider sends HTTP POST on publish."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.status_code = 200

        provider = WebhookEventProvider("https://webhook.site/test")
        event = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")

        await provider.publish(event)

        mock_post.assert_awaited_once()
        call_args = mock_post.call_args
        assert call_args.args[0] == "https://webhook.site/test"
        assert call_args.kwargs["json"] == event.to_dict()


@pytest.mark.asyncio
async def test_webhook_provider_publish_batch():
    """WebhookProvider sends each event in batch separately."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.status_code = 200

        provider = WebhookEventProvider("https://webhook.site/test")
        events = [
            DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost"),
            DicomEvent.from_instance_created("1.2.3", "4.5.6", "10.11.12", 2, "http://localhost"),
        ]

        await provider.publish_batch(events)

        assert mock_post.await_count == 2


@pytest.mark.asyncio
async def test_webhook_provider_retry_on_failure():
    """WebhookProvider retries on HTTP errors."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        # Fail first 2 attempts, succeed on 3rd
        mock_post.side_effect = [
            Exception("Network error"),
            Exception("Network error"),
            AsyncMock(status_code=200),
        ]

        provider = WebhookEventProvider("https://webhook.site/test", retry_attempts=3)
        event = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")

        await provider.publish(event)

        # Should have retried 3 times total
        assert mock_post.await_count == 3


@pytest.mark.asyncio
async def test_webhook_provider_gives_up_after_retries():
    """WebhookProvider raises after max retries."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = Exception("Network error")

        provider = WebhookEventProvider("https://webhook.site/test", retry_attempts=3)
        event = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")

        with pytest.raises(Exception, match="Network error"):
            await provider.publish(event)

        # Should have tried 3 times
        assert mock_post.await_count == 3
```

### Step 2: Run test to verify it fails

**Run:** `pytest tests/services/test_webhook_provider.py -v`

**Expected:** `ImportError: cannot import name 'WebhookEventProvider'`

### Step 3: Write minimal implementation

**Modify:** `app/services/events/providers.py` (add imports at top)

```python
import logging
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)
```

**Modify:** `app/services/events/providers.py` (add at end)

```python
class WebhookEventProvider(EventProvider):
    """Webhook-based event provider with retry logic."""

    def __init__(self, url: str, retry_attempts: int = 3):
        self.url = url
        self.retry_attempts = retry_attempts

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, Exception))
    )
    async def _send_webhook(self, event: DicomEvent) -> None:
        """Send webhook with exponential backoff retry."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.url,
                json=event.to_dict(),
                timeout=5.0
            )
            response.raise_for_status()

    async def publish(self, event: DicomEvent) -> None:
        """Publish event via webhook POST."""
        await self._send_webhook(event)

    async def publish_batch(self, events: list[DicomEvent]) -> None:
        """Publish batch of events via webhook (sends each separately)."""
        for event in events:
            await self._send_webhook(event)
```

**Modify:** `app/services/events/__init__.py`

```python
"""Event publishing services."""

from app.services.events.providers import (
    EventProvider,
    FileEventProvider,
    InMemoryEventProvider,
    WebhookEventProvider,
)

__all__ = [
    "EventProvider",
    "FileEventProvider",
    "InMemoryEventProvider",
    "WebhookEventProvider",
]
```

### Step 4: Run test to verify it passes

**Run:** `pytest tests/services/test_webhook_provider.py -v`

**Expected:** All 4 tests pass

### Step 5: Commit

```bash
git add app/services/events/ tests/services/test_webhook_provider.py
git commit -m "feat: add WebhookEventProvider with retry logic

- HTTP POST events to webhook URL
- Exponential backoff retry (tenacity)
- Configurable retry attempts
- 5 second timeout per request

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 6: Azure Storage Queue Event Provider

**Files:**
- Modify: `app/services/events/providers.py`
- Test: `tests/services/test_azure_queue_provider.py`

### Step 1: Write failing test

**Create:** `tests/services/test_azure_queue_provider.py`

```python
"""Tests for AzureStorageQueueProvider."""

import pytest
from unittest.mock import AsyncMock, Mock, patch

from app.models.events import DicomEvent
from app.services.events.providers import AzureStorageQueueProvider


@pytest.mark.asyncio
async def test_azure_queue_provider_publish():
    """AzureStorageQueueProvider sends message to Azure Queue."""
    mock_queue = Mock()
    mock_queue.send_message = Mock()

    with patch("app.services.events.providers.QueueClient.from_connection_string", return_value=mock_queue):
        provider = AzureStorageQueueProvider(
            connection_string="UseDevelopmentStorage=true",
            queue_name="test-queue"
        )

        event = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")
        await provider.publish(event)

        mock_queue.send_message.assert_called_once()
        call_args = mock_queue.send_message.call_args
        # Verify message is JSON string
        import json
        message_data = json.loads(call_args.args[0])
        assert message_data["eventType"] == "Microsoft.HealthcareApis.DicomImageCreated"


@pytest.mark.asyncio
async def test_azure_queue_provider_publish_batch():
    """AzureStorageQueueProvider sends each event in batch."""
    mock_queue = Mock()
    mock_queue.send_message = Mock()

    with patch("app.services.events.providers.QueueClient.from_connection_string", return_value=mock_queue):
        provider = AzureStorageQueueProvider(
            connection_string="UseDevelopmentStorage=true",
            queue_name="test-queue"
        )

        events = [
            DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost"),
            DicomEvent.from_instance_created("1.2.3", "4.5.6", "10.11.12", 2, "http://localhost"),
        ]

        await provider.publish_batch(events)

        assert mock_queue.send_message.call_count == 2


@pytest.mark.asyncio
async def test_azure_queue_provider_close():
    """AzureStorageQueueProvider closes queue client."""
    mock_queue = Mock()
    mock_queue.close = Mock()

    with patch("app.services.events.providers.QueueClient.from_connection_string", return_value=mock_queue):
        provider = AzureStorageQueueProvider(
            connection_string="UseDevelopmentStorage=true",
            queue_name="test-queue"
        )

        await provider.close()

        mock_queue.close.assert_called_once()
```

### Step 2: Run test to verify it fails

**Run:** `pytest tests/services/test_azure_queue_provider.py -v`

**Expected:** `ImportError: cannot import name 'AzureStorageQueueProvider'`

### Step 3: Write minimal implementation

**Modify:** `app/services/events/providers.py` (add import at top)

```python
from azure.storage.queue import QueueClient
```

**Modify:** `app/services/events/providers.py` (add at end)

```python
class AzureStorageQueueProvider(EventProvider):
    """Azure Storage Queue event provider (Azurite compatible)."""

    def __init__(self, connection_string: str, queue_name: str):
        self.queue_client = QueueClient.from_connection_string(
            connection_string, queue_name
        )

    async def publish(self, event: DicomEvent) -> None:
        """Send event to Azure Storage Queue."""
        import json
        message = json.dumps(event.to_dict())
        self.queue_client.send_message(message)

    async def publish_batch(self, events: list[DicomEvent]) -> None:
        """Send batch of events to Azure Storage Queue."""
        import json
        for event in events:
            message = json.dumps(event.to_dict())
            self.queue_client.send_message(message)

    async def close(self) -> None:
        """Close queue client connection."""
        self.queue_client.close()
```

**Modify:** `app/services/events/__init__.py`

```python
"""Event publishing services."""

from app.services.events.providers import (
    AzureStorageQueueProvider,
    EventProvider,
    FileEventProvider,
    InMemoryEventProvider,
    WebhookEventProvider,
)

__all__ = [
    "AzureStorageQueueProvider",
    "EventProvider",
    "FileEventProvider",
    "InMemoryEventProvider",
    "WebhookEventProvider",
]
```

### Step 4: Run test to verify it passes

**Run:** `pytest tests/services/test_azure_queue_provider.py -v`

**Expected:** All 3 tests pass

### Step 5: Commit

```bash
git add app/services/events/ tests/services/test_azure_queue_provider.py
git commit -m "feat: add AzureStorageQueueProvider

- Publishes events to Azure Storage Queue
- Compatible with Azurite for local development
- Sends events as JSON string messages

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 7: Event Manager (Multi-Provider Publishing)

**Files:**
- Create: `app/services/events/manager.py`
- Test: `tests/services/test_event_manager.py`

### Step 1: Write failing test

**Create:** `tests/services/test_event_manager.py`

```python
"""Tests for EventManager."""

import asyncio
import pytest
from unittest.mock import AsyncMock

from app.models.events import DicomEvent
from app.services.events.manager import EventManager
from app.services.events.providers import EventProvider


class MockProvider(EventProvider):
    def __init__(self, name: str, should_fail: bool = False):
        self.name = name
        self.should_fail = should_fail
        self.published = []

    async def publish(self, event: DicomEvent) -> None:
        if self.should_fail:
            raise Exception(f"{self.name} failed")
        self.published.append(event)

    async def publish_batch(self, events: list[DicomEvent]) -> None:
        if self.should_fail:
            raise Exception(f"{self.name} failed")
        self.published.extend(events)


@pytest.mark.asyncio
async def test_event_manager_publishes_to_all_providers():
    """EventManager publishes to all registered providers."""
    provider1 = MockProvider("provider1")
    provider2 = MockProvider("provider2")
    manager = EventManager([provider1, provider2])

    event = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")
    await manager.publish(event)

    assert len(provider1.published) == 1
    assert len(provider2.published) == 1
    assert provider1.published[0] == event
    assert provider2.published[0] == event


@pytest.mark.asyncio
async def test_event_manager_continues_on_provider_failure():
    """EventManager continues publishing even if one provider fails."""
    failing_provider = MockProvider("failing", should_fail=True)
    working_provider = MockProvider("working")
    manager = EventManager([failing_provider, working_provider])

    event = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")

    # Should not raise
    await manager.publish(event)

    # Working provider still received event
    assert len(working_provider.published) == 1
    assert working_provider.published[0] == event

    # Failing provider didn't publish
    assert len(failing_provider.published) == 0


@pytest.mark.asyncio
async def test_event_manager_timeout():
    """EventManager times out slow providers."""
    class SlowProvider(EventProvider):
        async def publish(self, event: DicomEvent) -> None:
            await asyncio.sleep(10)  # Simulate slow provider

        async def publish_batch(self, events: list[DicomEvent]) -> None:
            await asyncio.sleep(10)

    slow_provider = SlowProvider()
    fast_provider = MockProvider("fast")
    manager = EventManager([slow_provider, fast_provider], timeout=0.1)

    event = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")

    # Should not hang
    await manager.publish(event)

    # Fast provider still worked
    assert len(fast_provider.published) == 1


@pytest.mark.asyncio
async def test_event_manager_close_all_providers():
    """EventManager closes all providers."""
    provider1 = MockProvider("provider1")
    provider2 = MockProvider("provider2")
    provider1.close = AsyncMock()
    provider2.close = AsyncMock()

    manager = EventManager([provider1, provider2])
    await manager.close()

    provider1.close.assert_awaited_once()
    provider2.close.assert_awaited_once()
```

### Step 2: Run test to verify it fails

**Run:** `pytest tests/services/test_event_manager.py -v`

**Expected:** `ModuleNotFoundError: No module named 'app.services.events.manager'`

### Step 3: Write minimal implementation

**Create:** `app/services/events/manager.py`

```python
"""Event manager for multi-provider publishing."""

import asyncio
import logging
from typing import Any

from app.models.events import DicomEvent
from app.services.events.providers import EventProvider

logger = logging.getLogger(__name__)


class EventManager:
    """Manages event publishing to multiple providers."""

    def __init__(self, providers: list[EventProvider], timeout: float = 5.0):
        self.providers = providers
        self.timeout = timeout

    async def publish(self, event: DicomEvent) -> None:
        """Publish event to all providers (best-effort, non-blocking)."""
        failed_providers = []

        for provider in self.providers:
            try:
                await asyncio.wait_for(
                    provider.publish(event),
                    timeout=self.timeout
                )
            except asyncio.TimeoutError:
                provider_name = provider.__class__.__name__
                logger.error(f"Provider {provider_name} timed out")
                failed_providers.append(provider_name)
            except Exception as e:
                provider_name = provider.__class__.__name__
                logger.error(f"Provider {provider_name} failed: {e}")
                failed_providers.append(provider_name)

        if failed_providers:
            logger.warning(f"Event publish failed for providers: {failed_providers}")

    async def publish_batch(self, events: list[DicomEvent]) -> None:
        """Publish batch of events to all providers."""
        failed_providers = []

        for provider in self.providers:
            try:
                await asyncio.wait_for(
                    provider.publish_batch(events),
                    timeout=self.timeout * len(events)
                )
            except asyncio.TimeoutError:
                provider_name = provider.__class__.__name__
                logger.error(f"Provider {provider_name} timed out")
                failed_providers.append(provider_name)
            except Exception as e:
                provider_name = provider.__class__.__name__
                logger.error(f"Provider {provider_name} failed: {e}")
                failed_providers.append(provider_name)

        if failed_providers:
            logger.warning(f"Batch event publish failed for providers: {failed_providers}")

    async def close(self) -> None:
        """Close all providers."""
        for provider in self.providers:
            try:
                await provider.close()
            except Exception as e:
                logger.error(f"Failed to close provider {provider.__class__.__name__}: {e}")
```

**Modify:** `app/services/events/__init__.py`

```python
"""Event publishing services."""

from app.services.events.manager import EventManager
from app.services.events.providers import (
    AzureStorageQueueProvider,
    EventProvider,
    FileEventProvider,
    InMemoryEventProvider,
    WebhookEventProvider,
)

__all__ = [
    "AzureStorageQueueProvider",
    "EventManager",
    "EventProvider",
    "FileEventProvider",
    "InMemoryEventProvider",
    "WebhookEventProvider",
]
```

### Step 4: Run test to verify it passes

**Run:** `pytest tests/services/test_event_manager.py -v`

**Expected:** All 4 tests pass

### Step 5: Commit

```bash
git add app/services/events/ tests/services/test_event_manager.py
git commit -m "feat: add EventManager for multi-provider publishing

- Publishes to all providers with timeout protection
- Best-effort delivery (continues on provider failure)
- Logs failures but doesn't raise exceptions
- close() method for cleanup

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 8: Provider Configuration Loading

**Files:**
- Create: `app/services/events/config.py`
- Test: `tests/services/test_event_config.py`

### Step 1: Write failing test

**Create:** `tests/services/test_event_config.py`

```python
"""Tests for event provider configuration."""

import json
import os
import pytest
from tempfile import TemporaryDirectory
from pathlib import Path

from app.services.events.config import load_providers_from_config


def test_load_providers_from_json_env():
    """Load providers from EVENT_PROVIDERS environment variable."""
    config = [
        {
            "type": "in_memory",
            "enabled": True
        },
        {
            "type": "file",
            "path": "/tmp/events.jsonl",
            "enabled": True
        }
    ]

    os.environ["EVENT_PROVIDERS"] = json.dumps(config)

    try:
        providers = load_providers_from_config()

        assert len(providers) == 2
        assert providers[0].__class__.__name__ == "InMemoryEventProvider"
        assert providers[1].__class__.__name__ == "FileEventProvider"
    finally:
        del os.environ["EVENT_PROVIDERS"]


def test_load_providers_skips_disabled():
    """Disabled providers are not loaded."""
    config = [
        {
            "type": "in_memory",
            "enabled": True
        },
        {
            "type": "file",
            "path": "/tmp/events.jsonl",
            "enabled": False
        }
    ]

    os.environ["EVENT_PROVIDERS"] = json.dumps(config)

    try:
        providers = load_providers_from_config()

        assert len(providers) == 1
        assert providers[0].__class__.__name__ == "InMemoryEventProvider"
    finally:
        del os.environ["EVENT_PROVIDERS"]


def test_load_webhook_provider():
    """Load webhook provider with configuration."""
    config = [
        {
            "type": "webhook",
            "url": "https://webhook.site/test",
            "retry_attempts": 5,
            "enabled": True
        }
    ]

    os.environ["EVENT_PROVIDERS"] = json.dumps(config)

    try:
        providers = load_providers_from_config()

        assert len(providers) == 1
        provider = providers[0]
        assert provider.__class__.__name__ == "WebhookEventProvider"
        assert provider.url == "https://webhook.site/test"
        assert provider.retry_attempts == 5
    finally:
        del os.environ["EVENT_PROVIDERS"]


def test_load_azure_queue_provider():
    """Load Azure Storage Queue provider."""
    config = [
        {
            "type": "azure_storage_queue",
            "connection_string": "UseDevelopmentStorage=true",
            "queue_name": "dicom-events",
            "enabled": True
        }
    ]

    os.environ["EVENT_PROVIDERS"] = json.dumps(config)

    try:
        providers = load_providers_from_config()

        assert len(providers) == 1
        provider = providers[0]
        assert provider.__class__.__name__ == "AzureStorageQueueProvider"
    finally:
        del os.environ["EVENT_PROVIDERS"]


def test_load_providers_empty_config():
    """Empty config returns empty list."""
    if "EVENT_PROVIDERS" in os.environ:
        del os.environ["EVENT_PROVIDERS"]

    providers = load_providers_from_config()

    assert providers == []


def test_load_providers_invalid_type():
    """Invalid provider type is skipped with warning."""
    config = [
        {
            "type": "invalid_type",
            "enabled": True
        }
    ]

    os.environ["EVENT_PROVIDERS"] = json.dumps(config)

    try:
        providers = load_providers_from_config()

        # Should return empty list and log warning
        assert providers == []
    finally:
        del os.environ["EVENT_PROVIDERS"]
```

### Step 2: Run test to verify it fails

**Run:** `pytest tests/services/test_event_config.py -v`

**Expected:** `ModuleNotFoundError: No module named 'app.services.events.config'`

### Step 3: Write minimal implementation

**Create:** `app/services/events/config.py`

```python
"""Configuration loading for event providers."""

import json
import logging
import os
from typing import Any

from app.services.events.providers import (
    AzureStorageQueueProvider,
    EventProvider,
    FileEventProvider,
    InMemoryEventProvider,
    WebhookEventProvider,
)

logger = logging.getLogger(__name__)


def load_providers_from_config() -> list[EventProvider]:
    """Load event providers from EVENT_PROVIDERS environment variable."""
    config_json = os.getenv("EVENT_PROVIDERS")
    if not config_json:
        return []

    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse EVENT_PROVIDERS: {e}")
        return []

    providers = []

    for provider_config in config:
        if not provider_config.get("enabled", False):
            continue

        provider_type = provider_config.get("type")

        try:
            if provider_type == "in_memory":
                provider = InMemoryEventProvider()

            elif provider_type == "file":
                provider = FileEventProvider(provider_config["path"])

            elif provider_type == "webhook":
                provider = WebhookEventProvider(
                    url=provider_config["url"],
                    retry_attempts=provider_config.get("retry_attempts", 3)
                )

            elif provider_type == "azure_storage_queue":
                provider = AzureStorageQueueProvider(
                    connection_string=provider_config["connection_string"],
                    queue_name=provider_config["queue_name"]
                )

            else:
                logger.warning(f"Unknown provider type: {provider_type}")
                continue

            providers.append(provider)

        except KeyError as e:
            logger.error(f"Missing required config for {provider_type}: {e}")
            continue
        except Exception as e:
            logger.error(f"Failed to create provider {provider_type}: {e}")
            continue

    return providers
```

**Modify:** `app/services/events/__init__.py`

```python
"""Event publishing services."""

from app.services.events.config import load_providers_from_config
from app.services.events.manager import EventManager
from app.services.events.providers import (
    AzureStorageQueueProvider,
    EventProvider,
    FileEventProvider,
    InMemoryEventProvider,
    WebhookEventProvider,
)

__all__ = [
    "AzureStorageQueueProvider",
    "EventManager",
    "EventProvider",
    "FileEventProvider",
    "InMemoryEventProvider",
    "WebhookEventProvider",
    "load_providers_from_config",
]
```

### Step 4: Run test to verify it passes

**Run:** `pytest tests/services/test_event_config.py -v`

**Expected:** All 6 tests pass

### Step 5: Commit

```bash
git add app/services/events/ tests/services/test_event_config.py
git commit -m "feat: add provider configuration loading

- Load providers from EVENT_PROVIDERS env variable (JSON)
- Support in_memory, file, webhook, azure_storage_queue types
- Skip disabled providers
- Graceful error handling with logging

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 9: Integrate EventManager into FastAPI Lifespan

**Files:**
- Modify: `main.py`
- Test: Manual testing (smoke test addition later)

### Step 1: Write test placeholder

**Note:** This will be tested via smoke tests in later task. For now, we'll implement and verify manually.

### Step 2: Modify main.py to add EventManager

**Modify:** `main.py`

```python
"""
Azure Healthcare Workspace Emulator

A Docker-based local emulator for Azure Health Data Services DICOM Service.
Drop-in replacement for development and testing — like Azurite for Storage,
but for Healthcare DICOM Service.

MIT License - KostLabs
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app.routers import dicomweb, changefeed, extended_query_tags, operations
from app.services.events import EventManager, load_providers_from_config


# Global event manager instance
event_manager: EventManager | None = None


def get_event_manager() -> EventManager:
    """Get the global event manager instance."""
    global event_manager
    if event_manager is None:
        raise RuntimeError("Event manager not initialized")
    return event_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables and initialize event manager on startup."""
    global event_manager

    # Create database tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Initialize event providers
    providers = load_providers_from_config()
    event_manager = EventManager(providers)

    yield

    # Cleanup
    if event_manager:
        await event_manager.close()


app = FastAPI(
    title="Azure Healthcare Workspace Emulator",
    description=(
        "Local emulator for Azure Health Data Services DICOM Service (v2 API). "
        "Provides DICOMweb (STOW-RS, WADO-RS, QIDO-RS) plus Azure-specific APIs "
        "(Change Feed, Extended Query Tags, Operations)."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── DICOMweb Standard ──────────────────────────────────────────────
app.include_router(dicomweb.router, prefix="/v2", tags=["DICOMweb"])

# ── Azure Custom APIs ──────────────────────────────────────────────
app.include_router(changefeed.router, prefix="/v2", tags=["Change Feed"])
app.include_router(extended_query_tags.router, prefix="/v2", tags=["Extended Query Tags"])
app.include_router(operations.router, prefix="/v2", tags=["Operations"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy", "service": "azure-healthcare-workspace-emulator"}
```

### Step 3: Verify manually

**Run:** `uv run uvicorn main:app --host 0.0.0.0 --port 8080`

**Expected:** Server starts without errors

**Test:** `curl http://localhost:8080/health`

**Expected:** `{"status":"healthy","service":"azure-healthcare-workspace-emulator"}`

### Step 4: Commit

```bash
git add main.py
git commit -m "feat: integrate EventManager into FastAPI lifespan

- Initialize event providers from config on startup
- Create global event_manager instance
- Clean up providers on shutdown
- Export get_event_manager() for routers

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 10: Publish Events from STOW-RS (Store)

**Files:**
- Modify: `app/routers/dicomweb.py`
- Test: Integration test in smoke_test.py (later task)

### Step 1: Read current STOW-RS implementation

**Run:** `head -100 app/routers/dicomweb.py`

**(Implementation note: Implementer should read current code and identify where to add event publishing after successful store)**

### Step 2: Add event publishing to store endpoint

**Modify:** `app/routers/dicomweb.py` (add import at top)

```python
from main import get_event_manager
from app.models.events import DicomEvent
```

**Modify:** `app/routers/dicomweb.py` (in the store_instances function, after successful DB commit)

```python
# After instance is successfully stored in database:
# Publish DicomImageCreated event
try:
    event_manager = get_event_manager()
    event = DicomEvent.from_instance_created(
        study_uid=instance.study_instance_uid,
        series_uid=instance.series_instance_uid,
        instance_uid=instance.sop_instance_uid,
        sequence_number=instance.id,  # Use database ID as sequence
        service_url=str(request.base_url).rstrip("/"),
    )
    await event_manager.publish(event)
except Exception as e:
    # Log but don't fail the DICOM operation
    logger.error(f"Failed to publish event: {e}")
```

### Step 3: Test manually with in-memory provider

**Set environment:**
```bash
export EVENT_PROVIDERS='[{"type":"in_memory","enabled":true}]'
```

**Run:** `uv run uvicorn main:app --reload`

**Store instance:** *(use existing smoke test DICOM instance)*

**Expected:** No errors, instance stored successfully

### Step 4: Commit

```bash
git add app/routers/dicomweb.py
git commit -m "feat: publish DicomImageCreated events from STOW-RS

- Publish event after successful instance storage
- Use database sequence number
- Best-effort: log errors but don't fail DICOM operation

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 11: Publish Events from DELETE

**Files:**
- Modify: `app/routers/dicomweb.py`

### Step 1: Add event publishing to delete endpoints

**Modify:** `app/routers/dicomweb.py` (in delete_instance/series/study functions, after successful delete)

```python
# After deletion is committed:
# Publish DicomImageDeleted event for each deleted instance
try:
    event_manager = get_event_manager()
    for instance in deleted_instances:
        event = DicomEvent.from_instance_deleted(
            study_uid=instance.study_instance_uid,
            series_uid=instance.series_instance_uid,
            instance_uid=instance.sop_instance_uid,
            sequence_number=instance.id,
            service_url=str(request.base_url).rstrip("/"),
        )
        await event_manager.publish(event)
except Exception as e:
    logger.error(f"Failed to publish delete events: {e}")
```

### Step 2: Test manually

**Delete instance:** *(use existing smoke test)*

**Expected:** No errors, instance deleted successfully

### Step 3: Commit

```bash
git add app/routers/dicomweb.py
git commit -m "feat: publish DicomImageDeleted events from DELETE

- Publish event for each deleted instance
- Include all delete operations (instance/series/study)
- Best-effort delivery

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 12: Add /debug/events Endpoint (In-Memory Provider)

**Files:**
- Create: `app/routers/debug.py`
- Modify: `main.py`

### Step 1: Write debug router with /debug/events endpoint

**Create:** `app/routers/debug.py`

```python
"""Debug endpoints for development and testing."""

from fastapi import APIRouter, HTTPException

from main import get_event_manager
from app.services.events.providers import InMemoryEventProvider

router = APIRouter()


@router.get("/debug/events")
async def get_debug_events():
    """
    Get all events from InMemoryEventProvider (if configured).

    Useful for testing and debugging event publishing.
    """
    event_manager = get_event_manager()

    # Find InMemoryEventProvider
    in_memory_provider = None
    for provider in event_manager.providers:
        if isinstance(provider, InMemoryEventProvider):
            in_memory_provider = provider
            break

    if not in_memory_provider:
        raise HTTPException(
            status_code=404,
            detail="InMemoryEventProvider not configured"
        )

    events = in_memory_provider.get_events()

    return {
        "count": len(events),
        "events": [event.to_dict() for event in events]
    }


@router.delete("/debug/events")
async def clear_debug_events():
    """Clear all events from InMemoryEventProvider."""
    event_manager = get_event_manager()

    in_memory_provider = None
    for provider in event_manager.providers:
        if isinstance(provider, InMemoryEventProvider):
            in_memory_provider = provider
            break

    if not in_memory_provider:
        raise HTTPException(
            status_code=404,
            detail="InMemoryEventProvider not configured"
        )

    in_memory_provider.clear()

    return {"message": "Events cleared"}
```

### Step 2: Register debug router in main.py

**Modify:** `main.py` (add import)

```python
from app.routers import dicomweb, changefeed, extended_query_tags, operations, debug
```

**Modify:** `main.py` (add router registration after other routers)

```python
# ── Debug Endpoints ────────────────────────────────────────────────
app.include_router(debug.router, tags=["Debug"])
```

### Step 3: Test endpoint

**Set environment:**
```bash
export EVENT_PROVIDERS='[{"type":"in_memory","enabled":true}]'
```

**Run:** `uv run uvicorn main:app --reload`

**Test:** `curl http://localhost:8080/debug/events`

**Expected:** `{"count": 0, "events": []}`

**Store an instance, then test again:**

**Expected:** `{"count": 1, "events": [...]}`

### Step 4: Commit

```bash
git add app/routers/debug.py main.py
git commit -m "feat: add /debug/events endpoint for testing

- GET /debug/events returns all events from InMemoryEventProvider
- DELETE /debug/events clears event history
- Useful for integration tests and debugging

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 13: Update Docker Compose with Azurite

**Files:**
- Modify: `docker-compose.yml`

### Step 1: Add Azurite service to docker-compose.yml

**Modify:** `docker-compose.yml` (add azurite service after postgres)

```yaml
  azurite:
    image: mcr.microsoft.com/azure-storage/azurite:latest
    ports:
      - "10000:10000"  # Blob
      - "10001:10001"  # Queue
      - "10002:10002"  # Table
    command: "azurite --blobHost 0.0.0.0 --queueHost 0.0.0.0 --tableHost 0.0.0.0"
    healthcheck:
      test: ["CMD", "nc", "-z", "localhost", "10001"]
      interval: 10s
      timeout: 5s
      retries: 5
```

### Step 2: Update emulator service to depend on azurite

**Modify:** `docker-compose.yml` (in emulator service)

```yaml
  emulator:
    # ... existing config ...
    depends_on:
      postgres:
        condition: service_healthy
      azurite:
        condition: service_healthy
    environment:
      DATABASE_URL: "postgresql+asyncpg://emulator:emulator@postgres:5432/dicom_emulator"
      DICOM_STORAGE_DIR: "/data/dicom"
      EVENT_PROVIDERS: |
        [
          {
            "type": "azure_storage_queue",
            "connection_string": "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://azurite:10000/devstoreaccount1;QueueEndpoint=http://azurite:10001/devstoreaccount1;TableEndpoint=http://azurite:10002/devstoreaccount1;",
            "queue_name": "dicom-events",
            "enabled": true
          }
        ]
```

### Step 3: Test docker compose

**Run:** `docker compose down && docker compose up -d`

**Expected:** All 3 services start (postgres, azurite, emulator)

**Verify:** `docker compose ps`

**Expected:** All services healthy

### Step 4: Commit

```bash
git add docker-compose.yml
git commit -m "feat: add Azurite to docker-compose for event queue

- Add azurite service with Queue endpoint
- Configure emulator to use Azurite Storage Queue
- Set EVENT_PROVIDERS environment variable

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 14: Update Smoke Tests for Events

**Files:**
- Modify: `smoke_test.py`

### Step 1: Add Azure Storage Queue verification to smoke tests

**Modify:** `smoke_test.py` (add import at top)

```python
from azure.storage.queue import QueueClient
```

**Modify:** `smoke_test.py` (add new test function before main())

```python
def test_event_publishing():
    """Test event publishing to Azurite queue."""
    print("[9/9] Event Publishing...")

    # Connect to Azurite queue
    try:
        queue_client = QueueClient.from_connection_string(
            "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;",
            "dicom-events"
        )
    except Exception as e:
        print(f"  [SKIP] Azurite not available: {e}")
        return

    # Clear queue
    try:
        queue_client.delete_queue()
    except:
        pass
    queue_client.create_queue()

    # Store an instance
    dcm_file = create_test_dicom()
    files = {"file": ("test.dcm", dcm_file, "application/dicom")}
    response = requests.post(
        f"{base_url}/v2/studies",
        files=files,
        headers={"Content-Type": "multipart/related; type=application/dicom"}
    )
    assert response.status_code == 200, f"STOW failed: {response.text}"

    # Wait for event
    import time
    time.sleep(2)

    # Check for event in queue
    messages = queue_client.receive_messages(max_messages=10)
    messages_list = list(messages)

    assert len(messages_list) > 0, "No events in queue"

    # Verify event format
    import json
    event = json.loads(messages_list[0].content)
    assert event["eventType"] == "Microsoft.HealthcareApis.DicomImageCreated"
    assert "imageStudyInstanceUid" in event["data"]

    print(f"  [PASS] Event published to Azurite queue")
```

**Modify:** `smoke_test.py` (update test count in main)

```python
# Change header from [8/8] to [9/9] everywhere
# Add call to test_event_publishing() before final summary
```

### Step 2: Run smoke tests

**Run:** `python smoke_test.py http://localhost:8080`

**Expected:** All 9 tests pass including event publishing test

### Step 3: Commit

```bash
git add smoke_test.py
git commit -m "test: add event publishing verification to smoke tests

- Verify events are published to Azurite queue
- Check event format matches Azure Event Grid schema
- Skip test gracefully if Azurite unavailable

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Success Criteria

After completing all tasks, verify:

- ✅ All unit tests pass: `pytest tests/ -v`
- ✅ All smoke tests pass: `python smoke_test.py http://localhost:8080`
- ✅ Docker Compose starts all services (postgres, azurite, emulator)
- ✅ Events published to Azurite queue on STOW/DELETE operations
- ✅ Event schema matches Azure Event Grid format
- ✅ /debug/events endpoint works with InMemoryEventProvider
- ✅ DICOM operations succeed even if event publishing fails

---

## Next Phase

This completes Phase 1: Event System Foundation.

Next phases to implement:
- **Phase 2:** WADO-RS Enhancements (frame retrieval, rendered images)
- **Phase 3:** STOW-RS Enhancements (PUT upsert, expiry headers)
- **Phase 4:** QIDO-RS Enhancements (fuzzy/wildcard matching)
- **Phase 5:** UPS-RS Worklist Service

Would you like me to create detailed implementation plans for the remaining phases?
