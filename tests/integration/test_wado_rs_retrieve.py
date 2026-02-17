"""Integration tests for WADO-RS retrieve operations (Phase 2, Task 3).

Tests DICOM instance retrieval via WADO-RS (GET).
Covers study/series/instance retrieval, metadata retrieval,
multipart response format, and Accept header handling.
"""

import re
from io import BytesIO

import pydicom
import pytest
from fastapi.testclient import TestClient
from pydicom.uid import ExplicitVRLittleEndian

from tests.fixtures.factories import DicomFactory

pytestmark = pytest.mark.integration


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helper Functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def build_multipart_request(
    dicom_files: list[bytes], boundary: str = "boundary123"
) -> tuple[bytes, str]:
    """Build multipart/related request body for STOW-RS.

    Args:
        dicom_files: List of DICOM file bytes to include
        boundary: Boundary delimiter string

    Returns:
        Tuple of (request_body, content_type_header)
    """
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
    """Store a DICOM instance and return the STOW-RS response JSON.

    Args:
        client: Test client
        dicom_bytes: DICOM file as bytes

    Returns:
        STOW-RS response JSON dict
    """
    body, content_type = build_multipart_request([dicom_bytes])
    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert response.status_code == 200, f"Store failed: {response.status_code} {response.text}"
    return response.json()


