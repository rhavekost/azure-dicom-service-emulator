"""Tests for EventManager."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.models.events import DicomEvent
from app.services.events.manager import EventManager
from app.services.events.providers import EventProvider

pytestmark = pytest.mark.unit


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
