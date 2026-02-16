"""Tests for UPS-RS subscription stubs (501 Not Implemented)."""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_subscribe_returns_501(client: TestClient):
    """Subscribe endpoint returns 501."""
    response = client.post("/v2/workitems/1.2.3/subscribers/MYAET")
    assert response.status_code == 501
    assert "not implemented" in response.json()["detail"].lower()


def test_unsubscribe_returns_501(client: TestClient):
    """Unsubscribe endpoint returns 501."""
    response = client.delete("/v2/workitems/1.2.3/subscribers/MYAET")
    assert response.status_code == 501
    assert "not implemented" in response.json()["detail"].lower()


def test_list_subscriptions_returns_501(client: TestClient):
    """List subscriptions returns 501."""
    response = client.get("/v2/workitems/subscriptions")
    assert response.status_code == 501
    assert "not implemented" in response.json()["detail"].lower()
