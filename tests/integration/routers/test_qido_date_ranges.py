"""Integration tests for QIDO-RS date range queries (Task 7).

Tests DICOM date range query parsing for QIDO-RS StudyDate parameter.
Validates all four date range formats per DICOM standard:
- Exact match: "20260115"
- Range: "20260110-20260120"
- Before or equal: "-20260120"
- After or equal: "20260110-"

Test Coverage (8 tests):
- Date range parsing (4 tests): exact, range, before, after
- Edge cases (4 tests): boundaries, invalid formats, empty ranges, partial dates

Implementation Notes:
- StudyDate is stored as String(8) in YYYYMMDD format
- Date comparisons use lexicographic ordering (which works for YYYYMMDD)
- Invalid date formats should not raise errors (DICOM is lenient)
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
#  1. Date Range Parsing (4 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_search_study_date_exact_match(client: TestClient):
    """GET /v2/studies?StudyDate=20260115 returns exact date match."""
    # Store studies with different dates
    dcm_jan15 = DicomFactory.create_ct_image(
        patient_id="DATE-EXACT-JAN15",
        study_date="20260115",
    )
    dcm_jan16 = DicomFactory.create_ct_image(
        patient_id="DATE-EXACT-JAN16",
        study_date="20260116",
    )
    store_dicom(client, dcm_jan15)
    store_dicom(client, dcm_jan16)

    # Search for exact date
    response = client.get(
        "/v2/studies",
        params={"StudyDate": "20260115"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1

    # Verify correct study returned
    assert studies[0]["00100020"]["Value"][0] == "DATE-EXACT-JAN15"
    assert studies[0]["00080020"]["Value"][0] == "20260115"


def test_search_study_date_range(client: TestClient):
    """GET /v2/studies?StudyDate=20260110-20260120 returns dates in range."""
    # Store studies with dates inside and outside range
    dcm_jan05 = DicomFactory.create_ct_image(
        patient_id="DATE-RANGE-JAN05",
        study_date="20260105",  # Before range
    )
    dcm_jan10 = DicomFactory.create_ct_image(
        patient_id="DATE-RANGE-JAN10",
        study_date="20260110",  # Start boundary
    )
    dcm_jan15 = DicomFactory.create_ct_image(
        patient_id="DATE-RANGE-JAN15",
        study_date="20260115",  # Inside range
    )
    dcm_jan20 = DicomFactory.create_ct_image(
        patient_id="DATE-RANGE-JAN20",
        study_date="20260120",  # End boundary
    )
    dcm_jan25 = DicomFactory.create_ct_image(
        patient_id="DATE-RANGE-JAN25",
        study_date="20260125",  # After range
    )

    store_dicom(client, dcm_jan05)
    store_dicom(client, dcm_jan10)
    store_dicom(client, dcm_jan15)
    store_dicom(client, dcm_jan20)
    store_dicom(client, dcm_jan25)

    # Search with date range
    response = client.get(
        "/v2/studies",
        params={"StudyDate": "20260110-20260120"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()

    # Should return 3 studies (JAN10, JAN15, JAN20)
    assert len(studies) == 3

    patient_ids = {s["00100020"]["Value"][0] for s in studies}
    assert patient_ids == {"DATE-RANGE-JAN10", "DATE-RANGE-JAN15", "DATE-RANGE-JAN20"}


def test_search_study_date_before(client: TestClient):
    """GET /v2/studies?StudyDate=-20260215 returns dates on or before."""
    # Store studies with dates before, on, and after the boundary
    dcm_jan15 = DicomFactory.create_ct_image(
        patient_id="DATE-BEFORE-JAN15",
        study_date="20260115",  # Before
    )
    dcm_feb15 = DicomFactory.create_ct_image(
        patient_id="DATE-BEFORE-FEB15",
        study_date="20260215",  # On boundary
    )
    dcm_mar15 = DicomFactory.create_ct_image(
        patient_id="DATE-BEFORE-MAR15",
        study_date="20260315",  # After
    )

    store_dicom(client, dcm_jan15)
    store_dicom(client, dcm_feb15)
    store_dicom(client, dcm_mar15)

    # Search with "before or equal" date range
    response = client.get(
        "/v2/studies",
        params={"StudyDate": "-20260215"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()

    # Should return 2 studies (JAN15, FEB15)
    assert len(studies) == 2

    patient_ids = {s["00100020"]["Value"][0] for s in studies}
    assert patient_ids == {"DATE-BEFORE-JAN15", "DATE-BEFORE-FEB15"}


def test_search_study_date_after(client: TestClient):
    """GET /v2/studies?StudyDate=20260215- returns dates on or after."""
    # Store studies with dates before, on, and after the boundary
    dcm_jan15 = DicomFactory.create_ct_image(
        patient_id="DATE-AFTER-JAN15",
        study_date="20260115",  # Before
    )
    dcm_feb15 = DicomFactory.create_ct_image(
        patient_id="DATE-AFTER-FEB15",
        study_date="20260215",  # On boundary
    )
    dcm_mar15 = DicomFactory.create_ct_image(
        patient_id="DATE-AFTER-MAR15",
        study_date="20260315",  # After
    )

    store_dicom(client, dcm_jan15)
    store_dicom(client, dcm_feb15)
    store_dicom(client, dcm_mar15)

    # Search with "after or equal" date range
    response = client.get(
        "/v2/studies",
        params={"StudyDate": "20260215-"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()

    # Should return 2 studies (FEB15, MAR15)
    assert len(studies) == 2

    patient_ids = {s["00100020"]["Value"][0] for s in studies}
    assert patient_ids == {"DATE-AFTER-FEB15", "DATE-AFTER-MAR15"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. Edge Cases (4 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_search_study_date_boundary_inclusive(client: TestClient):
    """Date range boundaries are inclusive on both ends."""
    # Store study exactly at boundary dates
    dcm_start = DicomFactory.create_ct_image(
        patient_id="DATE-BOUND-START",
        study_date="20260101",
    )
    dcm_end = DicomFactory.create_ct_image(
        patient_id="DATE-BOUND-END",
        study_date="20260131",
    )

    store_dicom(client, dcm_start)
    store_dicom(client, dcm_end)

    # Search with exact boundary range
    response = client.get(
        "/v2/studies",
        params={"StudyDate": "20260101-20260131"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()

    # Both boundary dates should be included
    assert len(studies) == 2
    patient_ids = {s["00100020"]["Value"][0] for s in studies}
    assert patient_ids == {"DATE-BOUND-START", "DATE-BOUND-END"}


def test_search_study_date_empty_range(client: TestClient):
    """Date range with start > end returns no results."""
    # Store study
    dcm = DicomFactory.create_ct_image(
        patient_id="DATE-EMPTY-001",
        study_date="20260115",
    )
    store_dicom(client, dcm)

    # Search with inverted range (start > end)
    response = client.get(
        "/v2/studies",
        params={"StudyDate": "20260120-20260110"},  # Inverted
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()

    # Empty range returns no results
    assert len(studies) == 0


def test_search_study_date_partial_date(client: TestClient):
    """Partial dates (YYYYMM, YYYY) are not supported - treated as exact match."""
    # Store study with full date
    dcm = DicomFactory.create_ct_image(
        patient_id="DATE-PARTIAL-001",
        study_date="20260115",
    )
    store_dicom(client, dcm)

    # Search with partial date (YYYYMM format)
    response = client.get(
        "/v2/studies",
        params={"StudyDate": "202601"},  # Partial: year and month only
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()

    # Partial dates don't match (no wildcard expansion)
    # This is treated as exact match against "202601"
    assert len(studies) == 0


def test_search_study_date_null_values(client: TestClient):
    """Studies with null StudyDate are excluded from date searches."""
    # Store study without StudyDate (returns HTTP 202 with warning)
    dcm_no_date = DicomFactory.create_dicom_with_attrs(study_date=None)
    body, content_type = build_multipart_request([dcm_no_date])
    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    # HTTP 202 expected due to missing searchable attribute (StudyDate)
    assert response.status_code == 202

    dcm_with_date = DicomFactory.create_ct_image(
        patient_id="DATE-NULL-WITH",
        study_date="20260115",
    )
    store_dicom(client, dcm_with_date)

    # Search for any date (using open-ended range)
    response = client.get(
        "/v2/studies",
        params={"StudyDate": "20000101-"},  # Very broad range
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()

    # Only the study with a date should match
    assert len(studies) == 1
    assert studies[0]["00100020"]["Value"][0] == "DATE-NULL-WITH"
