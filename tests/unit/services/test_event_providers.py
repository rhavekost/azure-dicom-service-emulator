"""Tests for event provider base class."""

import pytest

from app.models.events import DicomEvent
from app.services.events.providers import EventProvider

pytestmark = pytest.mark.unit


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
    event = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")

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
