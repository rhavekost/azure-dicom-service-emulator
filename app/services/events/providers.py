"""Event provider base class and implementations."""

from abc import ABC, abstractmethod

from app.models.events import DicomEvent


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
