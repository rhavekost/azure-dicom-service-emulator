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
