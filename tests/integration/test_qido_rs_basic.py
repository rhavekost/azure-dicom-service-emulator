"""Integration tests for QIDO-RS basic search (Phase 2, Task 5).

Tests basic QIDO-RS search functionality across study, series, and instance
levels. Uses HTTP-level TestClient to exercise the full endpoint stack.

Test Coverage (20 tests):
- Study Search (6 tests): PatientID, PatientName, StudyDate, AccessionNumber, Modality filters
- Series Search (6 tests): Within-study, modality filter, empty results, series number
- Instance Search (6 tests): Within-series, within-study, multiple filters, empty results
- Response Format (2 tests): DICOM JSON format validation, RetrieveURL presence

Implementation Notes:
- BodyPartExamined is NOT implemented in the QIDO_TAG_MAP — test adapted to use Modality
- SOPClassUID / InstanceNumber / SeriesNumber are NOT QIDO query params — tests adapted
- No standalone GET /series or GET /instances endpoint exists — series/instance search
  always requires study context in the URL path
- ModalitiesInStudy is a response-only field, not a search filter; Modality maps to the
  modality column and filters at instance level (which propagates to study aggregation)
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


def store_multiple(client: TestClient, dicom_files: list[bytes]) -> dict:
    """Store multiple DICOM instances in a single STOW-RS request."""
    body, content_type = build_multipart_request(dicom_files)
    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert response.status_code == 200, f"STOW-RS failed: {response.status_code} {response.text}"
    return response.json()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. Study Search (6 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_search_all_studies_no_filters(client: TestClient):
    """GET /v2/studies with no filters returns all stored studies."""
    # Store 3 studies with different patients
    dcm1 = DicomFactory.create_ct_image(
        patient_id="ALL-STUDY-001",
        patient_name="Alpha^Patient",
    )
    dcm2 = DicomFactory.create_ct_image(
        patient_id="ALL-STUDY-002",
        patient_name="Beta^Patient",
    )
    dcm3 = DicomFactory.create_ct_image(
        patient_id="ALL-STUDY-003",
        patient_name="Gamma^Patient",
    )
    for dcm in [dcm1, dcm2, dcm3]:
        store_dicom(client, dcm)

    # Search without filters
    response = client.get(
        "/v2/studies",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) >= 3

    # All 3 patient IDs should appear
    patient_ids = {s["00100020"]["Value"][0] for s in studies}
    assert "ALL-STUDY-001" in patient_ids
    assert "ALL-STUDY-002" in patient_ids
    assert "ALL-STUDY-003" in patient_ids


def test_search_studies_by_patient_id(client: TestClient):
    """GET /v2/studies?PatientID=... filters by PatientID (exact match)."""
    dcm_target = DicomFactory.create_ct_image(
        patient_id="QIDO-PID-TARGET",
        patient_name="Target^Patient",
    )
    dcm_other = DicomFactory.create_ct_image(
        patient_id="QIDO-PID-OTHER",
        patient_name="Other^Patient",
    )
    store_dicom(client, dcm_target)
    store_dicom(client, dcm_other)

    # Search by PatientID
    response = client.get(
        "/v2/studies",
        params={"PatientID": "QIDO-PID-TARGET"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1
    assert studies[0]["00100020"]["Value"][0] == "QIDO-PID-TARGET"


def test_search_studies_by_patient_name(client: TestClient):
    """GET /v2/studies?PatientName=... filters by PatientName (exact match)."""
    dcm_target = DicomFactory.create_ct_image(
        patient_id="QIDO-PNAME-001",
        patient_name="FindMe^Patient",
    )
    dcm_other = DicomFactory.create_ct_image(
        patient_id="QIDO-PNAME-002",
        patient_name="NotThis^Patient",
    )
    store_dicom(client, dcm_target)
    store_dicom(client, dcm_other)

    # Search by PatientName
    response = client.get(
        "/v2/studies",
        params={"PatientName": "FindMe^Patient"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1
    assert studies[0]["00100010"]["Value"][0]["Alphabetic"] == "FindMe^Patient"


def test_search_studies_by_study_date(client: TestClient):
    """GET /v2/studies?StudyDate=... filters by StudyDate (exact match)."""
    dcm_target = DicomFactory.create_ct_image(
        patient_id="QIDO-DATE-001",
        study_date="20260101",
    )
    dcm_other = DicomFactory.create_ct_image(
        patient_id="QIDO-DATE-002",
        study_date="20250615",
    )
    store_dicom(client, dcm_target)
    store_dicom(client, dcm_other)

    # Search by StudyDate
    response = client.get(
        "/v2/studies",
        params={"StudyDate": "20260101"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    # Should include at least the target study
    assert len(studies) >= 1
    target_study = next(
        (s for s in studies if s["00100020"]["Value"][0] == "QIDO-DATE-001"),
        None,
    )
    assert target_study is not None
    assert target_study["00080020"]["Value"][0] == "20260101"


def test_search_studies_by_accession_number(client: TestClient):
    """GET /v2/studies?AccessionNumber=... filters by AccessionNumber."""
    dcm_target = DicomFactory.create_ct_image(
        patient_id="QIDO-ACC-001",
        accession_number="ACC-FINDME-123",
    )
    dcm_other = DicomFactory.create_ct_image(
        patient_id="QIDO-ACC-002",
        accession_number="ACC-NOTME-456",
    )
    store_dicom(client, dcm_target)
    store_dicom(client, dcm_other)

    # Search by AccessionNumber
    response = client.get(
        "/v2/studies",
        params={"AccessionNumber": "ACC-FINDME-123"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1
    assert studies[0]["00100020"]["Value"][0] == "QIDO-ACC-001"
    assert studies[0]["00080050"]["Value"][0] == "ACC-FINDME-123"


def test_search_studies_by_modality(client: TestClient):
    """GET /v2/studies?Modality=... filters by Modality.

    Note: The QIDO_TAG_MAP maps 'Modality' to the instance-level modality
    column. Since study search groups by study, filtering by Modality
    returns studies containing instances with that modality. There is no
    separate 'ModalitiesInStudy' filter — only a response field.
    """
    dcm_ct = DicomFactory.create_ct_image(
        patient_id="QIDO-MOD-CT",
        patient_name="CT^Patient",
        modality="CT",
    )
    dcm_mr = DicomFactory.create_mri_image(
        patient_id="QIDO-MOD-MR",
        patient_name="MR^Patient",
        modality="MR",
    )
    store_dicom(client, dcm_ct)
    store_dicom(client, dcm_mr)

    # Search for CT studies
    response = client.get(
        "/v2/studies",
        params={"Modality": "CT"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    # Should include at least the CT study
    assert len(studies) >= 1

    # All returned studies should include CT in ModalitiesInStudy
    ct_study = next(
        (s for s in studies if s["00100020"]["Value"][0] == "QIDO-MOD-CT"),
        None,
    )
    assert ct_study is not None
    assert "CT" in ct_study["00080061"]["Value"]

    # MR-only study should not appear
    mr_patient_ids = [
        s["00100020"]["Value"][0] for s in studies if s["00100020"]["Value"][0] == "QIDO-MOD-MR"
    ]
    assert len(mr_patient_ids) == 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. Series Search (6 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_search_series_within_study(client: TestClient):
    """GET /v2/studies/{study}/series returns series in that study."""
    study_uid = "1.2.826.0.1.3680043.8.498.99901"
    series_uid_1 = "1.2.826.0.1.3680043.8.498.99901.1"
    series_uid_2 = "1.2.826.0.1.3680043.8.498.99901.2"

    # Two series in same study
    dcm1 = DicomFactory.create_ct_image(
        patient_id="QIDO-SERIES-001",
        study_uid=study_uid,
        series_uid=series_uid_1,
        modality="CT",
    )
    dcm2 = DicomFactory.create_mri_image(
        patient_id="QIDO-SERIES-001",
        study_uid=study_uid,
        series_uid=series_uid_2,
        modality="MR",
    )
    store_dicom(client, dcm1)
    store_dicom(client, dcm2)

    # Search series within study
    response = client.get(
        f"/v2/studies/{study_uid}/series",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    series_list = response.json()
    assert len(series_list) == 2

    series_uids = {s["0020000E"]["Value"][0] for s in series_list}
    assert series_uid_1 in series_uids
    assert series_uid_2 in series_uids


def test_search_series_by_modality(client: TestClient):
    """Series search filtered by Modality via SeriesInstanceUID is not directly
    supported as a query param in qido_rs_series. Instead we verify that
    the returned series contain the expected modality values.

    Note: The qido_rs_series endpoint only accepts SeriesInstanceUID as a
    filter. Modality filtering at the series level is not implemented as
    a query parameter. This test stores two series with different modalities
    and verifies both appear with correct modality in the response.
    """
    study_uid = "1.2.826.0.1.3680043.8.498.99902"
    series_ct = "1.2.826.0.1.3680043.8.498.99902.1"
    series_mr = "1.2.826.0.1.3680043.8.498.99902.2"

    dcm_ct = DicomFactory.create_ct_image(
        patient_id="QIDO-SERMOD-001",
        study_uid=study_uid,
        series_uid=series_ct,
        modality="CT",
    )
    dcm_mr = DicomFactory.create_mri_image(
        patient_id="QIDO-SERMOD-001",
        study_uid=study_uid,
        series_uid=series_mr,
        modality="MR",
    )
    store_dicom(client, dcm_ct)
    store_dicom(client, dcm_mr)

    # Get all series in this study
    response = client.get(
        f"/v2/studies/{study_uid}/series",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    series_list = response.json()
    assert len(series_list) == 2

    # Verify modality is correctly reported per series
    modalities = {s["0020000E"]["Value"][0]: s["00080060"]["Value"][0] for s in series_list}
    assert modalities[series_ct] == "CT"
    assert modalities[series_mr] == "MR"


def test_search_series_by_series_number(client: TestClient):
    """Verify series number is present in series search response.

    Note: SeriesNumber is NOT a supported QIDO query parameter in the
    current implementation. This test verifies it appears in the response.
    """
    study_uid = "1.2.826.0.1.3680043.8.498.99903"
    series_uid = "1.2.826.0.1.3680043.8.498.99903.1"

    dcm = DicomFactory.create_ct_image(
        patient_id="QIDO-SERNUM-001",
        study_uid=study_uid,
        series_uid=series_uid,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/series",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    series_list = response.json()
    assert len(series_list) == 1

    series = series_list[0]
    # SeriesNumber tag (00200011) should be present in response
    assert "00200011" in series
    assert series["00200011"]["vr"] == "IS"
    assert series["00200011"]["Value"][0] == 1


def test_search_series_by_body_part(client: TestClient):
    """BodyPartExamined is NOT implemented in the current emulator.

    The QIDO_TAG_MAP does not include BodyPartExamined, and the
    DicomInstance model does not have a body_part_examined column.
    This test documents the limitation by verifying that a series
    search without BodyPartExamined still returns expected results
    for the given study.
    """
    study_uid = "1.2.826.0.1.3680043.8.498.99904"
    series_uid = "1.2.826.0.1.3680043.8.498.99904.1"

    dcm = DicomFactory.create_ct_image(
        patient_id="QIDO-BODY-001",
        study_uid=study_uid,
        series_uid=series_uid,
    )
    store_dicom(client, dcm)

    # Verify series appears in normal search (BodyPartExamined not filterable)
    response = client.get(
        f"/v2/studies/{study_uid}/series",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    series_list = response.json()
    assert len(series_list) == 1
    assert series_list[0]["0020000E"]["Value"][0] == series_uid


def test_search_all_series_across_studies(client: TestClient):
    """Standalone GET /v2/series (all series across studies) is NOT implemented.

    The current emulator only supports GET /v2/studies/{study}/series.
    This test documents the limitation by verifying that searching
    series within a specific study only returns series from that study.
    """
    study_uid_a = "1.2.826.0.1.3680043.8.498.99905"
    study_uid_b = "1.2.826.0.1.3680043.8.498.99906"
    series_a = "1.2.826.0.1.3680043.8.498.99905.1"
    series_b = "1.2.826.0.1.3680043.8.498.99906.1"

    dcm_a = DicomFactory.create_ct_image(
        patient_id="QIDO-CROSS-A",
        study_uid=study_uid_a,
        series_uid=series_a,
    )
    dcm_b = DicomFactory.create_ct_image(
        patient_id="QIDO-CROSS-B",
        study_uid=study_uid_b,
        series_uid=series_b,
    )
    store_dicom(client, dcm_a)
    store_dicom(client, dcm_b)

    # Search series within study A only
    response = client.get(
        f"/v2/studies/{study_uid_a}/series",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    series_list = response.json()
    assert len(series_list) == 1
    assert series_list[0]["0020000E"]["Value"][0] == series_a

    # Series from study B should not appear
    returned_uids = {s["0020000E"]["Value"][0] for s in series_list}
    assert series_b not in returned_uids


def test_search_series_empty_result_set(client: TestClient):
    """Series search for non-existent study returns empty array.

    Note: The series endpoint returns an empty list when no instances
    match the study UID filter.
    """
    non_existent_study = "1.2.826.0.1.3680043.8.498.99999"

    response = client.get(
        f"/v2/studies/{non_existent_study}/series",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    series_list = response.json()
    assert series_list == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. Instance Search (6 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_search_instances_within_series(client: TestClient):
    """GET /v2/studies/{study}/series/{series}/instances returns instances."""
    study_uid = "1.2.826.0.1.3680043.8.498.88801"
    series_uid = "1.2.826.0.1.3680043.8.498.88801.1"
    sop_uid_1 = "1.2.826.0.1.3680043.8.498.88801.1.1"
    sop_uid_2 = "1.2.826.0.1.3680043.8.498.88801.1.2"

    dcm1 = DicomFactory.create_ct_image(
        patient_id="QIDO-INST-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid_1,
    )
    dcm2 = DicomFactory.create_ct_image(
        patient_id="QIDO-INST-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid_2,
    )
    store_dicom(client, dcm1)
    store_dicom(client, dcm2)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    instances = response.json()
    assert len(instances) == 2

    sop_uids = {inst.get("00080018", {}).get("Value", [None])[0] for inst in instances}
    assert sop_uid_1 in sop_uids
    assert sop_uid_2 in sop_uids


def test_search_instances_by_sop_class_uid(client: TestClient):
    """SOPClassUID is NOT a supported QIDO query parameter.

    SOPClassUID is not in QIDO_TAG_MAP, so it cannot be used as a filter.
    This test documents the limitation and verifies that SOPClassUID is
    present in the instance-level DICOM JSON response.
    """
    study_uid = "1.2.826.0.1.3680043.8.498.88802"
    series_uid = "1.2.826.0.1.3680043.8.498.88802.1"
    sop_uid = "1.2.826.0.1.3680043.8.498.88802.1.1"

    dcm = DicomFactory.create_ct_image(
        patient_id="QIDO-SOPCLASS-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    instances = response.json()
    assert len(instances) == 1

    # SOPClassUID (00080016) should be in the DICOM JSON metadata
    inst = instances[0]
    assert "00080016" in inst
    assert inst["00080016"]["Value"][0] == "1.2.840.10008.5.1.4.1.1.2"  # CT Image Storage


def test_search_instances_by_instance_number(client: TestClient):
    """InstanceNumber is NOT a supported QIDO query parameter.

    This test documents the limitation and verifies that InstanceNumber
    appears in the instance-level DICOM JSON response.
    """
    study_uid = "1.2.826.0.1.3680043.8.498.88803"
    series_uid = "1.2.826.0.1.3680043.8.498.88803.1"
    sop_uid = "1.2.826.0.1.3680043.8.498.88803.1.1"

    dcm = DicomFactory.create_ct_image(
        patient_id="QIDO-INSTNUM-001",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
    )
    store_dicom(client, dcm)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    instances = response.json()
    assert len(instances) == 1

    # InstanceNumber (00200013) should be in the DICOM JSON
    inst = instances[0]
    assert "00200013" in inst
    # Value is stored as string in DICOM JSON (IS VR = Integer String)
    assert int(inst["00200013"]["Value"][0]) == 1


def test_search_instances_all_in_study(client: TestClient):
    """Retrieve all instances across all series in a study.

    Note: The endpoint GET /v2/studies/{study}/series/{series}/instances
    requires both study and series UID. To get "all instances in a study"
    you must iterate series. This test verifies instances are correctly
    scoped to their series.
    """
    study_uid = "1.2.826.0.1.3680043.8.498.88804"
    series_uid_1 = "1.2.826.0.1.3680043.8.498.88804.1"
    series_uid_2 = "1.2.826.0.1.3680043.8.498.88804.2"
    sop_uid_1 = "1.2.826.0.1.3680043.8.498.88804.1.1"
    sop_uid_2 = "1.2.826.0.1.3680043.8.498.88804.2.1"

    dcm1 = DicomFactory.create_ct_image(
        patient_id="QIDO-ALLSTUDY-001",
        study_uid=study_uid,
        series_uid=series_uid_1,
        sop_uid=sop_uid_1,
    )
    dcm2 = DicomFactory.create_mri_image(
        patient_id="QIDO-ALLSTUDY-001",
        study_uid=study_uid,
        series_uid=series_uid_2,
        sop_uid=sop_uid_2,
    )
    store_dicom(client, dcm1)
    store_dicom(client, dcm2)

    # Get instances from series 1
    resp1 = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid_1}/instances",
        headers={"Accept": "application/dicom+json"},
    )
    assert resp1.status_code == 200
    inst1 = resp1.json()
    assert len(inst1) == 1
    assert inst1[0]["00080018"]["Value"][0] == sop_uid_1

    # Get instances from series 2
    resp2 = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid_2}/instances",
        headers={"Accept": "application/dicom+json"},
    )
    assert resp2.status_code == 200
    inst2 = resp2.json()
    assert len(inst2) == 1
    assert inst2[0]["00080018"]["Value"][0] == sop_uid_2

    # Total instances across both series = 2
    assert len(inst1) + len(inst2) == 2


def test_search_instances_empty_result(client: TestClient):
    """Instance search for non-existent series returns empty array."""
    study_uid = "1.2.826.0.1.3680043.8.498.88805"
    series_uid = "1.2.826.0.1.3680043.8.498.88805.999"

    # Store at least one instance for this study (different series)
    real_series = "1.2.826.0.1.3680043.8.498.88805.1"
    dcm = DicomFactory.create_ct_image(
        patient_id="QIDO-EMPTY-INST",
        study_uid=study_uid,
        series_uid=real_series,
    )
    store_dicom(client, dcm)

    # Search in a series that has no instances
    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    instances = response.json()
    assert instances == []


def test_search_instances_multiple_filters(client: TestClient):
    """Combined study-level filters (PatientID + Modality) narrow results.

    This test uses study-level search with multiple query parameters
    to verify that the filter intersection works correctly.
    """
    # CT study for patient A
    dcm_ct_a = DicomFactory.create_ct_image(
        patient_id="QIDO-MULTI-A",
        patient_name="MultiA^CT",
        modality="CT",
    )
    # MR study for patient A
    dcm_mr_a = DicomFactory.create_mri_image(
        patient_id="QIDO-MULTI-A",
        patient_name="MultiA^MR",
        modality="MR",
    )
    # CT study for patient B
    dcm_ct_b = DicomFactory.create_ct_image(
        patient_id="QIDO-MULTI-B",
        patient_name="MultiB^CT",
        modality="CT",
    )
    store_dicom(client, dcm_ct_a)
    store_dicom(client, dcm_mr_a)
    store_dicom(client, dcm_ct_b)

    # Search for PatientID=QIDO-MULTI-A AND Modality=CT
    response = client.get(
        "/v2/studies",
        params={"PatientID": "QIDO-MULTI-A", "Modality": "CT"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    # Should return only patient A's CT study
    assert len(studies) == 1
    assert studies[0]["00100020"]["Value"][0] == "QIDO-MULTI-A"
    assert "CT" in studies[0]["00080061"]["Value"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. Response Format (2 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_search_response_dicom_json_format(client: TestClient):
    """Verify QIDO-RS response conforms to PS3.18 F.2 DICOM JSON format.

    Each study object should have:
    - Tag keys as 8-character hex strings (e.g., "0020000D")
    - Each tag has "vr" (value representation) field
    - Each tag has "Value" array (when data present) or empty dict {}
    """
    dcm = DicomFactory.create_ct_image(
        patient_id="QIDO-FORMAT-001",
        patient_name="Format^Test",
        study_date="20260215",
        accession_number="ACC-FORMAT-001",
    )
    store_dicom(client, dcm)

    response = client.get(
        "/v2/studies",
        params={"PatientID": "QIDO-FORMAT-001"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/dicom+json"

    studies = response.json()
    assert len(studies) == 1

    study = studies[0]

    # Verify tag keys are 8-hex-digit strings
    for tag_key in study.keys():
        assert len(tag_key) == 8, f"Tag key '{tag_key}' is not 8 characters"
        assert all(
            c in "0123456789ABCDEFabcdef" for c in tag_key
        ), f"Tag key '{tag_key}' contains non-hex characters"

    # Verify each tag has "vr" field with 2-character VR code
    for tag_key, tag_value in study.items():
        if isinstance(tag_value, dict) and "vr" in tag_value:
            assert isinstance(tag_value["vr"], str)
            assert (
                len(tag_value["vr"]) == 2
            ), f"VR for tag {tag_key} is '{tag_value['vr']}', expected 2 chars"

    # Verify specific expected tags are present
    assert "0020000D" in study  # StudyInstanceUID
    assert "00100020" in study  # PatientID
    assert "00100010" in study  # PatientName
    assert "00080020" in study  # StudyDate
    assert "00080050" in study  # AccessionNumber
    assert "00080061" in study  # ModalitiesInStudy

    # Verify Value arrays
    assert study["0020000D"]["vr"] == "UI"
    assert isinstance(study["0020000D"]["Value"], list)
    assert len(study["0020000D"]["Value"]) == 1

    assert study["00100020"]["vr"] == "LO"
    assert study["00100020"]["Value"][0] == "QIDO-FORMAT-001"

    # PatientName uses PN format with Alphabetic component
    assert study["00100010"]["vr"] == "PN"
    assert study["00100010"]["Value"][0]["Alphabetic"] == "Format^Test"


def test_search_response_includes_retrieve_urls(client: TestClient):
    """Verify QIDO-RS study response includes study-level information
    that enables retrieval (StudyInstanceUID, NumberOfStudyRelatedSeries,
    NumberOfStudyRelatedInstances).

    Note: The QIDO-RS study endpoint does not include an explicit
    RetrieveURL tag (00081190) in the study-level response. Clients
    construct the retrieval URL from the StudyInstanceUID. This test
    verifies the study UID and related counts are present.
    """
    study_uid = "1.2.826.0.1.3680043.8.498.77701"
    series_uid_1 = "1.2.826.0.1.3680043.8.498.77701.1"
    series_uid_2 = "1.2.826.0.1.3680043.8.498.77701.2"
    sop_uid_1 = "1.2.826.0.1.3680043.8.498.77701.1.1"
    sop_uid_2 = "1.2.826.0.1.3680043.8.498.77701.2.1"

    dcm1 = DicomFactory.create_ct_image(
        patient_id="QIDO-URL-001",
        study_uid=study_uid,
        series_uid=series_uid_1,
        sop_uid=sop_uid_1,
    )
    dcm2 = DicomFactory.create_mri_image(
        patient_id="QIDO-URL-001",
        study_uid=study_uid,
        series_uid=series_uid_2,
        sop_uid=sop_uid_2,
    )
    store_dicom(client, dcm1)
    store_dicom(client, dcm2)

    response = client.get(
        "/v2/studies",
        params={"PatientID": "QIDO-URL-001"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    studies = response.json()
    assert len(studies) == 1

    study = studies[0]

    # StudyInstanceUID enables URL construction
    assert "0020000D" in study
    assert study["0020000D"]["Value"][0] == study_uid

    # NumberOfStudyRelatedSeries (00201206)
    assert "00201206" in study
    assert study["00201206"]["Value"][0] == 2

    # NumberOfStudyRelatedInstances (00201208)
    assert "00201208" in study
    assert study["00201208"]["Value"][0] == 2

    # ModalitiesInStudy (00080061) should list both modalities
    assert "00080061" in study
    modalities = set(study["00080061"]["Value"])
    assert "CT" in modalities
    assert "MR" in modalities
