"""Event manager for multi-provider publishing."""

import asyncio
import logging

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
                await asyncio.wait_for(provider.publish(event), timeout=self.timeout)
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
                    provider.publish_batch(events), timeout=self.timeout * len(events)
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
