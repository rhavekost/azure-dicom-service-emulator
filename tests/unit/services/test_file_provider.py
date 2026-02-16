
"""Tests for FileEventProvider."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.models.events import DicomEvent
from app.services.events.providers import FileEventProvider

pytestmark = pytest.mark.unit

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
