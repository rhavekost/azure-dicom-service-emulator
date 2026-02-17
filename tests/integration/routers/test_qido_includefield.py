"""Integration tests for QIDO-RS includefield parameter (Task 8).

Tests the includefield query parameter for QIDO-RS endpoints to filter
returned DICOM JSON attributes.

Test Coverage (8 tests):
- Basic filtering (3 tests): single tag, multiple tags, all
- Default behavior (1 test): no includefield returns default set
- Required tags (1 test): always included regardless of includefield
- Edge cases (3 tests): invalid tags, empty value, series/instance levels

Implementation Notes:
- includefield=all: returns all attributes from dicom_json
- includefield=<tag>: returns only requested tags + required return tags
- No includefield: returns default set of study-level attributes
- Required tags (always included): StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID, SOPClassUID
- Invalid tags are silently ignored (DICOM lenient behavior)
"""

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.factories import DicomFactory

pytestmark = pytest.mark.integration


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helper Functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def build_multipart_request(
    dicom_files: list[bytes], boundary: str = "boundary123"
) -> tuple[bytes, str]:
    """Build multipart/related request body for STOW-RS."""
    parts = []
    for dcm_data in dicom_files:
        part = (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n" f"\r\n").encode()
        part += dcm_data + b"\r\n"
        parts.append(part)

    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    content_type = f'multipart/related; type="application/dicom"; boundary={boundary}'

    return body, content_type


def store_dicom(client: TestClient, dicom_bytes: bytes) -> dict:
    """Store a DICOM instance and return the STOW-RS response JSON."""
    body, content_type = build_multipart_request([dicom_bytes])
    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert response.status_code == 200, f"STOW-RS failed: {response.status_code} {response.text}"
    return response.json()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Basic Filtering Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_includefield_single_tag(client: TestClient):
    """QIDO with includefield=00100010 returns only PatientName + required tags."""
    dcm = DicomFactory.create_ct_image(
        patient_id="INCF-001",
        patient_name="SingleTag^Test",
        study_date="20260217",
        accession_number="ACC-INCF-001",
    )
    store_dicom(client, dcm)

    # Request only PatientName
    response = client.get(
        "/v2/studies",
        params={"PatientID": "INCF-001", "includefield": "00100010"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1

    study = studies[0]
    # Should have PatientName
    assert "00100010" in study
    assert study["00100010"]["Value"][0]["Alphabetic"] == "SingleTag^Test"

    # Should have required tag StudyInstanceUID
    assert "0020000D" in study

    # Should NOT have other default fields
    assert "00100020" not in study  # PatientID
    assert "00080020" not in study  # StudyDate
    assert "00080050" not in study  # AccessionNumber


def test_includefield_multiple_tags(client: TestClient):
    """QIDO with includefield=00100010,00080020 returns requested tags + required."""
    dcm = DicomFactory.create_ct_image(
        patient_id="INCF-002",
        patient_name="MultiTag^Test",
        study_date="20260217",
        accession_number="ACC-INCF-002",
    )
    store_dicom(client, dcm)

    # Request PatientName and StudyDate
    response = client.get(
        "/v2/studies",
        params={"PatientID": "INCF-002", "includefield": "00100010,00080020"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1

    study = studies[0]
    # Should have requested tags
    assert "00100010" in study  # PatientName
    assert "00080020" in study  # StudyDate
    assert study["00100010"]["Value"][0]["Alphabetic"] == "MultiTag^Test"
    assert study["00080020"]["Value"][0] == "20260217"

    # Should have required tag
    assert "0020000D" in study  # StudyInstanceUID

    # Should NOT have unrequested fields
    assert "00100020" not in study  # PatientID
    assert "00080050" not in study  # AccessionNumber


def test_includefield_all(client: TestClient):
    """QIDO with includefield=all returns all available attributes."""
    dcm = DicomFactory.create_ct_image(
        patient_id="INCF-003",
        patient_name="AllFields^Test",
        study_date="20260217",
        accession_number="ACC-INCF-003",
    )
    store_dicom(client, dcm)

    # Request all fields
    response = client.get(
        "/v2/studies",
        params={"PatientID": "INCF-003", "includefield": "all"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1

    study = studies[0]
    # Should have all standard study-level fields
    assert "0020000D" in study  # StudyInstanceUID
    assert "00100020" in study  # PatientID
    assert "00100010" in study  # PatientName
    assert "00080020" in study  # StudyDate
    assert "00080050" in study  # AccessionNumber
    assert "00080061" in study  # ModalitiesInStudy
    assert "00201206" in study  # NumberOfStudyRelatedSeries
    assert "00201208" in study  # NumberOfStudyRelatedInstances
    # StudyDescription may or may not be present depending on if it was set


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Default Behavior Test
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_includefield_default_behavior(client: TestClient):
    """QIDO without includefield returns the default attribute set."""
    dcm = DicomFactory.create_ct_image(
        patient_id="INCF-004",
        patient_name="DefaultSet^Test",
        study_date="20260217",
        accession_number="ACC-INCF-004",
    )
    store_dicom(client, dcm)

    # Search without includefield
    response = client.get(
        "/v2/studies",
        params={"PatientID": "INCF-004"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1

    study = studies[0]
    # Should have all default study-level fields
    assert "0020000D" in study  # StudyInstanceUID
    assert "00100020" in study  # PatientID
    assert "00100010" in study  # PatientName
    assert "00080020" in study  # StudyDate
    assert "00080050" in study  # AccessionNumber
    assert "00081030" in study  # StudyDescription
    assert "00080061" in study  # ModalitiesInStudy
    assert "00201206" in study  # NumberOfStudyRelatedSeries
    assert "00201208" in study  # NumberOfStudyRelatedInstances


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Required Tags Test
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_includefield_required_tags_always_included(client: TestClient):
    """QIDO with includefield always includes required return tags."""
    dcm = DicomFactory.create_ct_image(
        patient_id="INCF-005",
        patient_name="RequiredTags^Test",
        study_date="20260217",
    )
    store_dicom(client, dcm)

    # Request only StudyDate (not a required tag)
    response = client.get(
        "/v2/studies",
        params={"PatientID": "INCF-005", "includefield": "00080020"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1

    study = studies[0]
    # Should have requested tag
    assert "00080020" in study  # StudyDate

    # Should have required tag even though not requested
    assert "0020000D" in study  # StudyInstanceUID

    # Should NOT have other fields
    assert "00100020" not in study  # PatientID
    assert "00100010" not in study  # PatientName


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Edge Cases Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_includefield_invalid_tag_ignored(client: TestClient):
    """QIDO with invalid includefield tag silently ignores it."""
    dcm = DicomFactory.create_ct_image(
        patient_id="INCF-006",
        patient_name="InvalidTag^Test",
        study_date="20260217",
    )
    store_dicom(client, dcm)

    # Request invalid tag along with valid one
    response = client.get(
        "/v2/studies",
        params={"PatientID": "INCF-006", "includefield": "ZZZZZZZZ,00100010"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1

    study = studies[0]
    # Should have valid requested tag
    assert "00100010" in study
    # Invalid tag should not be present
    assert "ZZZZZZZZ" not in study
    # Should have required tag
    assert "0020000D" in study


def test_includefield_empty_value(client: TestClient):
    """QIDO with empty includefield returns default set."""
    dcm = DicomFactory.create_ct_image(
        patient_id="INCF-007",
        patient_name="EmptyInclude^Test",
        study_date="20260217",
    )
    store_dicom(client, dcm)

    # Request with empty includefield
    response = client.get(
        "/v2/studies",
        params={"PatientID": "INCF-007", "includefield": ""},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1

    study = studies[0]
    # Should return default set (same as no includefield)
    assert "0020000D" in study  # StudyInstanceUID
    assert "00100020" in study  # PatientID
    assert "00100010" in study  # PatientName


def test_includefield_series_level(client: TestClient):
    """QIDO series endpoint respects includefield parameter."""
    from io import BytesIO

    import pydicom

    dcm = DicomFactory.create_ct_image(
        patient_id="INCF-008",
        patient_name="SeriesLevel^Test",
        study_date="20260217",
        modality="CT",
    )
    # Parse DICOM to get study UID
    ds = pydicom.dcmread(BytesIO(dcm))
    study_uid = ds.StudyInstanceUID

    store_dicom(client, dcm)

    # Request series with only Modality
    response = client.get(
        f"/v2/studies/{study_uid}/series",
        params={"includefield": "00080060"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    series = response.json()
    assert len(series) == 1

    series_obj = series[0]
    # Should have requested tag
    assert "00080060" in series_obj  # Modality
    assert series_obj["00080060"]["Value"][0] == "CT"

    # Should have required tags
    assert "0020000D" in series_obj  # StudyInstanceUID
    assert "0020000E" in series_obj  # SeriesInstanceUID

    # Should NOT have unrequested fields
    assert "00200011" not in series_obj  # SeriesNumber
    assert "0008103E" not in series_obj  # SeriesDescription
