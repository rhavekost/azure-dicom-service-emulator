"""Unit tests for EventManager gap coverage (timeout, exception, close paths)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.events import DicomEvent
from app.services.events.manager import EventManager

pytestmark = pytest.mark.unit


def make_event() -> DicomEvent:
    return DicomEvent.from_instance_created(
        study_uid="1.2.3",
        series_uid="1.2.3.4",
        instance_uid="1.2.3.4.5",
        sequence_number=1,
        service_url="http://localhost",
    )


@pytest.mark.asyncio
async def test_publish_timeout_does_not_raise():
    """Provider timeout is caught and logged — no exception propagates."""
    provider = MagicMock()
    provider.publish = AsyncMock(side_effect=asyncio.TimeoutError())
    provider.__class__.__name__ = "MockProvider"

    manager = EventManager(providers=[provider], timeout=0.001)
    event = make_event()
    await manager.publish(event)


@pytest.mark.asyncio
async def test_publish_exception_does_not_raise():
    """Provider exception is caught and logged — no exception propagates."""
    provider = MagicMock()
    provider.publish = AsyncMock(side_effect=RuntimeError("boom"))
    provider.__class__.__name__ = "MockProvider"

    manager = EventManager(providers=[provider], timeout=5.0)
    await manager.publish(make_event())


@pytest.mark.asyncio
async def test_publish_batch_timeout_does_not_raise():
    provider = MagicMock()
    provider.publish_batch = AsyncMock(side_effect=asyncio.TimeoutError())
    provider.__class__.__name__ = "MockProvider"

    manager = EventManager(providers=[provider], timeout=0.001)
    await manager.publish_batch([make_event(), make_event()])


@pytest.mark.asyncio
async def test_close_exception_does_not_raise():
    """Provider close() error is caught — no exception propagates."""
    provider = MagicMock()
    provider.close = AsyncMock(side_effect=RuntimeError("close failed"))
    provider.__class__.__name__ = "MockProvider"

    manager = EventManager(providers=[provider])
    await manager.close()


@pytest.mark.asyncio
async def test_publish_successful_provider():
    provider = MagicMock()
    provider.publish = AsyncMock(return_value=None)

    manager = EventManager(providers=[provider])
    await manager.publish(make_event())
    provider.publish.assert_called_once()


@pytest.mark.asyncio
async def test_no_providers_publish_does_not_raise():
    manager = EventManager(providers=[])
    await manager.publish(make_event())
