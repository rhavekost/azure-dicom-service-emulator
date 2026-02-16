"""Event provider base class and implementations."""

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path

import httpx
from azure.storage.queue import QueueClient
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.models.events import DicomEvent

logger = logging.getLogger(__name__)


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


class InMemoryEventProvider(EventProvider):
    """In-memory event provider for testing and debugging."""

    def __init__(self) -> None:
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


class WebhookEventProvider(EventProvider):
    """Webhook-based event provider with retry logic."""

    def __init__(self, url: str, retry_attempts: int = 3):
        self.url = url
        self.retry_attempts = retry_attempts

    def _get_retry_decorator(self):
        """Get retry decorator with configured attempts."""
        return retry(
            stop=stop_after_attempt(self.retry_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type((httpx.HTTPError, Exception)),
            reraise=True,
        )

    async def _send_webhook(self, event: DicomEvent) -> None:
        """Send webhook with exponential backoff retry."""

        # Apply retry decorator dynamically
        @self._get_retry_decorator()
        async def _do_send():
            async with httpx.AsyncClient() as client:
                response = await client.post(self.url, json=event.to_dict(), timeout=5.0)
                response.raise_for_status()

        await _do_send()

    async def publish(self, event: DicomEvent) -> None:
        """Publish event via webhook POST."""
        await self._send_webhook(event)

    async def publish_batch(self, events: list[DicomEvent]) -> None:
        """Publish batch of events via webhook (sends each separately)."""
        for event in events:
            await self._send_webhook(event)


class AzureStorageQueueProvider(EventProvider):
    """Azure Storage Queue event provider (Azurite compatible)."""

    def __init__(self, connection_string: str, queue_name: str):
        self.queue_client = QueueClient.from_connection_string(connection_string, queue_name)

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
