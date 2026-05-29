"""Event provider base class and implementations."""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path

import aiofiles
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
        # Create parent directory if needed (non-blocking)
        await asyncio.to_thread(self.file_path.parent.mkdir, parents=True, exist_ok=True)

        # Append event as JSON line (non-blocking)
        async with aiofiles.open(self.file_path, "a") as f:
            await f.write(json.dumps(event.to_dict()) + "\n")

    async def publish_batch(self, events: list[DicomEvent]) -> None:
        """Append batch of events to JSON Lines file."""
        await asyncio.to_thread(self.file_path.parent.mkdir, parents=True, exist_ok=True)

        async with aiofiles.open(self.file_path, "a") as f:
            for event in events:
                await f.write(json.dumps(event.to_dict()) + "\n")


class WebhookEventProvider(EventProvider):
    """Webhook-based event provider with retry logic.

    For batch publishes the same :class:`httpx.AsyncClient` is reused
    across every event.  Each event is its own POST (matching the prior
    contract), but the underlying TLS handshake + connection pool is
    shared, which is significant when a STOW request fans out hundreds
    of ``DicomImageCreated`` events.

    ``verify_ssl`` controls TLS certificate verification on the outgoing
    POSTs.  It defaults to ``True`` (verify, the safe default) and can be
    set to ``False`` to target an ``https`` endpoint fronted by a
    self-signed / private CA certificate — common in local development
    behind a reverse proxy.  Disable it only in trusted local
    environments, never in production.
    """

    def __init__(self, url: str, retry_attempts: int = 3, verify_ssl: bool = True):
        self.url = url
        self.retry_attempts = retry_attempts
        self.verify_ssl = verify_ssl
        if not verify_ssl:
            logger.warning(
                "WebhookEventProvider TLS verification is DISABLED for url=%s. "
                "This is intended for local development against self-signed "
                "certificates only; do not disable verification in production.",
                url,
            )

    def _get_retry_decorator(self):
        """Get retry decorator with configured attempts."""
        return retry(
            stop=stop_after_attempt(self.retry_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type((httpx.HTTPError, Exception)),
            reraise=True,
        )

    async def _send_webhook(
        self, event: DicomEvent, client: httpx.AsyncClient | None = None
    ) -> None:
        """Send webhook with exponential backoff retry.

        When ``client`` is supplied, the POST reuses that connection
        pool (no per-event TLS handshake).  When omitted, a one-shot
        client is created — used by the legacy single-event ``publish``
        path and tests that mock ``httpx.AsyncClient.post`` directly.
        """
        retry_decorator = self._get_retry_decorator()

        if client is None:

            @retry_decorator
            async def _do_send_oneshot() -> None:
                async with httpx.AsyncClient(verify=self.verify_ssl) as oneshot:
                    response = await oneshot.post(self.url, json=event.to_dict(), timeout=5.0)
                    response.raise_for_status()

            await _do_send_oneshot()
            return

        @retry_decorator
        async def _do_send_shared() -> None:
            response = await client.post(self.url, json=event.to_dict(), timeout=5.0)
            response.raise_for_status()

        await _do_send_shared()

    async def publish(self, event: DicomEvent) -> None:
        """Publish event via webhook POST."""
        await self._send_webhook(event)

    async def publish_batch(self, events: list[DicomEvent]) -> None:
        """POST each event with a single shared httpx.AsyncClient.

        Connection reuse + HTTP keep-alive turn N TLS handshakes into 1,
        which is the bottleneck for high-fanout STOW requests.  Per-event
        retries are still independent so a transient failure on one
        event doesn't block the rest.
        """
        if not events:
            return
        async with httpx.AsyncClient(verify=self.verify_ssl) as client:
            for event in events:
                await self._send_webhook(event, client=client)


class AzureStorageQueueProvider(EventProvider):
    """Azure Storage Queue event provider (Azurite compatible).

    ``send_message`` is a synchronous SDK call, so each publish hops to
    the default thread pool via :func:`asyncio.to_thread`.  For STOW
    fan-out batches (potentially thousands of events) a serial loop here
    becomes the round-trip-bound bottleneck of the whole publish path.

    :meth:`publish_batch` therefore runs the per-event sends through a
    bounded :class:`asyncio.Semaphore`-throttled :func:`asyncio.gather`.
    The semaphore is critical: an unbounded gather would submit every
    event to the default thread pool at once, starving every other
    ``to_thread`` caller in the app (``aiofiles``, parse-pool dispatch,
    etc.).  The cap is configurable via
    ``EVENT_AZURE_QUEUE_BATCH_CONCURRENCY`` (default 16) — Azurite + the
    SDK client are both naturally bounded around that range.
    """

    def __init__(
        self,
        connection_string: str,
        queue_name: str,
        *,
        batch_concurrency: int | None = None,
    ):
        self.queue_client = QueueClient.from_connection_string(connection_string, queue_name)
        if batch_concurrency is None:
            from app.config import EVENT_AZURE_QUEUE_BATCH_CONCURRENCY

            batch_concurrency = EVENT_AZURE_QUEUE_BATCH_CONCURRENCY
        if batch_concurrency < 1:
            raise ValueError("batch_concurrency must be >= 1")
        self._batch_concurrency = batch_concurrency

    async def publish(self, event: DicomEvent) -> None:
        """Send event to Azure Storage Queue."""
        message = json.dumps(event.to_dict())
        await asyncio.to_thread(self.queue_client.send_message, message)

    async def publish_batch(self, events: list[DicomEvent]) -> None:
        """Send batch of events through a bounded ``asyncio.gather``.

        Per-event failures are isolated: ``return_exceptions=True`` lets
        the rest of the batch proceed, then we log a summary of any
        failures at the end.  The outbox sweeper picks up anything we
        missed on the next process boot, so a transient queue blip
        doesn't lose events permanently.
        """
        if not events:
            return

        sem = asyncio.Semaphore(self._batch_concurrency)

        async def _send(event: DicomEvent) -> None:
            message = json.dumps(event.to_dict())
            async with sem:
                await asyncio.to_thread(self.queue_client.send_message, message)

        results = await asyncio.gather(*(_send(e) for e in events), return_exceptions=True)
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            logger.warning(
                "AzureStorageQueueProvider.publish_batch: %d/%d send_message call(s) failed; "
                "first error: %s",
                len(failures),
                len(events),
                failures[0],
            )

    async def close(self) -> None:
        """Close queue client connection."""
        await asyncio.to_thread(self.queue_client.close)
