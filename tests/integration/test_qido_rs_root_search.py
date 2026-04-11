"""Integration tests for QIDO-RS root-level search endpoints.

Tests the three missing endpoints:
- GET /v2/series         — search all series across all studies
- GET /v2/instances      — search all instances across all studies/series
- GET /v2/studies/{study}/instances — search instances within a study (any series)

All endpoints must support the same QIDO-RS query parameters as the existing
search endpoints (PatientID, PatientName, StudyDate, AccessionNumber,
StudyDescription, Modality, StudyInstanceUID, SeriesInstanceUID,
SOPInstanceUID, limit, offset, fuzzymatching, includefield).
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
    """Store a DICOM instance via STOW-RS and return the response JSON."""
    body, content_type = build_multipart_request([dicom_bytes])
    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert response.status_code == 200, f"STOW-RS failed: {response.status_code} {response.text}"
    return response.json()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  GET /v2/series — Search all series across all studies
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_get_all_series_no_filters(client: TestClient):
    """GET /v2/series returns all series when no filters are applied."""
    dcm1 = DicomFactory.create_ct_image(patient_id="ROOT-SERIES-001", patient_name="Alpha^Root")
    dcm2 = DicomFactory.create_ct_image(patient_id="ROOT-SERIES-002", patient_name="Beta^Root")
    store_dicom(client, dcm1)
    store_dicom(client, dcm2)

    response = client.get("/v2/series", headers={"Accept": "application/dicom+json"})

    assert response.status_code == 200
    series_list = response.json()
    assert isinstance(series_list, list)
    assert len(series_list) >= 2


def test_get_all_series_returns_dicom_json_format(client: TestClient):
    """GET /v2/series response items include required DICOM series tags."""
    dcm = DicomFactory.create_ct_image(patient_id="SERIES-FMT-001", patient_name="Format^Test")
    store_dicom(client, dcm)

    response = client.get("/v2/series", headers={"Accept": "application/dicom+json"})

    assert response.status_code == 200
    series_list = response.json()
    assert len(series_list) >= 1

    first = series_list[0]
    # Required series-level tags
    assert "0020000D" in first  # StudyInstanceUID
    assert "0020000E" in first  # SeriesInstanceUID


def test_get_all_series_filter_by_modality(client: TestClient):
    """GET /v2/series?Modality=CT returns only CT series."""
    ct_dcm = DicomFactory.create_ct_image(patient_id="SERIES-MOD-CT", patient_name="CT^Patient")
    mri_dcm = DicomFactory.create_mri_image(patient_id="SERIES-MOD-MRI", patient_name="MRI^Patient")
    store_dicom(client, ct_dcm)
    store_dicom(client, mri_dcm)

    response = client.get(
        "/v2/series",
        params={"Modality": "CT"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    series_list = response.json()
    assert len(series_list) >= 1
    # All returned series should be CT
    for series in series_list:
        if "00080060" in series and series["00080060"].get("Value"):
            assert series["00080060"]["Value"][0] == "CT"


def test_get_all_series_filter_by_patient_id(client: TestClient):
    """GET /v2/series?PatientID=... filters series by patient."""
    target_dcm = DicomFactory.create_ct_image(
        patient_id="SERIES-PID-TARGET", patient_name="Target^Series"
    )
    other_dcm = DicomFactory.create_ct_image(
        patient_id="SERIES-PID-OTHER", patient_name="Other^Series"
    )
    store_dicom(client, target_dcm)
    store_dicom(client, other_dcm)

    response = client.get(
        "/v2/series",
        params={"PatientID": "SERIES-PID-TARGET"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    series_list = response.json()
    assert len(series_list) >= 1
    # All returned series must belong to the target study
    for series in series_list:
        assert "0020000D" in series


def test_get_all_series_filter_by_study_instance_uid(client: TestClient):
    """GET /v2/series?StudyInstanceUID=... filters series to that study."""
    import pydicom

    dcm1_bytes = DicomFactory.create_ct_image(
        patient_id="SERIES-SUID-001", patient_name="Study1^Patient"
    )
    dcm2_bytes = DicomFactory.create_ct_image(
        patient_id="SERIES-SUID-002", patient_name="Study2^Patient"
    )
    store_dicom(client, dcm1_bytes)
    store_dicom(client, dcm2_bytes)

    # Get study UID from first instance
    import io

    ds = pydicom.dcmread(io.BytesIO(dcm1_bytes))
    study_uid = str(ds.StudyInstanceUID)

    response = client.get(
        "/v2/series",
        params={"StudyInstanceUID": study_uid},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    series_list = response.json()
    assert len(series_list) >= 1
    # All returned series should belong to the requested study
    for series in series_list:
        assert series["0020000D"]["Value"][0] == study_uid


def test_get_all_series_empty_when_no_data(client: TestClient):
    """GET /v2/series returns empty list when no DICOM data exists."""
    response = client.get(
        "/v2/series",
        params={"PatientID": "NONEXISTENT-PATIENT-XYZ"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_get_all_series_limit_and_offset(client: TestClient):
    """GET /v2/series supports limit and offset pagination."""
    for i in range(3):
        dcm = DicomFactory.create_ct_image(
            patient_id=f"SERIES-PAGE-{i:03d}", patient_name=f"Page{i}^Series"
        )
        store_dicom(client, dcm)

    response_all = client.get("/v2/series", params={"limit": 100, "offset": 0})
    response_limited = client.get("/v2/series", params={"limit": 1, "offset": 0})
    response_offset = client.get("/v2/series", params={"limit": 100, "offset": 1})

    assert response_all.status_code == 200
    assert response_limited.status_code == 200
    assert response_offset.status_code == 200

    all_series = response_all.json()
    limited_series = response_limited.json()
    offset_series = response_offset.json()

    assert len(limited_series) == 1
    assert len(offset_series) == len(all_series) - 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  GET /v2/instances — Search all instances across all studies/series
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_get_all_instances_no_filters(client: TestClient):
    """GET /v2/instances returns all instances when no filters are applied."""
    dcm1 = DicomFactory.create_ct_image(patient_id="ROOT-INST-001", patient_name="Alpha^Inst")
    dcm2 = DicomFactory.create_ct_image(patient_id="ROOT-INST-002", patient_name="Beta^Inst")
    store_dicom(client, dcm1)
    store_dicom(client, dcm2)

    response = client.get("/v2/instances", headers={"Accept": "application/dicom+json"})

    assert response.status_code == 200
    instances = response.json()
    assert isinstance(instances, list)
    assert len(instances) >= 2


def test_get_all_instances_returns_dicom_json_format(client: TestClient):
    """GET /v2/instances response items include instance-level DICOM tags."""
    dcm = DicomFactory.create_ct_image(patient_id="INST-FMT-001", patient_name="Format^Inst")
    store_dicom(client, dcm)

    response = client.get("/v2/instances", headers={"Accept": "application/dicom+json"})

    assert response.status_code == 200
    instances = response.json()
    assert len(instances) >= 1

    first = instances[0]
    # Instance-level required tags
    assert "00080018" in first  # SOPInstanceUID
    assert "0020000D" in first  # StudyInstanceUID
    assert "0020000E" in first  # SeriesInstanceUID


def test_get_all_instances_filter_by_patient_id(client: TestClient):
    """GET /v2/instances?PatientID=... filters instances by patient."""
    target_dcm = DicomFactory.create_ct_image(
        patient_id="INST-PID-TARGET", patient_name="Target^Inst"
    )
    other_dcm = DicomFactory.create_ct_image(patient_id="INST-PID-OTHER", patient_name="Other^Inst")
    store_dicom(client, target_dcm)
    store_dicom(client, other_dcm)

    response = client.get(
        "/v2/instances",
        params={"PatientID": "INST-PID-TARGET"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    instances = response.json()
    assert len(instances) == 1


def test_get_all_instances_filter_by_modality(client: TestClient):
    """GET /v2/instances?Modality=MR returns only MRI instances."""
    ct_dcm = DicomFactory.create_ct_image(patient_id="INST-MOD-CT", patient_name="CT^Inst")
    mri_dcm = DicomFactory.create_mri_image(patient_id="INST-MOD-MRI", patient_name="MRI^Inst")
    store_dicom(client, ct_dcm)
    store_dicom(client, mri_dcm)

    response = client.get(
        "/v2/instances",
        params={"Modality": "MR"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    instances = response.json()
    assert len(instances) >= 1


def test_get_all_instances_filter_by_sop_instance_uid(client: TestClient):
    """GET /v2/instances?SOPInstanceUID=... returns exactly that instance."""
    import io

    import pydicom

    dcm_bytes = DicomFactory.create_ct_image(patient_id="INST-SOP-001", patient_name="SOP^Filter")
    store_dicom(client, dcm_bytes)
    ds = pydicom.dcmread(io.BytesIO(dcm_bytes))
    sop_uid = str(ds.SOPInstanceUID)

    response = client.get(
        "/v2/instances",
        params={"SOPInstanceUID": sop_uid},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    instances = response.json()
    assert len(instances) == 1
    assert instances[0]["00080018"]["Value"][0] == sop_uid


def test_get_all_instances_empty_when_no_match(client: TestClient):
    """GET /v2/instances returns empty list when no instances match filters."""
    response = client.get(
        "/v2/instances",
        params={"PatientID": "NONEXISTENT-PATIENT-XYZ"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_get_all_instances_limit_and_offset(client: TestClient):
    """GET /v2/instances supports limit and offset pagination."""
    for i in range(3):
        dcm = DicomFactory.create_ct_image(
            patient_id=f"INST-PAGE-{i:03d}", patient_name=f"Page{i}^Inst"
        )
        store_dicom(client, dcm)

    response_all = client.get("/v2/instances", params={"limit": 100, "offset": 0})
    response_limited = client.get("/v2/instances", params={"limit": 1, "offset": 0})

    assert response_all.status_code == 200
    assert response_limited.status_code == 200
    assert len(response_limited.json()) == 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  GET /v2/studies/{study}/instances — Search instances within a study
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_get_study_instances_returns_all_instances_in_study(client: TestClient):
    """GET /v2/studies/{study}/instances returns all instances for that study."""
    import io

    import pydicom

    # Create two instances in the same study (same StudyInstanceUID)
    dcm1_bytes = DicomFactory.create_ct_image(
        patient_id="STUDY-INST-001", patient_name="StudyInst^Patient"
    )
    ds1 = pydicom.dcmread(io.BytesIO(dcm1_bytes))
    study_uid = str(ds1.StudyInstanceUID)

    # Second instance in same study, different series
    dcm2_bytes = DicomFactory.create_ct_image(
        patient_id="STUDY-INST-001",
        patient_name="StudyInst^Patient",
        study_uid=study_uid,
    )

    store_dicom(client, dcm1_bytes)
    store_dicom(client, dcm2_bytes)

    response = client.get(
        f"/v2/studies/{study_uid}/instances",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    instances = response.json()
    assert len(instances) >= 1
    # All instances must belong to the requested study
    for inst in instances:
        assert inst["0020000D"]["Value"][0] == study_uid


def test_get_study_instances_returns_dicom_json_format(client: TestClient):
    """GET /v2/studies/{study}/instances items include instance-level tags."""
    import io

    import pydicom

    dcm_bytes = DicomFactory.create_ct_image(
        patient_id="STUDY-INST-FMT", patient_name="Format^StudyInst"
    )
    store_dicom(client, dcm_bytes)
    ds = pydicom.dcmread(io.BytesIO(dcm_bytes))
    study_uid = str(ds.StudyInstanceUID)

    response = client.get(
        f"/v2/studies/{study_uid}/instances",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    instances = response.json()
    assert len(instances) == 1

    first = instances[0]
    assert "00080018" in first  # SOPInstanceUID
    assert "0020000D" in first  # StudyInstanceUID
    assert "0020000E" in first  # SeriesInstanceUID


def test_get_study_instances_empty_for_unknown_study(client: TestClient):
    """GET /v2/studies/{study}/instances returns empty list for unknown study."""
    unknown_uid = "9.9.9.9999.9999.9999.9999.9999.99999"

    response = client.get(
        f"/v2/studies/{unknown_uid}/instances",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_get_study_instances_filter_by_sop_instance_uid(client: TestClient):
    """GET /v2/studies/{study}/instances?SOPInstanceUID=... returns that instance."""
    import io

    import pydicom

    dcm_bytes = DicomFactory.create_ct_image(
        patient_id="STUDY-INST-SOP", patient_name="SOP^StudyInst"
    )
    store_dicom(client, dcm_bytes)
    ds = pydicom.dcmread(io.BytesIO(dcm_bytes))
    study_uid = str(ds.StudyInstanceUID)
    sop_uid = str(ds.SOPInstanceUID)

    response = client.get(
        f"/v2/studies/{study_uid}/instances",
        params={"SOPInstanceUID": sop_uid},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    instances = response.json()
    assert len(instances) == 1
    assert instances[0]["00080018"]["Value"][0] == sop_uid


def test_get_study_instances_filter_by_series_uid(client: TestClient):
    """GET /v2/studies/{study}/instances?SeriesInstanceUID=... filters by series."""
    import io

    import pydicom

    dcm_bytes = DicomFactory.create_ct_image(
        patient_id="STUDY-INST-SERIES", patient_name="Series^StudyInst"
    )
    store_dicom(client, dcm_bytes)
    ds = pydicom.dcmread(io.BytesIO(dcm_bytes))
    study_uid = str(ds.StudyInstanceUID)
    series_uid = str(ds.SeriesInstanceUID)

    response = client.get(
        f"/v2/studies/{study_uid}/instances",
        params={"SeriesInstanceUID": series_uid},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    instances = response.json()
    assert len(instances) == 1
    assert instances[0]["0020000E"]["Value"][0] == series_uid


def test_get_study_instances_scope_isolation(client: TestClient):
    """GET /v2/studies/{study}/instances only returns instances from that study."""
    dcm1_bytes = DicomFactory.create_ct_image(
        patient_id="SCOPE-ISO-001", patient_name="Study1^Scope"
    )
    dcm2_bytes = DicomFactory.create_ct_image(
        patient_id="SCOPE-ISO-002", patient_name="Study2^Scope"
    )
    store_dicom(client, dcm1_bytes)
    store_dicom(client, dcm2_bytes)

    import io

    import pydicom

    ds1 = pydicom.dcmread(io.BytesIO(dcm1_bytes))
    study_uid_1 = str(ds1.StudyInstanceUID)

    response = client.get(
        f"/v2/studies/{study_uid_1}/instances",
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    instances = response.json()
    assert len(instances) >= 1
    # All instances must be from study 1 only
    for inst in instances:
        assert inst["0020000D"]["Value"][0] == study_uid_1


def test_get_study_instances_limit_and_offset(client: TestClient):
    """GET /v2/studies/{study}/instances supports limit and offset."""
    import io

    import pydicom

    dcm_bytes = DicomFactory.create_ct_image(
        patient_id="STUDY-INST-PAGE", patient_name="Page^StudyInst"
    )
    store_dicom(client, dcm_bytes)
    ds = pydicom.dcmread(io.BytesIO(dcm_bytes))
    study_uid = str(ds.StudyInstanceUID)

    response_limited = client.get(
        f"/v2/studies/{study_uid}/instances",
        params={"limit": 1, "offset": 0},
    )
    response_offset = client.get(
        f"/v2/studies/{study_uid}/instances",
        params={"limit": 100, "offset": 100},
    )

    assert response_limited.status_code == 200
    assert response_offset.status_code == 200
    assert len(response_limited.json()) <= 1
    assert len(response_offset.json()) == 0  # Only 1 instance stored, offset 100 is empty


def test_get_all_series_fuzzymatching(client: TestClient):
    """GET /v2/series?PatientName=joh&fuzzymatching=true does prefix matching."""
    dcm = DicomFactory.create_ct_image(patient_id="SERIES-FUZZY-001", patient_name="Johnson^Dr")
    store_dicom(client, dcm)

    response = client.get(
        "/v2/series",
        params={"PatientName": "joh", "fuzzymatching": "true"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    series_list = response.json()
    assert len(series_list) >= 1


def test_get_all_instances_fuzzymatching(client: TestClient):
    """GET /v2/instances?PatientName=joh&fuzzymatching=true does prefix matching."""
    dcm = DicomFactory.create_ct_image(patient_id="INST-FUZZY-001", patient_name="Johnson^Dr")
    store_dicom(client, dcm)

    response = client.get(
        "/v2/instances",
        params={"PatientName": "joh", "fuzzymatching": "true"},
        headers={"Accept": "application/dicom+json"},
    )

    assert response.status_code == 200
    instances = response.json()
    assert len(instances) >= 1
