"""Unit tests for debug endpoints."""

import pytest

pytestmark = pytest.mark.unit


def test_get_debug_events_no_provider_returns_404(client):
    """Debug endpoint returns 404 when no InMemoryEventProvider is configured."""
    response = client.get("/debug/events")
    assert response.status_code == 404


def test_delete_debug_events_no_provider_returns_404(client):
    """Clear debug events returns 404 when no InMemoryEventProvider is configured."""
    response = client.delete("/debug/events")
    assert response.status_code == 404
