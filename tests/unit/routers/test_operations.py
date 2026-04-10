"""Unit tests for Operations API."""

import uuid

import pytest

pytestmark = pytest.mark.unit

TAG_PAYLOAD = {"tags": [{"path": "00101020", "vr": "DS", "level": "Study"}]}


def test_get_operation_invalid_uuid(client):
    response = client.get("/v2/operations/not-a-uuid")
    assert response.status_code == 400


def test_get_operation_not_found(client):
    random_uuid = str(uuid.uuid4())
    response = client.get(f"/v2/operations/{random_uuid}")
    assert response.status_code == 404


def test_get_operation_valid(client):
    post_response = client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    assert post_response.status_code == 202
    operation_id = post_response.json()["id"]

    response = client.get(f"/v2/operations/{operation_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == operation_id
    assert data["status"] == "succeeded"
    assert "createdTime" in data
    assert "lastUpdatedTime" in data
