"""Unit tests for WADO-RS (retrieve) endpoints."""

import pytest

pytestmark = pytest.mark.unit


def test_retrieve_study_returns_multipart(client, stored_instance):
    uid = stored_instance["study_uid"]
    response = client.get(f"/v2/studies/{uid}")
    assert response.status_code == 200
    assert "multipart/related" in response.headers["content-type"]


def test_retrieve_series_returns_multipart(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    response = client.get(f"/v2/studies/{study}/series/{series}")
    assert response.status_code == 200
    assert "multipart/related" in response.headers["content-type"]


def test_retrieve_instance_returns_multipart(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    sop = stored_instance["sop_uid"]
    response = client.get(f"/v2/studies/{study}/series/{series}/instances/{sop}")
    assert response.status_code == 200
    assert "multipart/related" in response.headers["content-type"]


def test_retrieve_nonexistent_study_returns_404(client):
    response = client.get("/v2/studies/1.2.3.4.5.6.7.8.9.notreal")
    assert response.status_code == 404


def test_retrieve_study_metadata(client, stored_instance):
    uid = stored_instance["study_uid"]
    response = client.get(
        f"/v2/studies/{uid}/metadata",
        headers={"Accept": "application/dicom+json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0


def test_retrieve_series_metadata(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    response = client.get(
        f"/v2/studies/{study}/series/{series}/metadata",
        headers={"Accept": "application/dicom+json"},
    )
    assert response.status_code == 200


def test_retrieve_instance_metadata(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    sop = stored_instance["sop_uid"]
    response = client.get(
        f"/v2/studies/{study}/series/{series}/instances/{sop}/metadata",
        headers={"Accept": "application/dicom+json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_retrieve_frames(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    sop = stored_instance["sop_uid"]
    response = client.get(f"/v2/studies/{study}/series/{series}/instances/{sop}/frames/1")
    assert response.status_code == 200


def test_retrieve_frames_out_of_range_returns_404(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    sop = stored_instance["sop_uid"]
    response = client.get(f"/v2/studies/{study}/series/{series}/instances/{sop}/frames/999")
    assert response.status_code == 404


def test_rendered_instance_jpeg(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    sop = stored_instance["sop_uid"]
    response = client.get(
        f"/v2/studies/{study}/series/{series}/instances/{sop}/rendered",
        headers={"Accept": "image/jpeg"},
    )
    assert response.status_code == 200


def test_invalid_accept_returns_406(client, stored_instance):
    study = stored_instance["study_uid"]
    response = client.get(
        f"/v2/studies/{study}",
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 406
