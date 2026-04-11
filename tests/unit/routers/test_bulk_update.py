"""Unit tests for POST /v2/studies/$bulkUpdate endpoint."""

import pytest
from pydicom.uid import generate_uid

from tests.fixtures.factories import DicomFactory

pytestmark = pytest.mark.unit

BOUNDARY = "test_boundary"
CONTENT_TYPE = f"multipart/related; type=application/dicom; boundary={BOUNDARY}"


def make_multipart(dicom_bytes: bytes) -> bytes:
    return (
        (f"--{BOUNDARY}\r\nContent-Type: application/dicom\r\n\r\n").encode()
        + dicom_bytes
        + f"\r\n--{BOUNDARY}--\r\n".encode()
    )


def store_instance(client, tmp_path, monkeypatch, study_uid=None, patient_id="TEST-001"):
    """Helper: store one DICOM instance and return its UIDs."""
    from pathlib import Path

    import app.routers.dicomweb as dicomweb_module
    import app.services.dicom_engine as dicom_engine
    from app.services.frame_cache import FrameCache

    storage_dir = tmp_path / "dicom_storage"
    storage_dir.mkdir(exist_ok=True)
    storage_path = Path(str(storage_dir))
    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("DICOM_STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(dicomweb_module, "DICOM_STORAGE_DIR", storage_path)
    monkeypatch.setattr(dicomweb_module, "frame_cache", FrameCache(storage_path))

    sop_uid = generate_uid()
    series_uid = generate_uid()
    study_uid = study_uid or generate_uid()

    dicom_bytes = DicomFactory.create_ct_image(
        patient_id=patient_id,
        patient_name="Bulk^Test",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
    )
    body = make_multipart(dicom_bytes)
    resp = client.post("/v2/studies", content=body, headers={"Content-Type": CONTENT_TYPE})
    assert resp.status_code in (200, 202)
    return {"study_uid": study_uid, "series_uid": series_uid, "sop_uid": sop_uid}


def test_bulk_update_returns_202(client, tmp_path, monkeypatch):
    uids = store_instance(client, tmp_path, monkeypatch)
    payload = {
        "studyInstanceUids": [uids["study_uid"]],
        "changeDataset": {"00100020": {"vr": "LO", "Value": ["NEW-ID"]}},
    }
    resp = client.post("/v2/studies/$bulkUpdate", json=payload)
    assert resp.status_code == 202
    assert "location" in resp.headers


def test_bulk_update_operation_accessible(client, tmp_path, monkeypatch):
    uids = store_instance(client, tmp_path, monkeypatch)
    payload = {
        "studyInstanceUids": [uids["study_uid"]],
        "changeDataset": {"00100020": {"vr": "LO", "Value": ["OP-TEST"]}},
    }
    resp = client.post("/v2/studies/$bulkUpdate", json=payload)
    location = resp.headers["location"]
    op_path = "/" + location.split("/", 3)[-1]
    op_resp = client.get(op_path)
    assert op_resp.status_code == 200
    assert op_resp.json()["status"] == "succeeded"


def test_bulk_update_empty_study_uids_returns_400(client):
    payload = {
        "studyInstanceUids": [],
        "changeDataset": {"00100020": {"vr": "LO", "Value": ["X"]}},
    }
    resp = client.post("/v2/studies/$bulkUpdate", json=payload)
    assert resp.status_code == 400


def test_bulk_update_missing_change_dataset_returns_400(client):
    payload = {"studyInstanceUids": [generate_uid()]}
    resp = client.post("/v2/studies/$bulkUpdate", json=payload)
    assert resp.status_code in (400, 422)  # validation error (Pydantic or custom guard)


def test_bulk_update_malformed_change_dataset_returns_400(client, tmp_path, monkeypatch):
    uids = store_instance(client, tmp_path, monkeypatch)
    payload = {
        "studyInstanceUids": [uids["study_uid"]],
        "changeDataset": {"00100020": "not-a-dict"},
    }
    resp = client.post("/v2/studies/$bulkUpdate", json=payload)
    assert resp.status_code == 400


def test_bulk_update_nonexistent_study_silently_skipped(client):
    payload = {
        "studyInstanceUids": [generate_uid()],
        "changeDataset": {"00100020": {"vr": "LO", "Value": ["X"]}},
    }
    resp = client.post("/v2/studies/$bulkUpdate", json=payload)
    assert resp.status_code == 202
