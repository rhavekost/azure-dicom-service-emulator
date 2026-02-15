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
