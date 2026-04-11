"""Unit tests for debug endpoints."""

import asyncio

import pytest

from app.models.events import DicomEvent
from app.services.events.manager import EventManager
from app.services.events.providers import InMemoryEventProvider

pytestmark = pytest.mark.unit


def test_get_debug_events_no_provider_returns_404(client):
    """Debug endpoint returns 404 when no InMemoryEventProvider is configured."""
    response = client.get("/debug/events")
    assert response.status_code == 404


def test_delete_debug_events_no_provider_returns_404(client):
    """Clear debug events returns 404 when no InMemoryEventProvider is configured."""
    response = client.delete("/debug/events")
    assert response.status_code == 404


@pytest.fixture
def client_with_event_manager(client, monkeypatch):
    """Return a client with InMemoryEventProvider configured in the event manager."""
    in_memory_provider = InMemoryEventProvider()
    event_manager = EventManager(providers=[in_memory_provider])

    import app.dependencies as deps

    monkeypatch.setattr(deps, "_event_manager", event_manager)

    return client, in_memory_provider


def test_get_debug_events_empty_returns_200(client_with_event_manager):
    """GET /debug/events returns 200 with empty list when no events stored."""
    test_client, provider = client_with_event_manager
    response = test_client.get("/debug/events")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 0
    assert data["events"] == []


def test_get_debug_events_with_events_returns_count(client_with_event_manager):
    """GET /debug/events returns events stored in InMemoryEventProvider."""
    test_client, provider = client_with_event_manager

    # Manually add an event to the provider
    event = DicomEvent.from_instance_created(
        study_uid="1.2.3",
        series_uid="1.2.3.4",
        instance_uid="1.2.3.4.5",
        sequence_number=1,
        service_url="http://localhost:8080",
    )
    asyncio.run(provider.publish(event))

    response = test_client.get("/debug/events")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert len(data["events"]) == 1


def test_delete_debug_events_clears_provider(client_with_event_manager):
    """DELETE /debug/events clears all events from InMemoryEventProvider."""
    test_client, provider = client_with_event_manager

    # Add an event first
    event = DicomEvent.from_instance_created(
        study_uid="1.2.3",
        series_uid="1.2.3.4",
        instance_uid="1.2.3.4.5",
        sequence_number=1,
        service_url="http://localhost:8080",
    )
    asyncio.run(provider.publish(event))
    assert len(provider.events) == 1

    response = test_client.delete("/debug/events")
    assert response.status_code == 200
    assert response.json()["message"] == "Events cleared"
    assert len(provider.events) == 0


def test_get_debug_events_no_event_manager_raises_404(client, monkeypatch):
    """GET /debug/events returns 404 when event manager raises RuntimeError."""
    import app.dependencies as deps

    monkeypatch.setattr(deps, "_event_manager", None)

    response = client.get("/debug/events")
    assert response.status_code == 404


def test_delete_debug_events_no_event_manager_raises_404(client, monkeypatch):
    """DELETE /debug/events returns 404 when event manager raises RuntimeError."""
    import app.dependencies as deps

    monkeypatch.setattr(deps, "_event_manager", None)

    response = client.delete("/debug/events")
    assert response.status_code == 404


def test_get_debug_events_provider_not_in_memory_returns_404(client, monkeypatch):
    """GET /debug/events returns 404 when provider is not InMemoryEventProvider."""
    import app.dependencies as deps
    from app.services.events.providers import FileEventProvider

    file_provider = FileEventProvider("/tmp/test_events.jsonl")
    event_manager = EventManager(providers=[file_provider])
    monkeypatch.setattr(deps, "_event_manager", event_manager)

    response = client.get("/debug/events")
    assert response.status_code == 404


def test_delete_debug_events_provider_not_in_memory_returns_404(client, monkeypatch):
    """DELETE /debug/events returns 404 when provider is not InMemoryEventProvider."""
    import app.dependencies as deps
    from app.services.events.providers import FileEventProvider

    file_provider = FileEventProvider("/tmp/test_events.jsonl")
    event_manager = EventManager(providers=[file_provider])
    monkeypatch.setattr(deps, "_event_manager", event_manager)

    response = client.delete("/debug/events")
    assert response.status_code == 404
