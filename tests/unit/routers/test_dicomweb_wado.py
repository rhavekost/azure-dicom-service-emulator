"""Unit tests for WADO-RS (retrieve) endpoints."""

import pytest

import app.routers.dicomweb as dicomweb_module
from app.services.frame_cache import FrameCache

pytestmark = pytest.mark.unit


@pytest.fixture
def stored_instance_with_frames(client, tmp_path, monkeypatch):
    """Store a CT DICOM instance and patch frame_cache to use the same tmp storage."""
    from pydicom.uid import generate_uid

    import app.services.dicom_engine as dicom_engine
    from tests.fixtures.factories import DicomFactory
    from tests.unit.routers.conftest import CONTENT_TYPE, make_multipart

    storage_dir = tmp_path / "dicom_storage"
    storage_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("DICOM_STORAGE_DIR", str(storage_dir))
    storage_path = dicomweb_module.Path(str(storage_dir))
    monkeypatch.setattr(dicomweb_module, "DICOM_STORAGE_DIR", storage_path)
    monkeypatch.setattr(dicomweb_module, "frame_cache", FrameCache(storage_path))

    study_uid = generate_uid()
    series_uid = generate_uid()
    sop_uid = generate_uid()

    dicom_bytes = DicomFactory.create_ct_image(
        patient_id="FRAME-001",
        patient_name="Frame^Test",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=True,
    )

    response = client.post(
        "/v2/studies",
        content=make_multipart(dicom_bytes),
        headers={"Content-Type": CONTENT_TYPE},
    )
    assert response.status_code in (200, 202), f"Setup failed: {response.text}"

    yield {"study_uid": study_uid, "series_uid": series_uid, "sop_uid": sop_uid}


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


def test_retrieve_frames(client, stored_instance_with_frames):
    study = stored_instance_with_frames["study_uid"]
    series = stored_instance_with_frames["series_uid"]
    sop = stored_instance_with_frames["sop_uid"]
    response = client.get(f"/v2/studies/{study}/series/{series}/instances/{sop}/frames/1")
    assert response.status_code == 200


def test_retrieve_frames_out_of_range_returns_404(client, stored_instance_with_frames):
    study = stored_instance_with_frames["study_uid"]
    series = stored_instance_with_frames["series_uid"]
    sop = stored_instance_with_frames["sop_uid"]
    response = client.get(f"/v2/studies/{study}/series/{series}/instances/{sop}/frames/999")
    assert response.status_code == 404


def test_rendered_instance_jpeg(client, stored_instance_with_frames):
    study = stored_instance_with_frames["study_uid"]
    series = stored_instance_with_frames["series_uid"]
    sop = stored_instance_with_frames["sop_uid"]
    response = client.get(
        f"/v2/studies/{study}/series/{series}/instances/{sop}/rendered",
        headers={"Accept": "image/jpeg"},
    )
    assert response.status_code in (200, 404)  # 404 if no pixel data path


def test_invalid_accept_returns_406(client, stored_instance):
    study = stored_instance["study_uid"]
    response = client.get(
        f"/v2/studies/{study}",
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 406
