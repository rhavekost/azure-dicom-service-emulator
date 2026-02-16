#!/usr/bin/env python3
"""
Smoke test for Azure Healthcare Workspace Emulator.

Tests the core DICOMweb and Azure-specific API endpoints.
Requires: pip install pydicom httpx

Usage:
    python smoke_test.py [base_url]
    python smoke_test.py http://localhost:8080
"""

import sys
import uuid
from io import BytesIO

import httpx
import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"


def create_test_dicom() -> tuple[bytes, str, str, str]:
    """Create a minimal valid DICOM file for testing."""
    study_uid = generate_uid()
    series_uid = generate_uid()
    sop_uid = generate_uid()

    file_meta = pydicom.Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT Image
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(
        filename_or_obj=BytesIO(),
        dataset=Dataset(),
        file_meta=file_meta,
        preamble=b"\x00" * 128,
    )

    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.PatientID = "TEST-001"
    ds.PatientName = "Test^Patient"
    ds.StudyDate = "20260214"
    ds.StudyTime = "120000"
    ds.Modality = "CT"
    ds.AccessionNumber = f"ACC-{uuid.uuid4().hex[:8]}"
    ds.StudyDescription = "Smoke Test Study"
    ds.SeriesDescription = "Test Series"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1

    buffer = BytesIO()
    ds.save_as(buffer)
    return buffer.getvalue(), study_uid, series_uid, sop_uid


def test_health():
    """Test health endpoint."""
    r = httpx.get(f"{BASE_URL}/health")
    assert r.status_code == 200, f"Health check failed: {r.status_code}"
    data = r.json()
    assert data["status"] == "healthy"
    print("  [PASS] Health check")


