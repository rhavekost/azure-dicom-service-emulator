"""
FastAPI dependency injection for application-level singletons.

Centralises access to the EventManager so that route handlers can receive it
via ``Depends(get_event_manager)`` instead of importing from ``main``.
"""

from app.services.events.manager import EventManager

_event_manager: EventManager | None = None


def set_event_manager(manager: EventManager) -> None:
    """Store the global EventManager instance (called once during app startup)."""
    global _event_manager
    _event_manager = manager


def get_event_manager() -> EventManager:
    """Return the global EventManager instance.

    Raises:
        RuntimeError: If the EventManager has not been initialised yet.
    """
    if _event_manager is None:
        raise RuntimeError("EventManager not initialized")
    return _event_manager
