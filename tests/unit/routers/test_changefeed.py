"""Unit tests for Change Feed API."""

import pytest

pytestmark = pytest.mark.unit


def test_changefeed_empty(client):
    response = client.get("/v2/changefeed")
    assert response.status_code == 200
    assert response.json() == []


def test_changefeed_latest_empty(client):
    response = client.get("/v2/changefeed/latest")
    assert response.status_code == 200
    assert response.json() == {"Sequence": 0}


def test_changefeed_after_store_has_entry(client, stored_instance):
    response = client.get("/v2/changefeed")
    assert response.status_code == 200
    entries = response.json()
    assert len(entries) >= 1
    entry = entries[0]
    assert "Sequence" in entry
    assert "StudyInstanceUID" in entry
    assert "Action" in entry
    assert "State" in entry


def test_changefeed_latest_after_store(client, stored_instance):
    response = client.get("/v2/changefeed/latest")
    assert response.status_code == 200
    data = response.json()
    assert data["Sequence"] >= 1
    assert "StudyInstanceUID" in data


def test_changefeed_starttime_filter(client, stored_instance):
    response = client.get("/v2/changefeed?startTime=2099-01-01T00:00:00Z")
    assert response.status_code == 200
    assert response.json() == []


def test_changefeed_endtime_filter(client, stored_instance):
    response = client.get("/v2/changefeed?endTime=2000-01-01T00:00:00Z")
    assert response.status_code == 200
    assert response.json() == []


def test_changefeed_pagination_offset(client, stored_instance):
    response = client.get("/v2/changefeed?offset=999")
    assert response.status_code == 200
    assert response.json() == []


def test_changefeed_pagination_limit(client, stored_instance):
    response = client.get("/v2/changefeed?limit=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) <= 1
