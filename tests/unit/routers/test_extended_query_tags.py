"""Unit tests for Extended Query Tags API."""

import pytest

pytestmark = pytest.mark.unit

TAG_PAYLOAD = {"tags": [{"path": "00101010", "vr": "AS", "level": "Study"}]}


def test_list_tags_empty(client):
    response = client.get("/v2/extendedquerytags")
    assert response.status_code == 200
    assert response.json() == []


def test_add_tag_returns_202(client):
    response = client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "succeeded"
    assert "id" in data


def test_add_tag_creates_entry(client):
    client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    response = client.get("/v2/extendedquerytags")
    assert response.status_code == 200
    tags = response.json()
    assert len(tags) == 1
    assert tags[0]["Path"] == "00101010"
    assert tags[0]["Status"] == "Ready"
    assert tags[0]["QueryStatus"] == "Enabled"


def test_add_duplicate_tag_returns_409(client):
    client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    response = client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    assert response.status_code == 409


def test_get_tag_by_path(client):
    client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    response = client.get("/v2/extendedquerytags/00101010")
    assert response.status_code == 200
    data = response.json()
    assert data["Path"] == "00101010"
    assert data["VR"] == "AS"


def test_get_tag_not_found(client):
    response = client.get("/v2/extendedquerytags/FFFFFFFF")
    assert response.status_code == 404


def test_delete_tag_returns_204(client):
    client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    response = client.delete("/v2/extendedquerytags/00101010")
    assert response.status_code == 204


def test_delete_tag_removes_from_list(client):
    client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    client.delete("/v2/extendedquerytags/00101010")
    response = client.get("/v2/extendedquerytags")
    assert response.json() == []


def test_delete_tag_not_found_returns_404(client):
    response = client.delete("/v2/extendedquerytags/FFFFFFFF")
    assert response.status_code == 404