def extract_dicom_from_multipart(content: bytes) -> bytes:
    """Extract DICOM data from a multipart/related response.

    Finds the DICM marker and extracts the DICOM file bytes.

    Args:
        content: Raw multipart response body

    Returns:
        DICOM file as bytes
    """
    dicm_index = content.find(b"DICM")
    if dicm_index < 0:
        raise ValueError("No DICM marker found in multipart response")

    # DICOM file starts 128 bytes before DICM marker (preamble)
    dicom_start = dicm_index - 128
    dicom_data = content[dicom_start:]

    # Find end of DICOM data (before next boundary)
    # Boundaries start with "--boundary-"
    boundary_marker = b"\r\n--boundary-"
    next_boundary = dicom_data.find(boundary_marker, 100)
    if next_boundary > 0:
        dicom_data = dicom_data[:next_boundary]

    return dicom_data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. Study Retrieval (5 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_retrieve_study_returns_all_instances(client: TestClient):
    """GET /v2/studies/{study} returns all instances in the study."""
    study_uid = "1.2.826.0.1.3680043.8.498.70000000000000000000000000000000001"
    series_uid = "1.2.826.0.1.3680043.8.498.70000000000000000000000000000000002"

    # Store 3 instances in the same study/series
    for i in range(3):
        dcm = DicomFactory.create_ct_image(
            patient_id="WADO-STUDY-001",
            study_uid=study_uid,
            series_uid=series_uid,
            with_pixel_data=False,
        )
        store_dicom(client, dcm)

    # Retrieve all instances in the study
    response = client.get(
        f"/v2/studies/{study_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 200
    assert "multipart/related" in response.headers["content-type"]

    # Verify we get 3 DICOM instances in the multipart response
    content = response.content
    dicm_count = content.count(b"DICM")
    assert dicm_count == 3


def test_retrieve_study_multipart_response(client: TestClient):
    """Verify multipart/related response has correct boundary format."""
    study_uid = "1.2.826.0.1.3680043.8.498.70000000000000000000000000000000003"

    dcm = DicomFactory.create_ct_image(
        patient_id="WADO-BOUNDARY-001",
        study_uid=study_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 200

    # Verify content-type includes multipart/related, type, and boundary
    ct = response.headers["content-type"]
    assert "multipart/related" in ct
    assert "application/dicom" in ct
    assert "boundary=" in ct

    # Verify body contains boundary delimiters
    # Extract boundary from content-type
    boundary_match = re.search(r"boundary=([^\s;]+)", ct)
    assert boundary_match is not None
    boundary = boundary_match.group(1)

    # Check opening and closing boundary in body
    assert f"--{boundary}".encode() in response.content
    assert f"--{boundary}--".encode() in response.content


def test_retrieve_study_multiple_series(client: TestClient):
    """Retrieve study that spans multiple series returns all instances."""
    study_uid = "1.2.826.0.1.3680043.8.498.70000000000000000000000000000000004"
    series1 = "1.2.826.0.1.3680043.8.498.70000000000000000000000000000000005"
    series2 = "1.2.826.0.1.3680043.8.498.70000000000000000000000000000000006"

    # Store 2 instances in series 1
    for _ in range(2):
        dcm = DicomFactory.create_ct_image(
            patient_id="WADO-MULTISERIES-001",
            study_uid=study_uid,
            series_uid=series1,
            with_pixel_data=False,
        )
        store_dicom(client, dcm)

    # Store 1 instance in series 2
    dcm = DicomFactory.create_mri_image(
        patient_id="WADO-MULTISERIES-001",
        study_uid=study_uid,
        series_uid=series2,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    # Retrieve entire study
    response = client.get(
        f"/v2/studies/{study_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 200
    assert "multipart/related" in response.headers["content-type"]

    # Should get 3 instances total (2 + 1)
    dicm_count = response.content.count(b"DICM")
    assert dicm_count == 3


def test_retrieve_study_preserves_transfer_syntax(client: TestClient):
    """Retrieved DICOM preserves original transfer syntax (no transcoding)."""
    study_uid = "1.2.826.0.1.3680043.8.498.70000000000000000000000000000000007"
    series_uid = "1.2.826.0.1.3680043.8.498.70000000000000000000000000000000008"
    sop_uid = "1.2.826.0.1.3680043.8.498.70000000000000000000000000000000009"

    dcm = DicomFactory.create_ct_image(
        patient_id="WADO-SYNTAX-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        transfer_syntax=ExplicitVRLittleEndian,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 200

    # Extract DICOM and verify transfer syntax
    dicom_data = extract_dicom_from_multipart(response.content)
    ds = pydicom.dcmread(BytesIO(dicom_data), force=True)
    assert ds.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian


def test_retrieve_empty_study_returns_404(client: TestClient):
    """GET for non-existent study returns 404."""
    nonexistent_uid = "1.2.826.0.1.3680043.8.498.79999999999999999999999999999999999"

    response = client.get(
        f"/v2/studies/{nonexistent_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 404


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. Series Retrieval (5 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_retrieve_series_returns_instances(client: TestClient):
    """GET /v2/studies/{study}/series/{series} returns instances in the series."""
    study_uid = "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000001"
    series_uid = "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000002"

    # Store 2 instances
    for _ in range(2):
        dcm = DicomFactory.create_ct_image(
            patient_id="WADO-SERIES-001",
            study_uid=study_uid,
            series_uid=series_uid,
            with_pixel_data=False,
        )
        store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 200
    assert "multipart/related" in response.headers["content-type"]

    dicm_count = response.content.count(b"DICM")
    assert dicm_count == 2


def test_retrieve_series_multipart_boundary(client: TestClient):
    """Verify series retrieval has correct multipart content-type."""
    study_uid = "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000003"
    series_uid = "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000004"

    dcm = DicomFactory.create_ct_image(
        patient_id="WADO-SERIES-BOUNDARY-001",
        study_uid=study_uid,
        series_uid=series_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 200

    ct = response.headers["content-type"]
    assert "multipart/related" in ct
    assert "application/dicom" in ct
    assert "boundary=" in ct


def test_retrieve_series_with_10_instances(client: TestClient):
    """Batch retrieval of 10 instances from one series."""
    study_uid = "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000005"
    series_uid = "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000006"

    # Store 10 instances
    for i in range(10):
        dcm = DicomFactory.create_ct_image(
            patient_id="WADO-BATCH-001",
            study_uid=study_uid,
            series_uid=series_uid,
            with_pixel_data=False,
        )
        store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 200
    assert "multipart/related" in response.headers["content-type"]

    # Should get all 10 instances
    dicm_count = response.content.count(b"DICM")
    assert dicm_count == 10


def test_retrieve_series_from_multiframe_study(client: TestClient):
    """Retrieve series containing a multiframe instance."""
    # Create and store a multiframe instance
    dcm = DicomFactory.create_multiframe_ct(
        patient_id="WADO-MULTIFRAME-001",
        num_frames=5,
        with_pixel_data=True,
    )

    store_response = store_dicom(client, dcm)

    # Extract study/series UIDs from the store response
    retrieve_url = store_response["00081199"]["Value"][0]["00081190"]["Value"][0]
    # URL format: /v2/studies/{study}/series/{series}/instances/{instance}
    url_parts = retrieve_url.split("/")
    study_uid = url_parts[3]
    series_uid = url_parts[5]

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 200
    assert "multipart/related" in response.headers["content-type"]

    # Should get 1 instance (the multiframe one)
    dicm_count = response.content.count(b"DICM")
    assert dicm_count == 1

    # Verify the retrieved file is large (has pixel data for 5 frames)
    dicom_data = extract_dicom_from_multipart(response.content)
    assert len(dicom_data) > 1000  # Has pixel data


def test_retrieve_nonexistent_series_returns_404(client: TestClient):
    """GET for non-existent series returns 404."""
    study_uid = "1.2.826.0.1.3680043.8.498.80000000000000000000000000000000007"
    series_uid = "1.2.826.0.1.3680043.8.498.89999999999999999999999999999999999"

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 404


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. Instance Retrieval (5 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_retrieve_instance_single_file(client: TestClient):
    """GET .../instances/{instance} returns the specific instance."""
    study_uid = "1.2.826.0.1.3680043.8.498.90000000000000000000000000000000001"
    series_uid = "1.2.826.0.1.3680043.8.498.90000000000000000000000000000000002"
    sop_uid = "1.2.826.0.1.3680043.8.498.90000000000000000000000000000000003"

    dcm = DicomFactory.create_ct_image(
        patient_id="WADO-INSTANCE-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 200

    # Verify we get exactly 1 DICOM instance
    dicm_count = response.content.count(b"DICM")
    assert dicm_count == 1

    # Verify it's the right instance
    dicom_data = extract_dicom_from_multipart(response.content)
    ds = pydicom.dcmread(BytesIO(dicom_data), force=True)
    assert str(ds.SOPInstanceUID) == sop_uid
    assert str(ds.PatientID) == "WADO-INSTANCE-001"


def test_retrieve_instance_content_type_dicom(client: TestClient):
    """Verify instance retrieval returns multipart/related with application/dicom."""
    study_uid = "1.2.826.0.1.3680043.8.498.90000000000000000000000000000000004"
    series_uid = "1.2.826.0.1.3680043.8.498.90000000000000000000000000000000005"
    sop_uid = "1.2.826.0.1.3680043.8.498.90000000000000000000000000000000006"

    dcm = DicomFactory.create_ct_image(
        patient_id="WADO-CT-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 200

    ct = response.headers["content-type"]
    assert "multipart/related" in ct
    assert "application/dicom" in ct


def test_retrieve_instance_exact_bytes(client: TestClient):
    """Retrieved DICOM is byte-for-byte match with what was stored."""
    study_uid = "1.2.826.0.1.3680043.8.498.90000000000000000000000000000000007"
    series_uid = "1.2.826.0.1.3680043.8.498.90000000000000000000000000000000008"
    sop_uid = "1.2.826.0.1.3680043.8.498.90000000000000000000000000000000009"

    original_bytes = DicomFactory.create_ct_image(
        patient_id="WADO-EXACT-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, original_bytes)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 200

    # Extract DICOM from multipart and compare
    retrieved_bytes = extract_dicom_from_multipart(response.content)
    assert retrieved_bytes == original_bytes


def test_retrieve_instance_with_pixel_data(client: TestClient):
    """Retrieve instance that contains pixel data (large file)."""
    study_uid = "1.2.826.0.1.3680043.8.498.90000000000000000000000000000000010"
    series_uid = "1.2.826.0.1.3680043.8.498.90000000000000000000000000000000011"
    sop_uid = "1.2.826.0.1.3680043.8.498.90000000000000000000000000000000012"

    dcm = DicomFactory.create_ct_image(
        patient_id="WADO-PIXEL-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=True,  # 512x512 16-bit = 524288 bytes of pixel data
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 200

    # Verify large file was retrieved
    dicom_data = extract_dicom_from_multipart(response.content)
    assert len(dicom_data) > 500000  # At least 500KB (pixel data is ~512KB)

    # Parse and verify pixel data is present
    ds = pydicom.dcmread(BytesIO(dicom_data), force=True)
    assert hasattr(ds, "PixelData")
    assert ds.Rows == 512
    assert ds.Columns == 512


def test_retrieve_nonexistent_instance_returns_404(client: TestClient):
    """GET for non-existent instance returns 404."""
    study_uid = "1.2.826.0.1.3680043.8.498.90000000000000000000000000000000013"
    series_uid = "1.2.826.0.1.3680043.8.498.90000000000000000000000000000000014"
    sop_uid = "1.2.826.0.1.3680043.8.498.99999999999999999999999999999999999"

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 404


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. Metadata Retrieval (5 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_retrieve_study_metadata(client: TestClient):
    """GET /v2/studies/{study}/metadata returns DICOM JSON metadata."""
    study_uid = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000000001"
    series_uid = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000000002"

    # Store 2 instances in the same study
    for _ in range(2):
        dcm = DicomFactory.create_ct_image(
            patient_id="WADO-META-001",
            study_uid=study_uid,
            series_uid=series_uid,
            with_pixel_data=False,
        )
        store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/metadata",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    assert "application/dicom+json" in response.headers["content-type"]

    metadata = response.json()
    assert isinstance(metadata, list)
    assert len(metadata) == 2

    # Each entry should have DICOM JSON structure
    for entry in metadata:
        assert isinstance(entry, dict)
        # Should have StudyInstanceUID
        assert "0020000D" in entry
        assert entry["0020000D"]["Value"][0] == study_uid


def test_metadata_excludes_pixel_data(client: TestClient):
    """Metadata response must not include pixel data tag (7FE0,0010)."""
    study_uid = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000000003"
    series_uid = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000000004"
    sop_uid = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000000005"

    # Store with pixel data
    dcm = DicomFactory.create_ct_image(
        patient_id="WADO-NOPIXEL-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=True,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/metadata",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    metadata = response.json()
    assert len(metadata) == 1

    # Pixel data tag (7FE00010) must NOT be in metadata
    assert "7FE00010" not in metadata[0]


def test_metadata_json_format(client: TestClient):
    """Metadata follows PS3.18 F.2 DICOM JSON format."""
    study_uid = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000000006"
    series_uid = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000000007"
    sop_uid = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000000008"

    dcm = DicomFactory.create_ct_image(
        patient_id="WADO-FORMAT-001",
        patient_name="Format^Test",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/metadata",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    metadata = response.json()
    assert len(metadata) == 1

    instance_meta = metadata[0]

    # Verify tag format: 8 hex digits
    for tag_key in instance_meta.keys():
        assert len(tag_key) == 8, f"Tag key '{tag_key}' is not 8 characters"
        assert all(
            c in "0123456789ABCDEF" for c in tag_key
        ), f"Tag key '{tag_key}' has non-hex chars"

    # Verify each tag has "vr" field
    for tag_key, tag_value in instance_meta.items():
        assert "vr" in tag_value, f"Tag '{tag_key}' missing 'vr' field"
        assert isinstance(tag_value["vr"], str)
        assert len(tag_value["vr"]) == 2, f"Tag '{tag_key}' VR is not 2 characters"

    # Verify Value is array where present
    for tag_key, tag_value in instance_meta.items():
        if "Value" in tag_value:
            assert isinstance(tag_value["Value"], list), f"Tag '{tag_key}' Value is not a list"


def test_metadata_includes_patient_module(client: TestClient):
    """Metadata includes patient-level DICOM tags."""
    study_uid = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000000009"
    series_uid = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000000010"
    sop_uid = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000000011"

    dcm = DicomFactory.create_ct_image(
        patient_id="WADO-PATIENT-001",
        patient_name="Patient^Module^Test",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/metadata",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    metadata = response.json()
    assert len(metadata) == 1

    instance_meta = metadata[0]

    # Patient ID (00100020)
    assert "00100020" in instance_meta
    assert instance_meta["00100020"]["vr"] == "LO"
    assert instance_meta["00100020"]["Value"][0] == "WADO-PATIENT-001"

    # Patient Name (00100010)
    assert "00100010" in instance_meta
    assert instance_meta["00100010"]["vr"] == "PN"
    assert instance_meta["00100010"]["Value"][0]["Alphabetic"] == "Patient^Module^Test"


def test_retrieve_instance_metadata(client: TestClient):
    """GET .../instances/{instance}/metadata returns single instance metadata."""
    study_uid = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000000012"
    series_uid = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000000013"
    sop_uid = "1.2.826.0.1.3680043.8.498.10000000000000000000000000000000014"

    dcm = DicomFactory.create_ct_image(
        patient_id="WADO-INSTMETA-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/metadata",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    assert "application/dicom+json" in response.headers["content-type"]

    metadata = response.json()
    assert isinstance(metadata, list)
    assert len(metadata) == 1

    instance_meta = metadata[0]
    # Verify it's the right instance
    assert "00080018" in instance_meta  # SOPInstanceUID
    assert instance_meta["00080018"]["Value"][0] == sop_uid


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. Accept Header Handling (5 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_retrieve_with_accept_dicom(client: TestClient):
    """Accept: application/dicom returns multipart with DICOM content."""
    study_uid = "1.2.826.0.1.3680043.8.498.20000000000000000000000000000000001"
    series_uid = "1.2.826.0.1.3680043.8.498.20000000000000000000000000000000002"
    sop_uid = "1.2.826.0.1.3680043.8.498.20000000000000000000000000000000003"

    dcm = DicomFactory.create_ct_image(
        patient_id="WADO-ACCEPT-DCM-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}",
        headers={"Accept": "application/dicom"},
    )

    assert response.status_code == 200
    # Should return multipart/related containing DICOM
    assert "multipart/related" in response.headers["content-type"]
    assert response.content.count(b"DICM") == 1


def test_retrieve_with_accept_multipart(client: TestClient):
    """Accept: multipart/related returns instances."""
    study_uid = "1.2.826.0.1.3680043.8.498.20000000000000000000000000000000004"
    series_uid = "1.2.826.0.1.3680043.8.498.20000000000000000000000000000000005"
    sop_uid = "1.2.826.0.1.3680043.8.498.20000000000000000000000000000000006"

    dcm = DicomFactory.create_ct_image(
        patient_id="WADO-ACCEPT-MULTI-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )

    assert response.status_code == 200
    assert "multipart/related" in response.headers["content-type"]
    assert response.content.count(b"DICM") == 1


def test_retrieve_metadata_requires_json(client: TestClient):
    """Accept: application/dicom+json on instance returns metadata as JSON."""
    study_uid = "1.2.826.0.1.3680043.8.498.20000000000000000000000000000000007"
    series_uid = "1.2.826.0.1.3680043.8.498.20000000000000000000000000000000008"
    sop_uid = "1.2.826.0.1.3680043.8.498.20000000000000000000000000000000009"

    dcm = DicomFactory.create_ct_image(
        patient_id="WADO-ACCEPT-JSON-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    assert "application/dicom+json" in response.headers["content-type"]

    # Should be JSON metadata, not multipart
    metadata = response.json()
    assert isinstance(metadata, list)
    assert len(metadata) == 1
    assert "0020000D" in metadata[0]  # StudyInstanceUID


def test_retrieve_unsupported_accept_returns_406(client: TestClient):
    """Unsupported Accept header (e.g., text/plain) returns 406 Not Acceptable.

    Per DICOMweb specification, WADO-RS endpoints must validate Accept headers
    and return 406 for unsupported media types.
    """
    study_uid = "1.2.826.0.1.3680043.8.498.20000000000000000000000000000000010"
    series_uid = "1.2.826.0.1.3680043.8.498.20000000000000000000000000000000011"
    sop_uid = "1.2.826.0.1.3680043.8.498.20000000000000000000000000000000012"

    dcm = DicomFactory.create_ct_image(
        patient_id="WADO-ACCEPT-UNSUP-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}",
        headers={"Accept": "text/plain"},
    )

    # Should return 406 Not Acceptable per DICOMweb spec
    assert response.status_code == 406


def test_retrieve_no_accept_defaults_to_multipart(client: TestClient):
    """No Accept header defaults to multipart/related response."""
    study_uid = "1.2.826.0.1.3680043.8.498.20000000000000000000000000000000013"
    series_uid = "1.2.826.0.1.3680043.8.498.20000000000000000000000000000000014"
    sop_uid = "1.2.826.0.1.3680043.8.498.20000000000000000000000000000000015"

    dcm = DicomFactory.create_ct_image(
        patient_id="WADO-ACCEPT-NONE-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=False,
    )
    store_dicom(client, dcm)

    # Send request without Accept header
    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}",
    )

    assert response.status_code == 200
    assert "multipart/related" in response.headers["content-type"]
    assert response.content.count(b"DICM") == 1