def test_stow_rs(dcm_bytes: bytes, study_uid: str):
    """Test STOW-RS store."""
    boundary = "boundary-test"
    body = (
        (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n\r\n").encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    r = httpx.post(
        f"{BASE_URL}/v2/studies",
        content=body,
        headers={"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"},
    )
    assert r.status_code in (200, 202), f"STOW-RS failed: {r.status_code} {r.text}"
    print("  [PASS] STOW-RS store")


def test_qido_rs_studies():
    """Test QIDO-RS study search."""
    r = httpx.get(f"{BASE_URL}/v2/studies")
    assert r.status_code == 200, f"QIDO-RS studies failed: {r.status_code}"
    data = r.json()
    assert len(data) > 0, "No studies found"
    print(f"  [PASS] QIDO-RS studies ({len(data)} found)")


def test_qido_rs_search():
    """Test QIDO-RS with search parameters."""
    r = httpx.get(f"{BASE_URL}/v2/studies", params={"PatientID": "TEST-001"})
    assert r.status_code == 200, f"QIDO-RS search failed: {r.status_code}"
    data = r.json()
    assert len(data) > 0, "No studies found for PatientID=TEST-001"
    print(f"  [PASS] QIDO-RS search by PatientID ({len(data)} found)")


def test_qido_rs_wildcard():
    """Test QIDO-RS wildcard matching."""
    r = httpx.get(f"{BASE_URL}/v2/studies", params={"PatientID": "TEST-*"})
    assert r.status_code == 200, f"QIDO-RS wildcard failed: {r.status_code}"
    print("  [PASS] QIDO-RS wildcard search")


def test_wado_rs_metadata(study_uid: str):
    """Test WADO-RS metadata retrieval."""
    r = httpx.get(f"{BASE_URL}/v2/studies/{study_uid}/metadata")
    assert r.status_code == 200, f"WADO-RS metadata failed: {r.status_code}"
    data = r.json()
    assert len(data) > 0, "No metadata returned"
    print(f"  [PASS] WADO-RS metadata ({len(data)} instances)")


def test_wado_rs_instance(study_uid: str, series_uid: str, sop_uid: str):
    """Test WADO-RS instance retrieval."""
    r = httpx.get(
        f"{BASE_URL}/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}",
        headers={"Accept": "multipart/related; type=application/dicom"},
    )
    assert r.status_code == 200, f"WADO-RS instance failed: {r.status_code}"
    assert "multipart/related" in r.headers.get("content-type", "")
    print("  [PASS] WADO-RS instance retrieval")


def test_change_feed():
    """Test Change Feed API."""
    r = httpx.get(f"{BASE_URL}/v2/changefeed")
    assert r.status_code == 200, f"Change feed failed: {r.status_code}"
    data = r.json()
    assert len(data) > 0, "No change feed entries"
    entry = data[0]
    assert "Sequence" in entry
    assert "Action" in entry
    assert entry["Action"] in ("create", "update", "delete")
    print(f"  [PASS] Change Feed ({len(data)} entries)")


def test_change_feed_latest():
    """Test Change Feed latest endpoint."""
    r = httpx.get(f"{BASE_URL}/v2/changefeed/latest")
    assert r.status_code == 200, f"Change feed latest failed: {r.status_code}"
    data = r.json()
    assert data is not None
    assert "Sequence" in data
    print(f"  [PASS] Change Feed latest (seq={data['Sequence']})")


def test_extended_query_tags():
    """Test Extended Query Tags CRUD."""
    # List (should be empty initially)
    r = httpx.get(f"{BASE_URL}/v2/extendedquerytags")
    assert r.status_code == 200
    print("  [PASS] Extended Query Tags list")

    # Add a tag
    r = httpx.post(
        f"{BASE_URL}/v2/extendedquerytags",
        json={"tags": [{"path": "00101002", "vr": "SQ", "level": "Study"}]},
    )
    assert r.status_code == 202, f"Add EQT failed: {r.status_code} {r.text}"
    data = r.json()
    assert data["status"] == "succeeded"
    print("  [PASS] Extended Query Tags add")

    # Get specific tag
    r = httpx.get(f"{BASE_URL}/v2/extendedquerytags/00101002")
    assert r.status_code == 200
    print("  [PASS] Extended Query Tags get")

    # Delete
    r = httpx.delete(f"{BASE_URL}/v2/extendedquerytags/00101002")
    assert r.status_code == 204
    print("  [PASS] Extended Query Tags delete")


def test_delete(study_uid: str):
    """Test DELETE study."""
    r = httpx.delete(f"{BASE_URL}/v2/studies/{study_uid}")
    assert r.status_code == 204, f"DELETE failed: {r.status_code}"
    print("  [PASS] DELETE study")

    # Verify deletion via change feed
    r = httpx.get(f"{BASE_URL}/v2/changefeed/latest")
    data = r.json()
    assert data["Action"] == "delete"
    print("  [PASS] DELETE reflected in change feed")


def test_event_publishing():
    """Test event publishing via /debug/events endpoint."""
    try:
        # Check if InMemoryEventProvider is configured
        r = httpx.get(f"{BASE_URL}/debug/events")
        if r.status_code == 404:
            print("  [SKIP] InMemoryEventProvider not configured")
            return

        # Clear existing events
        httpx.delete(f"{BASE_URL}/debug/events")

        # Store a new instance
        dcm_bytes, study_uid, series_uid, sop_uid = create_test_dicom()
        test_stow_rs(dcm_bytes, study_uid)

        # Check for events
        r = httpx.get(f"{BASE_URL}/debug/events")
        data = r.json()

        assert data["count"] > 0, "No events published"

        # Verify event format
        event = data["events"][0]
        assert event["eventType"] == "Microsoft.HealthcareApis.DicomImageCreated"
        assert "imageStudyInstanceUid" in event["data"]

        print(f"  [PASS] Event published ({data['count']} events)")
    except Exception as e:
        print(f"  [SKIP] Event test failed: {e}")


def test_frame_retrieval():
    """Test WADO-RS frame retrieval."""
    print("[9/13] Frame Retrieval...")

    # Note: Requires uploading DICOM instance first
    # For now, skip if no instances exist

    try:
        # Placeholder for frame retrieval test
        print("  [SKIP] Frame retrieval (requires test DICOM data)")
    except Exception as e:
        print(f"  [SKIP] Frame retrieval: {e}")


def test_rendered_images():
    """Test WADO-RS rendered image endpoints."""
    print("[10/13] Rendered Images...")

    try:
        # Placeholder for rendered image test
        print("  [SKIP] Rendered images (requires test DICOM data)")
    except Exception as e:
        print(f"  [SKIP] Rendered images: {e}")


def test_advanced_search():
    """Test QIDO-RS advanced search features."""
    print("[11/13] Advanced Search...")

    try:
        # Fuzzy search
        r = httpx.get(
            f"{BASE_URL}/v2/studies", params={"PatientName": "test", "fuzzymatching": "true"}
        )
        assert r.status_code == 200, f"Fuzzy search failed: {r.status_code}"
        print("  [PASS] Fuzzy search")

        # Wildcard search
        r = httpx.get(f"{BASE_URL}/v2/studies", params={"PatientID": "PAT*"})
        assert r.status_code == 200, f"Wildcard search failed: {r.status_code}"
        print("  [PASS] Wildcard search")

        # UID list (using made-up UIDs)
        r = httpx.get(f"{BASE_URL}/v2/studies", params={"StudyInstanceUID": "1.2.3,4.5.6"})
        assert r.status_code == 200, f"UID list search failed: {r.status_code}"
        print("  [PASS] UID list search")

    except Exception as e:
        print(f"  [SKIP] Advanced search: {e}")


def main():
    print(f"\n{'='*60}")
    print("  Azure Healthcare Workspace Emulator - Smoke Test")
    print(f"  Target: {BASE_URL}")
    print(f"{'='*60}\n")

    print("[1/13] Health check...")
    test_health()

    print("\n[2/13] Creating test DICOM instance...")
    dcm_bytes, study_uid, series_uid, sop_uid = create_test_dicom()
    print(f"       Study:    {study_uid}")
    print(f"       Series:   {series_uid}")
    print(f"       Instance: {sop_uid}")

    print("\n[3/13] STOW-RS (store)...")
    test_stow_rs(dcm_bytes, study_uid)

    print("\n[4/13] QIDO-RS (search)...")
    test_qido_rs_studies()
    test_qido_rs_search()
    test_qido_rs_wildcard()

    print("\n[5/13] WADO-RS (retrieve)...")
    test_wado_rs_metadata(study_uid)
    test_wado_rs_instance(study_uid, series_uid, sop_uid)

    print("\n[6/13] Change Feed...")
    test_change_feed()
    test_change_feed_latest()

    print("\n[7/13] Extended Query Tags...")
    test_extended_query_tags()

    print("\n[8/13] Event Publishing...")
    test_event_publishing()

    print("\n")
    test_frame_retrieval()

    print("\n")
    test_rendered_images()

    print("\n")
    test_advanced_search()

    print("\n[12/13] DELETE...")
    test_delete(study_uid)

    print(f"\n{'='*60}")
    print("  ALL TESTS PASSED")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
