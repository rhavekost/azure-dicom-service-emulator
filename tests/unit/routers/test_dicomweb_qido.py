"""Unit tests for QIDO-RS (search) endpoints and helper functions."""

import pytest

from app.routers.dicomweb import filter_dicom_json_by_includefield

pytestmark = pytest.mark.unit


def test_search_studies_empty(client):
    response = client.get("/v2/studies")
    assert response.status_code == 200
    assert response.json() == []


def test_search_studies_returns_result(client, stored_instance):
    response = client.get("/v2/studies")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1


def test_search_by_patient_id(client, stored_instance):
    response = client.get("/v2/studies?PatientID=STORE-001")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1


def test_search_by_patient_id_no_match(client, stored_instance):
    response = client.get("/v2/studies?PatientID=DOESNOTEXIST")
    assert response.status_code == 200
    assert response.json() == []


def test_search_by_study_uid(client, stored_instance):
    uid = stored_instance["study_uid"]
    response = client.get(f"/v2/studies?StudyInstanceUID={uid}")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1


def test_search_series_under_study(client, stored_instance):
    study = stored_instance["study_uid"]
    response = client.get(f"/v2/studies/{study}/series")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1


def test_search_instances_under_series(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    response = client.get(f"/v2/studies/{study}/series/{series}/instances")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1


def test_search_with_limit(client, stored_instance):
    response = client.get("/v2/studies?limit=1")
    assert response.status_code == 200
    assert len(response.json()) <= 1


def test_search_with_offset(client, stored_instance):
    response = client.get("/v2/studies?offset=999")
    assert response.status_code == 200
    assert response.json() == []


# Pure function tests — no HTTP needed


def test_filter_dicom_json_by_includefield_all():
    dicom_json = {
        "00100020": {"vr": "LO", "Value": ["P1"]},
        "00080060": {"vr": "CS", "Value": ["CT"]},
    }
    result = filter_dicom_json_by_includefield(dicom_json, "all")
    assert result == dicom_json


def test_filter_dicom_json_by_includefield_none():
    dicom_json = {"00100020": {"vr": "LO", "Value": ["P1"]}}
    result = filter_dicom_json_by_includefield(dicom_json, None)
    assert result == dicom_json


def test_filter_dicom_json_by_includefield_specific():
    dicom_json = {
        "00100020": {"vr": "LO", "Value": ["P1"]},
        "00080060": {"vr": "CS", "Value": ["CT"]},
        "00201208": {"vr": "IS", "Value": [1]},
    }
    result = filter_dicom_json_by_includefield(dicom_json, "00100020")
    assert "00100020" in result
    assert "00080060" not in result
    assert "00201208" not in result


# ── Root-level QIDO-RS endpoints (Gap 1) ─────────────────────────────


def test_get_all_series_empty(client):
    response = client.get("/v2/series")
    assert response.status_code == 200
    assert response.json() == []


def test_get_all_series_returns_result(client, stored_instance):
    response = client.get("/v2/series")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    # Must contain SeriesInstanceUID tag
    assert "0020000E" in data[0]


def test_get_all_series_filter_by_modality(client, stored_instance):
    response = client.get("/v2/series?Modality=CT")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1


def test_get_all_series_filter_no_match(client, stored_instance):
    response = client.get("/v2/series?Modality=NOMATCH")
    assert response.status_code == 200
    assert response.json() == []


def test_get_all_instances_empty(client):
    response = client.get("/v2/instances")
    assert response.status_code == 200
    assert response.json() == []


def test_get_all_instances_returns_result(client, stored_instance):
    response = client.get("/v2/instances")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert "00080018" in data[0]  # SOPInstanceUID


def test_get_all_instances_filter_by_patient_id(client, stored_instance):
    response = client.get("/v2/instances?PatientID=STORE-001")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1


def test_get_study_instances_empty(client, stored_instance):
    response = client.get("/v2/studies/1.2.3.nonexistent/instances")
    assert response.status_code == 200
    assert response.json() == []


def test_get_study_instances_returns_result(client, stored_instance):
    study = stored_instance["study_uid"]
    response = client.get(f"/v2/studies/{study}/instances")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert "00080018" in data[0]  # SOPInstanceUID


def test_get_study_instances_scoped_to_study(client, stored_instance):
    study = stored_instance["study_uid"]
    response = client.get(f"/v2/studies/{study}/instances")
    assert response.status_code == 200
    for inst in response.json():
        assert inst["0020000D"]["Value"][0] == study
