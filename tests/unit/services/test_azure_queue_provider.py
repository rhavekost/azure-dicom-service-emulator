"""Tests for AzureStorageQueueProvider."""

import asyncio
import threading
import time
from unittest.mock import Mock, patch

import pytest

from app.models.events import DicomEvent
from app.services.events.providers import AzureStorageQueueProvider

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_azure_queue_provider_publish():
    """AzureStorageQueueProvider sends message to Azure Queue."""
    mock_queue = Mock()
    mock_queue.send_message = Mock()

    with patch(
        "app.services.events.providers.QueueClient.from_connection_string", return_value=mock_queue
    ):
        provider = AzureStorageQueueProvider(
            connection_string="UseDevelopmentStorage=true", queue_name="test-queue"
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

    with patch(
        "app.services.events.providers.QueueClient.from_connection_string", return_value=mock_queue
    ):
        provider = AzureStorageQueueProvider(
            connection_string="UseDevelopmentStorage=true", queue_name="test-queue"
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

    with patch(
        "app.services.events.providers.QueueClient.from_connection_string", return_value=mock_queue
    ):
        provider = AzureStorageQueueProvider(
            connection_string="UseDevelopmentStorage=true", queue_name="test-queue"
        )

        await provider.close()

        mock_queue.close.assert_called_once()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Round-2 changes: bounded asyncio.gather inside publish_batch
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.asyncio
async def test_azure_queue_provider_publish_batch_respects_concurrency_cap():
    """Concurrent in-flight ``send_message`` count never exceeds the configured cap.

    The provider runs each event through ``asyncio.to_thread`` under a
    bounded ``asyncio.Semaphore``.  We instrument the Mock to record the
    high-water-mark of concurrent threads inside ``send_message`` and
    assert it stays at-or-below the cap.
    """
    mock_queue = Mock()
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def slow_send(_message: str) -> None:
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        # Sleep just long enough that the semaphore has to enforce the cap;
        # too short and the gather might serialise naturally.
        time.sleep(0.02)
        with lock:
            in_flight -= 1

    mock_queue.send_message = Mock(side_effect=slow_send)

    with patch(
        "app.services.events.providers.QueueClient.from_connection_string", return_value=mock_queue
    ):
        provider = AzureStorageQueueProvider(
            connection_string="UseDevelopmentStorage=true",
            queue_name="test-queue",
            batch_concurrency=4,
        )

        events = [
            DicomEvent.from_instance_created(
                "1.2.3", "4.5.6", f"7.8.9.{i}", i, "http://localhost"
            )
            for i in range(20)
        ]

        await provider.publish_batch(events)

    assert mock_queue.send_message.call_count == 20
    assert peak <= 4, f"Peak concurrency {peak} exceeded the cap of 4"
    # Sanity check: with 20 events across 4 slots we should observe more
    # than 1 concurrent send at some point (otherwise the test is no-op).
    assert peak >= 2, f"Peak concurrency {peak} suggests the gather serialised"


@pytest.mark.asyncio
async def test_azure_queue_provider_publish_batch_isolates_per_event_failures(caplog):
    """One event raising must not poison the rest of the batch."""
    mock_queue = Mock()
    sent_messages: list[str] = []
    lock = threading.Lock()

    def flaky_send(message: str) -> None:
        with lock:
            sent_messages.append(message)
        # The middle event blows up — the other two should still complete.
        if "BAD-SOP" in message:
            raise RuntimeError("simulated send_message failure")

    mock_queue.send_message = Mock(side_effect=flaky_send)

    with patch(
        "app.services.events.providers.QueueClient.from_connection_string", return_value=mock_queue
    ):
        provider = AzureStorageQueueProvider(
            connection_string="UseDevelopmentStorage=true",
            queue_name="test-queue",
            batch_concurrency=8,
        )

        events = [
            DicomEvent.from_instance_created("1.2.3", "4.5.6", "GOOD-SOP-1", 1, "http://localhost"),
            DicomEvent.from_instance_created("1.2.3", "4.5.6", "BAD-SOP", 2, "http://localhost"),
            DicomEvent.from_instance_created("1.2.3", "4.5.6", "GOOD-SOP-3", 3, "http://localhost"),
        ]

        # publish_batch must NOT raise even though one send_message did.
        await provider.publish_batch(events)

    # All three events were dispatched (the bad one tried, then raised).
    assert mock_queue.send_message.call_count == 3
    # The two good events made it through.
    assert any("GOOD-SOP-1" in m for m in sent_messages)
    assert any("GOOD-SOP-3" in m for m in sent_messages)
    # And the failure was surfaced via the logger so operators see it.
    assert any(
        "send_message" in record.message and "failed" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_azure_queue_provider_empty_batch_is_a_noop():
    """An empty event list must not touch the queue at all."""
    mock_queue = Mock()
    mock_queue.send_message = Mock()

    with patch(
        "app.services.events.providers.QueueClient.from_connection_string", return_value=mock_queue
    ):
        provider = AzureStorageQueueProvider(
            connection_string="UseDevelopmentStorage=true", queue_name="test-queue"
        )
        await provider.publish_batch([])

    mock_queue.send_message.assert_not_called()


def test_azure_queue_provider_rejects_zero_concurrency():
    """``batch_concurrency`` < 1 is a configuration error."""
    with patch(
        "app.services.events.providers.QueueClient.from_connection_string", return_value=Mock()
    ):
        with pytest.raises(ValueError):
            AzureStorageQueueProvider(
                connection_string="UseDevelopmentStorage=true",
                queue_name="test-queue",
                batch_concurrency=0,
            )
