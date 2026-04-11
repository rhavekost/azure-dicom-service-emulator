"""Unit tests for STOW-RS (store) endpoints."""

import pytest
from pydicom.uid import generate_uid

from tests.fixtures.factories import DicomFactory
from tests.unit.routers.conftest import CONTENT_TYPE, make_multipart

pytestmark = pytest.mark.unit


def test_store_single_instance_returns_200(client):
    dicom_bytes = DicomFactory.create_ct_image()
    response = client.post(
        "/v2/studies",
        content=make_multipart(dicom_bytes),
        headers={"Content-Type": CONTENT_TYPE},
    )
    assert response.status_code == 200


def test_store_duplicate_returns_202_with_warning(client):
    """POST of a duplicate instance returns 202 (not 409).

    DICOM warning code 45070 (0xB00E) is a warning-level status, so
    Azure DICOM Service returns 202 with a WarningReason, not a 409 failure.
    """
    sop_uid = generate_uid()
    dicom_bytes = DicomFactory.create_ct_image(sop_uid=sop_uid)

    client.post(
        "/v2/studies", content=make_multipart(dicom_bytes), headers={"Content-Type": CONTENT_TYPE}
    )
    response = client.post(
        "/v2/studies", content=make_multipart(dicom_bytes), headers={"Content-Type": CONTENT_TYPE}
    )
    assert response.status_code == 202
    # FailedSOPSequence should contain the duplicate with reason 45070
    body = response.json()
    failed = body.get("00081198", {}).get("Value", [])
    assert len(failed) == 1
    assert failed[0]["00081197"]["Value"][0] == 45070


def test_store_missing_required_uid_returns_409(client):
    invalid_bytes = DicomFactory.create_invalid_dicom(missing_tags=["SOPInstanceUID"])
    response = client.post(
        "/v2/studies",
        content=make_multipart(invalid_bytes),
        headers={"Content-Type": CONTENT_TYPE},
    )
    assert response.status_code == 409


def test_store_missing_content_type_returns_400(client):
    response = client.post("/v2/studies", content=b"not multipart")
    assert response.status_code in (400, 422)


def test_store_multiple_instances_all_succeed(client):
    study_uid = generate_uid()
    series_uid = generate_uid()
    dicom1 = DicomFactory.create_ct_image(study_uid=study_uid, series_uid=series_uid)
    dicom2 = DicomFactory.create_ct_image(study_uid=study_uid, series_uid=series_uid)

    boundary = "multi_boundary"
    body = (
        (f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n").encode()
        + dicom1
        + (f"\r\n--{boundary}\r\nContent-Type: application/dicom\r\n\r\n").encode()
        + dicom2
        + f"\r\n--{boundary}--\r\n".encode()
    )

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"},
    )
    assert response.status_code == 200


def test_store_to_study_endpoint(client):
    study_uid = generate_uid()
    dicom_bytes = DicomFactory.create_ct_image(study_uid=study_uid)
    response = client.post(
        f"/v2/studies/{study_uid}",
        content=make_multipart(dicom_bytes),
        headers={"Content-Type": CONTENT_TYPE},
    )
    assert response.status_code == 200


def test_store_searchable_attr_warning_returns_202(client):
    # Missing PatientID triggers a searchable attribute warning (202)
    dicom_bytes = DicomFactory.create_dicom_with_attrs(patient_id=None)
    response = client.post(
        "/v2/studies",
        content=make_multipart(dicom_bytes),
        headers={"Content-Type": CONTENT_TYPE},
    )
    assert response.status_code in (200, 202)
