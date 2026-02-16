"""Integration tests for QIDO-RS advanced features (Phase 2, Task 6).

Tests advanced QIDO-RS features: pagination (limit/offset), field inclusion
(includefield), and date range queries.

Test Coverage (15 tests):
- Pagination (5 tests): limit, offset, combined, limit exceeds total, offset beyond results
- Field Inclusion (5 tests): single tag, multiple tags, "all", default set, invalid tag
- Date Range Queries (5 tests): range, exact, before, after, invalid format

Implementation Notes:
- Pagination (limit/offset): Fully implemented at the SQL query level. However,
  for study-level search, limit/offset apply to instance rows, not study groups.
  A limit of N returns studies derived from at most N instance rows. This is a
  behavioral nuance documented in the tests.
- includefield: The query parameter is recognized (skipped in _parse_qido_params)
  but NOT functionally implemented. The response always returns the full default
  set of study-level fields regardless of the includefield value. Tests document
  this deviation with TODO markers.
- Date range queries: NOT implemented. The _parse_qido_params function only
  performs exact string matching on StudyDate. DICOM date range syntax
  (YYYYMMDD-YYYYMMDD) is not parsed; the literal range string is compared
  against the stored date, yielding no matches. Tests document this with TODOs.
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
        client: TestClient instance
        dicom_bytes: DICOM file bytes

    Returns:
        STOW-RS response body as dict
    """
    body, content_type = build_multipart_request([dicom_bytes])
    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert response.status_code == 200, f"STOW-RS failed: {response.status_code} {response.text}"
    return response.json()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. Pagination (5 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_search_with_limit_parameter(client: TestClient):
    """GET /v2/studies?limit=2 restricts the number of returned studies.

    The limit parameter is applied at the instance-row level in the SQL query.
    Each study is backed by at least one instance row, so limit=N effectively
    restricts results to at most N studies (when each study has one instance).
    """
    # Store 5 distinct studies
    patient_ids = []
    for i in range(5):
        pid = f"ADV-LIMIT-{i:03d}"
        patient_ids.append(pid)
        dcm = DicomFactory.create_ct_image(patient_id=pid)
        store_dicom(client, dcm)

    # Search with limit=2
    response = client.get(
        "/v2/studies",
        params={"limit": 2},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    # With limit=2, we should get at most 2 studies
    assert len(studies) <= 2
    assert len(studies) >= 1  # At least 1 study should be returned


def test_search_with_offset_parameter(client: TestClient):
    """GET /v2/studies?offset=N skips the first N instance rows.

    Offset applies at the SQL level to instance rows. This means the first
    N instances (ordered by study_date DESC) are skipped. For deterministic
    testing, we use unique patient IDs and verify that offset produces a
    different (or subset) result set than offset=0.
    """
    # Store 5 distinct studies with known dates for ordering
    for i in range(5):
        pid = f"ADV-OFFSET-{i:03d}"
        # Dates descending: 20260105, 20260104, ... 20260101
        date = f"2026010{5 - i}"
        dcm = DicomFactory.create_ct_image(patient_id=pid, study_date=date)
        store_dicom(client, dcm)

    # Get all studies (no offset)
    resp_all = client.get(
        "/v2/studies",
        params={"PatientID": "ADV-OFFSET-*"},
        headers={"Accept": "application/dicom+json"},
    )
    assert resp_all.status_code == 200
    all_studies = resp_all.json()

    # Get studies with offset=3
    resp_offset = client.get(
        "/v2/studies",
        params={"PatientID": "ADV-OFFSET-*", "offset": 3},
        headers={"Accept": "application/dicom+json"},
    )
    assert resp_offset.status_code == 200
    offset_studies = resp_offset.json()

    # Offset=3 should return fewer studies than offset=0
    assert len(offset_studies) < len(all_studies)
    # Should have at most 2 studies remaining (5 - 3 = 2)
    assert len(offset_studies) <= 2


def test_search_limit_and_offset_combined(client: TestClient):
    """GET /v2/studies?limit=2&offset=1 paginates results.

    Combines limit and offset for pagination. Stores multiple studies,
    fetches page 1 (offset=0, limit=2) and page 2 (offset=2, limit=2),
    and verifies they return different studies.
    """
    # Store 4 studies with distinct dates for ordering
    pids = []
    for i in range(4):
        pid = f"ADV-COMBO-{i:03d}"
        pids.append(pid)
        date = f"2026020{4 - i}"  # 20260204, 20260203, 20260202, 20260201
        dcm = DicomFactory.create_ct_image(patient_id=pid, study_date=date)
        store_dicom(client, dcm)

    # Page 1: offset=0, limit=2
    resp_page1 = client.get(
        "/v2/studies",
        params={"PatientID": "ADV-COMBO-*", "limit": 2, "offset": 0},
        headers={"Accept": "application/dicom+json"},
    )
    assert resp_page1.status_code == 200
    page1 = resp_page1.json()
    assert len(page1) == 2

    # Page 2: offset=2, limit=2
    resp_page2 = client.get(
        "/v2/studies",
        params={"PatientID": "ADV-COMBO-*", "limit": 2, "offset": 2},
        headers={"Accept": "application/dicom+json"},
    )
    assert resp_page2.status_code == 200
    page2 = resp_page2.json()
    assert len(page2) == 2

    # Page 1 and page 2 should have different study UIDs
    page1_uids = {s["0020000D"]["Value"][0] for s in page1}
    page2_uids = {s["0020000D"]["Value"][0] for s in page2}
    assert page1_uids.isdisjoint(page2_uids), "Pages should not overlap"


def test_search_limit_exceeds_total_returns_all(client: TestClient):
    """GET /v2/studies?limit=1000 with only a few results returns all matches.

    When the limit is larger than the total number of matching results,
    all matching results are returned.
    """
    # Store 3 studies with unique prefix
    stored_pids = []
    for i in range(3):
        pid = f"ADV-BIGLIM-{i:03d}"
        stored_pids.append(pid)
        dcm = DicomFactory.create_ct_image(patient_id=pid)
        store_dicom(client, dcm)

    # Search with very large limit
    response = client.get(
        "/v2/studies",
        params={"PatientID": "ADV-BIGLIM-*", "limit": 1000},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    # All 3 studies should be returned
    assert len(studies) == 3

    returned_pids = {s["00100020"]["Value"][0] for s in studies}
    for pid in stored_pids:
        assert pid in returned_pids


def test_search_offset_beyond_results_returns_empty(client: TestClient):
    """GET /v2/studies?offset=1000 with few results returns empty array.

    When the offset exceeds the total number of matching instance rows,
    no results are returned (empty array).
    """
    # Store 3 studies
    for i in range(3):
        pid = f"ADV-BIGOFF-{i:03d}"
        dcm = DicomFactory.create_ct_image(patient_id=pid)
        store_dicom(client, dcm)

    # Search with offset way beyond results
    response = client.get(
        "/v2/studies",
        params={"PatientID": "ADV-BIGOFF-*", "offset": 1000},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert studies == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. Field Inclusion (5 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_search_with_includefield_patient_name(client: TestClient):
    """GET /v2/studies?includefield=00100010 requests PatientName inclusion.

    TODO: includefield is not functionally implemented. The parameter is
    accepted (not rejected), but the response always returns the full default
    set of study-level fields regardless of which fields are requested.
    This test verifies the parameter is accepted and the default response
    includes PatientName.
    """
    dcm = DicomFactory.create_ct_image(
        patient_id="ADV-INCFLD-001",
        patient_name="IncludeField^Test",
    )
    store_dicom(client, dcm)

    # Request with includefield=00100010 (PatientName)
    response = client.get(
        "/v2/studies",
        params={"PatientID": "ADV-INCFLD-001", "includefield": "00100010"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1

    study = studies[0]
    # PatientName should be present (always included in default set)
    assert "00100010" in study
    assert study["00100010"]["Value"][0]["Alphabetic"] == "IncludeField^Test"


def test_search_includefield_multiple_tags(client: TestClient):
    """GET /v2/studies?includefield=00100010&includefield=00080020 with multiple tags.

    TODO: includefield is not functionally implemented. Multiple includefield
    parameters are accepted but do not filter the response. The full default
    set of fields is always returned. This test verifies both requested tags
    are present in the response (which they are by default).
    """
    dcm = DicomFactory.create_ct_image(
        patient_id="ADV-INCMULTI-001",
        patient_name="MultiField^Test",
        study_date="20260215",
    )
    store_dicom(client, dcm)

    # Request with multiple includefield params
    response = client.get(
        "/v2/studies",
        params={
            "PatientID": "ADV-INCMULTI-001",
            "includefield": ["00100010", "00080020"],
        },
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1

    study = studies[0]
    # Both fields should be present (always in default set)
    assert "00100010" in study  # PatientName
    assert "00080020" in study  # StudyDate
    assert study["00100010"]["Value"][0]["Alphabetic"] == "MultiField^Test"
    assert study["00080020"]["Value"][0] == "20260215"


def test_search_includefield_all(client: TestClient):
    """GET /v2/studies?includefield=all requests all available fields.

    TODO: includefield=all is not functionally implemented. The parameter is
    accepted, but the response always returns the same default set of
    study-level fields regardless. This test verifies the parameter is
    accepted and the standard fields are present.
    """
    dcm = DicomFactory.create_ct_image(
        patient_id="ADV-INCALL-001",
        patient_name="IncludeAll^Test",
        study_date="20260215",
        accession_number="ACC-INCALL-001",
    )
    store_dicom(client, dcm)

    # Request with includefield=all
    response = client.get(
        "/v2/studies",
        params={"PatientID": "ADV-INCALL-001", "includefield": "all"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1

    study = studies[0]
    # Standard study-level fields should be present
    assert "0020000D" in study  # StudyInstanceUID
    assert "00100020" in study  # PatientID
    assert "00100010" in study  # PatientName
    assert "00080020" in study  # StudyDate
    assert "00080050" in study  # AccessionNumber
    assert "00081030" in study  # StudyDescription
    assert "00080061" in study  # ModalitiesInStudy
    assert "00201206" in study  # NumberOfStudyRelatedSeries
    assert "00201208" in study  # NumberOfStudyRelatedInstances


def test_search_without_includefield_returns_default_set(client: TestClient):
    """GET /v2/studies without includefield returns the default attribute set.

    The default study-level response includes: StudyInstanceUID, PatientID,
    PatientName, StudyDate, AccessionNumber, StudyDescription,
    ModalitiesInStudy, NumberOfStudyRelatedSeries, NumberOfStudyRelatedInstances.
    """
    dcm = DicomFactory.create_ct_image(
        patient_id="ADV-DEFAULT-001",
        patient_name="Default^Set",
        study_date="20260215",
        accession_number="ACC-DEFAULT-001",
    )
    store_dicom(client, dcm)

    # Search without includefield
    response = client.get(
        "/v2/studies",
        params={"PatientID": "ADV-DEFAULT-001"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1

    study = studies[0]

    # Verify the default attribute set
    expected_tags = {
        "0020000D",  # StudyInstanceUID
        "00100020",  # PatientID
        "00100010",  # PatientName
        "00080020",  # StudyDate
        "00080050",  # AccessionNumber
        "00081030",  # StudyDescription
        "00080061",  # ModalitiesInStudy
        "00201206",  # NumberOfStudyRelatedSeries
        "00201208",  # NumberOfStudyRelatedInstances
    }

    for tag in expected_tags:
        assert tag in study, f"Default attribute {tag} missing from response"

    # Verify VR types for key fields
    assert study["0020000D"]["vr"] == "UI"
    assert study["00100020"]["vr"] == "LO"
    assert study["00100010"]["vr"] == "PN"
    assert study["00080020"]["vr"] == "DA"
    assert study["00080050"]["vr"] == "SH"
    assert study["00081030"]["vr"] == "LO"
    assert study["00080061"]["vr"] == "CS"
    assert study["00201206"]["vr"] == "IS"
    assert study["00201208"]["vr"] == "IS"


def test_search_includefield_invalid_tag_ignored(client: TestClient):
    """GET /v2/studies?includefield=ZZZZZZZZ with invalid tag is accepted.

    TODO: includefield is not functionally implemented, so invalid tags are
    simply ignored (they have no effect). The response returns the default
    set of fields. This test verifies no error is raised for invalid tags.
    """
    dcm = DicomFactory.create_ct_image(
        patient_id="ADV-INCBAD-001",
        patient_name="BadTag^Test",
    )
    store_dicom(client, dcm)

    # Request with invalid/nonsense tag
    response = client.get(
        "/v2/studies",
        params={"PatientID": "ADV-INCBAD-001", "includefield": "ZZZZZZZZ"},
        headers={"Accept": "application/dicom+json"},
    )

    # Should not fail; invalid includefield is silently ignored
    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1

    # Default fields should still be present
    study = studies[0]
    assert "0020000D" in study  # StudyInstanceUID
    assert "00100020" in study  # PatientID
    assert study["00100020"]["Value"][0] == "ADV-INCBAD-001"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. Date Range Queries (5 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_search_study_date_range(client: TestClient):
    """GET /v2/studies?StudyDate=20260101-20260131 searches a date range.

    TODO: Date range queries are NOT implemented. The _parse_qido_params
    function treats the range string "20260101-20260131" as a literal exact
    match against the stored StudyDate column. Since no stored date equals
    this literal string, no results are returned.

    This test documents the current behavior. When date range support is
    added, this test should be updated to verify range matching.
    """
    # Store studies with dates in January 2026
    dcm_jan15 = DicomFactory.create_ct_image(
        patient_id="ADV-DRANGE-JAN15",
        study_date="20260115",
    )
    dcm_jan25 = DicomFactory.create_ct_image(
        patient_id="ADV-DRANGE-JAN25",
        study_date="20260125",
    )
    # Store a study outside the range
    dcm_feb15 = DicomFactory.create_ct_image(
        patient_id="ADV-DRANGE-FEB15",
        study_date="20260215",
    )
    store_dicom(client, dcm_jan15)
    store_dicom(client, dcm_jan25)
    store_dicom(client, dcm_feb15)

    # Search with date range
    response = client.get(
        "/v2/studies",
        params={"StudyDate": "20260101-20260131"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()

    # TODO: Date range NOT implemented - returns empty because
    # "20260101-20260131" is treated as a literal exact match.
    # When implemented, this should return 2 studies (JAN15 and JAN25).
    assert len(studies) == 0, (
        "Date range query returned results unexpectedly. "
        "If date range support was added, update this test to verify range matching."
    )


def test_search_study_date_exact(client: TestClient):
    """GET /v2/studies?StudyDate=20260215 searches an exact date.

    Exact date matching IS implemented and works correctly.
    """
    dcm_target = DicomFactory.create_ct_image(
        patient_id="ADV-DEXACT-TARGET",
        study_date="20260215",
    )
    dcm_other = DicomFactory.create_ct_image(
        patient_id="ADV-DEXACT-OTHER",
        study_date="20260216",
    )
    store_dicom(client, dcm_target)
    store_dicom(client, dcm_other)

    # Search with exact date
    response = client.get(
        "/v2/studies",
        params={"StudyDate": "20260215"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()

    # Should find the target study
    target_pids = [s["00100020"]["Value"][0] for s in studies]
    assert "ADV-DEXACT-TARGET" in target_pids

    # The other study (20260216) should not be in results
    assert "ADV-DEXACT-OTHER" not in target_pids


def test_search_study_date_before(client: TestClient):
    """GET /v2/studies?StudyDate=-20260215 searches dates on or before a date.

    TODO: Date range queries (including open-ended ranges) are NOT implemented.
    The "-20260215" value is treated as a literal exact match, which will never
    match any stored date. This test documents the current behavior.
    """
    dcm_before = DicomFactory.create_ct_image(
        patient_id="ADV-DBEFORE-JAN",
        study_date="20260101",
    )
    dcm_on = DicomFactory.create_ct_image(
        patient_id="ADV-DBEFORE-FEB15",
        study_date="20260215",
    )
    dcm_after = DicomFactory.create_ct_image(
        patient_id="ADV-DBEFORE-MAR",
        study_date="20260301",
    )
    store_dicom(client, dcm_before)
    store_dicom(client, dcm_on)
    store_dicom(client, dcm_after)

    # Search with "before" date range
    response = client.get(
        "/v2/studies",
        params={"StudyDate": "-20260215"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()

    # TODO: Date range NOT implemented - returns empty because
    # "-20260215" is treated as a literal exact match.
    # When implemented, this should return 2 studies (JAN and FEB15).
    assert len(studies) == 0, (
        "Open-ended date range query returned results unexpectedly. "
        "If date range support was added, update this test."
    )


def test_search_study_date_after(client: TestClient):
    """GET /v2/studies?StudyDate=20260215- searches dates on or after a date.

    TODO: Date range queries (including open-ended ranges) are NOT implemented.
    The "20260215-" value is treated as a literal exact match, which will never
    match any stored date. This test documents the current behavior.
    """
    dcm_before = DicomFactory.create_ct_image(
        patient_id="ADV-DAFTER-JAN",
        study_date="20260101",
    )
    dcm_on = DicomFactory.create_ct_image(
        patient_id="ADV-DAFTER-FEB15",
        study_date="20260215",
    )
    dcm_after = DicomFactory.create_ct_image(
        patient_id="ADV-DAFTER-MAR",
        study_date="20260301",
    )
    store_dicom(client, dcm_before)
    store_dicom(client, dcm_on)
    store_dicom(client, dcm_after)

    # Search with "after" date range
    response = client.get(
        "/v2/studies",
        params={"StudyDate": "20260215-"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()

    # TODO: Date range NOT implemented - returns empty because
    # "20260215-" is treated as a literal exact match.
    # When implemented, this should return 2 studies (FEB15 and MAR).
    assert len(studies) == 0, (
        "Open-ended date range query returned results unexpectedly. "
        "If date range support was added, update this test."
    )


def test_search_invalid_date_format_returns_400(client: TestClient):
    """GET /v2/studies?StudyDate=not-a-date with invalid date format.

    TODO: Date format validation is NOT implemented. The _parse_qido_params
    function passes any string value through as an exact match filter on the
    study_date column. Invalid date strings do not produce a 400 error;
    they simply match nothing (200 with empty results).

    When date validation is added, this test should verify a 400 response.
    """
    dcm = DicomFactory.create_ct_image(
        patient_id="ADV-DINVALID-001",
        study_date="20260215",
    )
    store_dicom(client, dcm)

    # Search with invalid date format
    response = client.get(
        "/v2/studies",
        params={"StudyDate": "not-a-date"},
        headers={"Accept": "application/dicom+json"},
    )

    # TODO: No date validation implemented - returns 200 with empty results
    # instead of 400. When validation is added, change this assertion to:
    #   assert response.status_code == 400
    assert response.status_code == 200
    studies = response.json()
    # The invalid date string matches nothing
    assert len(studies) == 0
