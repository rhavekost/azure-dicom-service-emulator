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


@pytest.mark.asyncio
async def test_in_memory_provider_health_check():
    """InMemoryProvider health check returns True."""
    provider = InMemoryEventProvider()
    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_in_memory_provider_close():
    """InMemoryProvider close is safe to call."""
    provider = InMemoryEventProvider()
    await provider.close()  # Should not raise
