"""Performance baseline tests for STOW-RS.

Measures store time for a single DICOM file and a 10-file batch.
Thresholds are intentionally generous for CI environments.

Baseline targets (as of v0.2.0):
  - Single file:  < 5 s
  - 10-file batch: < 30 s

Run against a live service:
    pytest tests/performance/ -m performance
    DICOM_E2E_BASE_URL=http://my-host:8080 pytest tests/performance/ -m performance
"""

import time
import uuid
from io import BytesIO

import httpx
import pytest
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

SINGLE_FILE_MAX_SECONDS = 5.0
BATCH_10_MAX_SECONDS = 30.0

# ---------------------------------------------------------------------------
# Service connectivity
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:8080"


def _service_is_up(base_url: str) -> bool:
    try:
        resp = httpx.get(f"{base_url}/health", timeout=5.0)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_dicom_bytes(
    *,
    patient_id: str = "PERF-001",
    patient_name: str = "Perf^Patient",
    study_uid: str | None = None,
    series_uid: str | None = None,
) -> bytes:
    """Create a minimal valid DICOM instance and return its bytes."""
    study_uid = study_uid or generate_uid()
    series_uid = series_uid or generate_uid()
    sop_uid = generate_uid()

    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT Image Storage
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.1"

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
    ds.PatientID = patient_id
    ds.PatientName = patient_name
    ds.StudyDate = "20260214"
    ds.StudyTime = "120000"
    ds.Modality = "CT"
    ds.AccessionNumber = f"ACC-{uuid.uuid4().hex[:8]}"
    ds.StudyDescription = "Performance Baseline Test"
    ds.SeriesDescription = "Perf Series"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1

    buffer = BytesIO()
    ds.save_as(buffer)
    return buffer.getvalue()


def _stow(client: httpx.Client, dcm_bytes: bytes) -> httpx.Response:
    """POST a single DICOM instance via STOW-RS multipart."""
    boundary = "perf-boundary"
    body = (
        (f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n").encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return client.post(
        "/v2/studies",
        content=body,
        headers={
            "Content-Type": (f"multipart/related; type=application/dicom; boundary={boundary}")
        },
    )


def _stow_batch(client: httpx.Client, instances: list[bytes]) -> httpx.Response:
    """POST multiple DICOM instances in a single multipart/related request."""
    boundary = "perf-batch-boundary"
    parts = b""
    for dcm_bytes in instances:
        parts += (
            f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode()
            + dcm_bytes
            + b"\r\n"
        )
    body = parts + f"--{boundary}--\r\n".encode()
    return client.post(
        "/v2/studies",
        content=body,
        headers={
            "Content-Type": (f"multipart/related; type=application/dicom; boundary={boundary}")
        },
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def perf_client():
    """httpx.Client pointed at the live service; skip if unreachable."""
    if not _service_is_up(BASE_URL):
        pytest.skip(f"Live service not reachable at {BASE_URL}")
    with httpx.Client(base_url=BASE_URL, timeout=60.0) as client:
        yield client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_stow_single_file_baseline(perf_client: httpx.Client) -> None:
    """STOW-RS single DICOM file must complete within SINGLE_FILE_MAX_SECONDS.

    Baseline: < 5 s (generous for CI; typical local result ~0.1-0.5 s).
    """
    dcm_bytes = _build_dicom_bytes(patient_id="PERF-SINGLE-001")

    start = time.perf_counter()
    response = _stow(perf_client, dcm_bytes)
    elapsed = time.perf_counter() - start

    print(f"\n[STOW single] elapsed={elapsed:.3f}s  threshold={SINGLE_FILE_MAX_SECONDS}s")

    assert response.status_code in (
        200,
        202,
    ), f"STOW failed: {response.status_code} {response.text}"
    assert (
        elapsed < SINGLE_FILE_MAX_SECONDS
    ), f"Single-file STOW took {elapsed:.3f}s — exceeds {SINGLE_FILE_MAX_SECONDS}s threshold"


@pytest.mark.performance
def test_stow_10_file_batch_baseline(perf_client: httpx.Client) -> None:
    """STOW-RS 10-file batch must complete within BATCH_10_MAX_SECONDS.

    All 10 instances share the same study so the server can reuse study state.
    Baseline: < 30 s (generous for CI; typical local result ~0.5-2 s).
    """
    study_uid = generate_uid()
    series_uid = generate_uid()
    instances = [
        _build_dicom_bytes(
            patient_id=f"PERF-BATCH-{i:03d}",
            study_uid=study_uid,
            series_uid=series_uid,
        )
        for i in range(10)
    ]

    start = time.perf_counter()
    response = _stow_batch(perf_client, instances)
    elapsed = time.perf_counter() - start

    print(f"\n[STOW 10-batch] elapsed={elapsed:.3f}s  threshold={BATCH_10_MAX_SECONDS}s")

    assert response.status_code in (
        200,
        202,
    ), f"Batch STOW failed: {response.status_code} {response.text}"
    assert (
        elapsed < BATCH_10_MAX_SECONDS
    ), f"10-file batch STOW took {elapsed:.3f}s — exceeds {BATCH_10_MAX_SECONDS}s threshold"
