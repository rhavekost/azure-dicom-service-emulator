"""Integration tests for POST /v2/studies/$bulkUpdate endpoint.

Tests the bulk update API which updates DICOM metadata across multiple
instances in one or more studies in a single operation.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.dicom import DicomStudy
from tests.fixtures.factories import DicomFactory

pytestmark = pytest.mark.integration

# Valid DICOM UIDs (digits and dots only)
STUDY_UID_1 = "2.25.100000000000000000000000000000000001"
STUDY_UID_2 = "2.25.100000000000000000000000000000000002"
STUDY_UID_3 = "2.25.100000000000000000000000000000000003"
STUDY_UID_4 = "2.25.100000000000000000000000000000000004"
STUDY_UID_5 = "2.25.100000000000000000000000000000000005"
STUDY_UID_6 = "2.25.100000000000000000000000000000000006"
STUDY_UID_7 = "2.25.100000000000000000000000000000000007"
STUDY_UID_8 = "2.25.100000000000000000000000000000000008"
STUDY_UID_9 = "2.25.100000000000000000000000000000000009"
STUDY_UID_10 = "2.25.100000000000000000000000000000000010"
STUDY_UID_11 = "2.25.100000000000000000000000000000000011"
STUDY_UID_12 = "2.25.100000000000000000000000000000000012"
STUDY_UID_13 = "2.25.100000000000000000000000000000000013"
NONEXISTENT_UID = "9.9.9.99999999"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def build_multipart_request(
    dicom_files: list[bytes], boundary: str = "boundary123"
) -> tuple[bytes, str]:
    """Build a multipart/related request body for STOW-RS."""
    parts = []
    for dcm_data in dicom_files:
        part = (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n" f"\r\n").encode()
        part += dcm_data + b"\r\n"
        parts.append(part)

    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    content_type = f'multipart/related; type="application/dicom"; boundary={boundary}'
    return body, content_type


def store_instance(client: TestClient, dicom_bytes: bytes) -> dict:
    """Store a DICOM instance; assert HTTP 200 and return the response JSON."""
    body, content_type = build_multipart_request([dicom_bytes])
    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert response.status_code == 200, f"Store failed: {response.status_code} {response.text}"
    return response.json()


def bulk_update(client: TestClient, study_uids: list[str], change_dataset: dict):
    """POST /v2/studies/$bulkUpdate and return the raw response."""
    return client.post(
        "/v2/studies/$bulkUpdate",
        json={
            "studyInstanceUids": study_uids,
            "changeDataset": change_dataset,
        },
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. Happy path — single study
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_bulk_update_returns_202_with_location(client: TestClient):
    """POST /v2/studies/$bulkUpdate should return 202 with a Location header."""
    dicom_bytes = DicomFactory.create_ct_image(
        study_uid=STUDY_UID_1,
        patient_id="PAT001",
        patient_name="Smith^John",
    )
    store_instance(client, dicom_bytes)

    response = bulk_update(
        client,
        study_uids=[STUDY_UID_1],
        change_dataset={"00100020": {"vr": "LO", "Value": ["NEW_PATIENT_ID"]}},
    )

    assert response.status_code == 202
    assert "location" in response.headers
    assert "/v2/operations/" in response.headers["location"]


def test_bulk_update_operation_succeeds(client: TestClient):
    """The created operation should have status 'succeeded' when polled."""
    dicom_bytes = DicomFactory.create_ct_image(study_uid=STUDY_UID_2, patient_id="PAT002")
    store_instance(client, dicom_bytes)

    response = bulk_update(
        client,
        study_uids=[STUDY_UID_2],
        change_dataset={"00100020": {"vr": "LO", "Value": ["UPDATED_ID"]}},
    )

    assert response.status_code == 202
    location = response.headers["location"]
    op_response = client.get(location)
    assert op_response.status_code == 200
    op_data = op_response.json()
    assert op_data["status"] == "succeeded"
    assert op_data["type"] == "bulk-update"


def test_bulk_update_patches_patient_id_in_dicom_json(client: TestClient):
    """Updated PatientID (00100020) should be reflected in WADO metadata."""
    dicom_bytes = DicomFactory.create_ct_image(
        study_uid=STUDY_UID_3,
        patient_id="OLD_ID",
        patient_name="Old^Name",
    )
    store_instance(client, dicom_bytes)

    bulk_update(
        client,
        study_uids=[STUDY_UID_3],
        change_dataset={"00100020": {"vr": "LO", "Value": ["NEW_ID"]}},
    )

    metadata_response = client.get(f"/v2/studies/{STUDY_UID_3}/metadata")
    assert metadata_response.status_code == 200
    metadata = metadata_response.json()
    assert len(metadata) == 1
    assert metadata[0]["00100020"]["Value"] == ["NEW_ID"]


def test_bulk_update_patches_patient_name_in_dicom_json(client: TestClient):
    """Updated PatientName (00100010) should be reflected in WADO metadata."""
    dicom_bytes = DicomFactory.create_ct_image(
        study_uid=STUDY_UID_4,
        patient_id="PAT004",
        patient_name="Old^Name",
    )
    store_instance(client, dicom_bytes)

    bulk_update(
        client,
        study_uids=[STUDY_UID_4],
        change_dataset={"00100010": {"vr": "PN", "Value": [{"Alphabetic": "New^Name"}]}},
    )

    metadata_response = client.get(f"/v2/studies/{STUDY_UID_4}/metadata")
    assert metadata_response.status_code == 200
    metadata = metadata_response.json()
    assert metadata[0]["00100010"]["Value"] == [{"Alphabetic": "New^Name"}]


def test_bulk_update_updates_indexed_patient_id_column(client: TestClient):
    """The patient_id indexed column should be updated, enabling QIDO search."""
    dicom_bytes = DicomFactory.create_ct_image(
        study_uid=STUDY_UID_5,
        patient_id="BEFORE_UPDATE",
    )
    store_instance(client, dicom_bytes)

    # QIDO should find with old patient_id
    qido_before = client.get("/v2/studies?PatientID=BEFORE_UPDATE")
    assert qido_before.status_code == 200
    assert len(qido_before.json()) == 1

    bulk_update(
        client,
        study_uids=[STUDY_UID_5],
        change_dataset={"00100020": {"vr": "LO", "Value": ["AFTER_UPDATE"]}},
    )

    # QIDO should now find with new patient_id
    qido_after = client.get("/v2/studies?PatientID=AFTER_UPDATE")
    assert qido_after.status_code == 200
    assert len(qido_after.json()) == 1

    # Old patient_id should return nothing
    qido_old = client.get("/v2/studies?PatientID=BEFORE_UPDATE")
    assert qido_old.status_code == 200
    assert len(qido_old.json()) == 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. Multiple studies
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_bulk_update_multiple_studies(client: TestClient):
    """Bulk update should apply changeDataset to all specified studies."""
    store_instance(
        client,
        DicomFactory.create_ct_image(study_uid=STUDY_UID_6, patient_id="PAT_A"),
    )
    store_instance(
        client,
        DicomFactory.create_ct_image(study_uid=STUDY_UID_7, patient_id="PAT_B"),
    )

    response = bulk_update(
        client,
        study_uids=[STUDY_UID_6, STUDY_UID_7],
        change_dataset={"00100020": {"vr": "LO", "Value": ["MERGED_ID"]}},
    )

    assert response.status_code == 202

    for uid in (STUDY_UID_6, STUDY_UID_7):
        meta = client.get(f"/v2/studies/{uid}/metadata").json()
        assert meta[0]["00100020"]["Value"] == ["MERGED_ID"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. Change feed
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_bulk_update_creates_change_feed_entries(client: TestClient):
    """A change feed entry with state='replaced' should be created per instance."""
    dicom_bytes = DicomFactory.create_ct_image(study_uid=STUDY_UID_8, patient_id="PAT_CF")
    store_instance(client, dicom_bytes)

    bulk_update(
        client,
        study_uids=[STUDY_UID_8],
        change_dataset={"00100020": {"vr": "LO", "Value": ["CF_UPDATED"]}},
    )

    feed = client.get("/v2/changefeed?limit=100").json()
    study_entries = [e for e in feed if e.get("StudyInstanceUID") == STUDY_UID_8]
    replaced_entries = [e for e in study_entries if e.get("State") == "replaced"]
    assert len(replaced_entries) >= 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. Edge cases — non-existent studies silently skipped
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_bulk_update_nonexistent_study_is_silently_skipped(client: TestClient):
    """A study UID that doesn't exist should not cause an error."""
    response = bulk_update(
        client,
        study_uids=[NONEXISTENT_UID],
        change_dataset={"00100020": {"vr": "LO", "Value": ["GHOST_ID"]}},
    )
    assert response.status_code == 202


