"""Event provider base class and implementations."""

import json
from abc import ABC, abstractmethod
from pathlib import Path

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
