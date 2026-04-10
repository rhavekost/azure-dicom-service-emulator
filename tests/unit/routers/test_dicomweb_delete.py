"""Unit tests for DELETE endpoints."""

import pytest

pytestmark = pytest.mark.unit


def test_delete_study(client, stored_instance):
    uid = stored_instance["study_uid"]
    response = client.delete(f"/v2/studies/{uid}")
    assert response.status_code == 204


def test_delete_study_twice_returns_404(client, stored_instance):
    uid = stored_instance["study_uid"]
    client.delete(f"/v2/studies/{uid}")
    response = client.delete(f"/v2/studies/{uid}")
    assert response.status_code == 404


def test_delete_series(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    response = client.delete(f"/v2/studies/{study}/series/{series}")
    assert response.status_code == 204


def test_delete_instance(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    sop = stored_instance["sop_uid"]
    response = client.delete(f"/v2/studies/{study}/series/{series}/instances/{sop}")
    assert response.status_code == 204


def test_delete_nonexistent_study_returns_404(client):
    response = client.delete("/v2/studies/1.2.3.4.5.notreal")
    assert response.status_code == 404


def test_delete_creates_changefeed_entry(client, stored_instance):
    uid = stored_instance["study_uid"]
    client.delete(f"/v2/studies/{uid}")
    cf_response = client.get("/v2/changefeed")
    assert cf_response.status_code == 200
    entries = cf_response.json()
    actions = [e["Action"] for e in entries]
    assert "delete" in actions