def test_bulk_update_mix_existing_and_nonexistent(client: TestClient):
    """Existing studies are updated; non-existent ones are silently skipped."""
    store_instance(
        client,
        DicomFactory.create_ct_image(study_uid=STUDY_UID_9, patient_id="PAT_MIX"),
    )

    response = bulk_update(
        client,
        study_uids=[STUDY_UID_9, NONEXISTENT_UID],
        change_dataset={"00100020": {"vr": "LO", "Value": ["MIX_UPDATED"]}},
    )

    assert response.status_code == 202

    meta = client.get(f"/v2/studies/{STUDY_UID_9}/metadata").json()
    assert meta[0]["00100020"]["Value"] == ["MIX_UPDATED"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. Validation errors
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_bulk_update_missing_study_uids_returns_400(client: TestClient):
    """Missing studyInstanceUids should return 400 Bad Request."""
    response = client.post(
        "/v2/studies/$bulkUpdate",
        json={"changeDataset": {"00100020": {"vr": "LO", "Value": ["X"]}}},
    )
    assert response.status_code == 400


def test_bulk_update_empty_study_uids_returns_400(client: TestClient):
    """Empty studyInstanceUids list should return 400 Bad Request."""
    response = client.post(
        "/v2/studies/$bulkUpdate",
        json={
            "studyInstanceUids": [],
            "changeDataset": {"00100020": {"vr": "LO", "Value": ["X"]}},
        },
    )
    assert response.status_code == 400


def test_bulk_update_missing_change_dataset_returns_400(client: TestClient):
    """Missing changeDataset should return 400 Bad Request."""
    response = client.post(
        "/v2/studies/$bulkUpdate",
        json={"studyInstanceUids": [STUDY_UID_10]},
    )
    assert response.status_code == 400


def test_bulk_update_empty_change_dataset_returns_400(client: TestClient):
    """Empty changeDataset should return 400 Bad Request."""
    response = client.post(
        "/v2/studies/$bulkUpdate",
        json={"studyInstanceUids": [STUDY_UID_10], "changeDataset": {}},
    )
    assert response.status_code == 400


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  6. Multiple instances in a study
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_bulk_update_updates_all_instances_in_study(client: TestClient):
    """All instances belonging to the study should be updated."""
    from pydicom.uid import generate_uid

    series_uid = generate_uid()

    # Store three instances in the same study
    for _ in range(3):
        dicom_bytes = DicomFactory.create_ct_image(
            study_uid=STUDY_UID_11,
            series_uid=series_uid,
            sop_uid=generate_uid(),
            patient_id="PAT_MULTI",
        )
        store_instance(client, dicom_bytes)

    bulk_update(
        client,
        study_uids=[STUDY_UID_11],
        change_dataset={"00100020": {"vr": "LO", "Value": ["BULK_UPDATED"]}},
    )

    metadata_response = client.get(f"/v2/studies/{STUDY_UID_11}/metadata")
    assert metadata_response.status_code == 200
    metadata = metadata_response.json()
    assert len(metadata) == 3
    for instance_meta in metadata:
        assert instance_meta["00100020"]["Value"] == ["BULK_UPDATED"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  7. Partial tag update — existing tags preserved
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_bulk_update_preserves_unmodified_tags(client: TestClient):
    """Tags not in changeDataset should remain unchanged in dicom_json."""
    dicom_bytes = DicomFactory.create_ct_image(
        study_uid=STUDY_UID_12,
        patient_id="ORIG_ID",
        patient_name="Orig^Name",
    )
    store_instance(client, dicom_bytes)

    # Only update patient_id; patient_name should stay intact
    bulk_update(
        client,
        study_uids=[STUDY_UID_12],
        change_dataset={"00100020": {"vr": "LO", "Value": ["CHANGED_ID"]}},
    )

    metadata_response = client.get(f"/v2/studies/{STUDY_UID_12}/metadata")
    assert metadata_response.status_code == 200
    metadata = metadata_response.json()

    # PatientID changed
    assert metadata[0]["00100020"]["Value"] == ["CHANGED_ID"]

    # PatientName (00100010) should still be present (not wiped out)
    assert "00100010" in metadata[0]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  8. DicomStudy record is updated
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def test_bulk_update_updates_dicom_study_patient_id(client: TestClient, db_session):
    """Bulk update should update the DicomStudy.patient_id indexed column.

    After a bulk update that changes PatientID (00100020), the corresponding
    DicomStudy row for that study_instance_uid must reflect the new value.
    """
    dicom_bytes = DicomFactory.create_ct_image(
        study_uid=STUDY_UID_13,
        patient_id="STUDY_BEFORE",
        patient_name="Study^Patient",
    )
    store_instance(client, dicom_bytes)

    bulk_update(
        client,
        study_uids=[STUDY_UID_13],
        change_dataset={"00100020": {"vr": "LO", "Value": ["STUDY_AFTER"]}},
    )

    # Query DicomStudy directly to verify the study-level record was updated.
    async with db_session() as session:
        result = await session.execute(
            select(DicomStudy).where(DicomStudy.study_instance_uid == STUDY_UID_13)
        )
        dicom_study = result.scalar_one_or_none()

    assert dicom_study is not None, "DicomStudy record should exist after bulk update"
    assert (
        dicom_study.patient_id == "STUDY_AFTER"
    ), f"DicomStudy.patient_id should be 'STUDY_AFTER', got {dicom_study.patient_id!r}"
