"""Debug endpoints for development and testing."""

from fastapi import APIRouter, HTTPException

from app.services.events.providers import InMemoryEventProvider

router = APIRouter()


@router.get("/debug/events")
async def get_debug_events():
    """
    Get all events from InMemoryEventProvider (if configured).

    Useful for testing and debugging event publishing.
    """
    from main import get_event_manager

    event_manager = get_event_manager()

    # Find InMemoryEventProvider
    in_memory_provider = None
    for provider in event_manager.providers:
        if isinstance(provider, InMemoryEventProvider):
            in_memory_provider = provider
            break

    if not in_memory_provider:
        raise HTTPException(status_code=404, detail="InMemoryEventProvider not configured")

    events = in_memory_provider.get_events()

    return {"count": len(events), "events": [event.to_dict() for event in events]}


@router.delete("/debug/events")
async def clear_debug_events():
    """Clear all events from InMemoryEventProvider."""
    from main import get_event_manager

    event_manager = get_event_manager()

    in_memory_provider = None
    for provider in event_manager.providers:
        if isinstance(provider, InMemoryEventProvider):
            in_memory_provider = provider
            break

    if not in_memory_provider:
        raise HTTPException(status_code=404, detail="InMemoryEventProvider not configured")

    in_memory_provider.clear()

    return {"message": "Events cleared"}
