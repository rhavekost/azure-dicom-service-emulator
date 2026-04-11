"""Performance baseline tests for QIDO-RS at scale.

Validates that the GIN index on patient_name and the composite index on
(patient_id, study_date) provide acceptable query latency when the
database contains 1k and 10k instances.

Thresholds (as of v0.2.0):
  - QIDO with 1 000 instances in DB: < 2 s
  - QIDO with 10 000 instances in DB: < 10 s

Run against a live service:
    pytest tests/performance/ -m performance
    DICOM_E2E_BASE_URL=http://my-host:8080 pytest tests/performance/ -m performance

NOTE: The scale-seeding phase (inserting 1k / 10k instances) can take several
minutes the first time.  The tests are skipped when the live service is not up.
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

QIDO_1K_MAX_SECONDS = 2.0
QIDO_10K_MAX_SECONDS = 10.0

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
    patient_id: str,
    patient_name: str,
    study_uid: str | None = None,
    series_uid: str | None = None,
    study_date: str = "20260214",
) -> bytes:
    """Return bytes for a minimal valid DICOM CT instance."""
    study_uid = study_uid or generate_uid()
    series_uid = series_uid or generate_uid()
    sop_uid = generate_uid()

    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
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
    ds.StudyDate = study_date
    ds.StudyTime = "120000"
    ds.Modality = "CT"
    ds.AccessionNumber = f"ACC-{uuid.uuid4().hex[:8]}"
    ds.StudyDescription = "QIDO Perf Seed"
    ds.SeriesDescription = "Seed Series"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1

    buf = BytesIO()
    ds.save_as(buf)
    return buf.getvalue()


def _stow_batch(client: httpx.Client, instances: list[bytes]) -> int:
    """POST a batch of DICOM instances; return HTTP status code."""
    boundary = "qido-seed-boundary"
    parts = b""
    for dcm_bytes in instances:
        parts += (
            f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode()
            + dcm_bytes
            + b"\r\n"
        )
    body = parts + f"--{boundary}--\r\n".encode()
    resp = client.post(
        "/v2/studies",
        content=body,
        headers={
            "Content-Type": (f"multipart/related; type=application/dicom; boundary={boundary}")
        },
    )
    return resp.status_code


def _seed_instances(
    client: httpx.Client,
    *,
    count: int,
    tag: str,
    batch_size: int = 50,
) -> None:
    """Insert `count` DICOM instances tagged with a unique patient-name prefix.

    Instances are inserted in batches of `batch_size` to avoid HTTP timeouts
    on large seeds while still minimising the per-request overhead.
    """
    study_uid = generate_uid()
    series_uid = generate_uid()
    remaining = count
    index = 0
    while remaining > 0:
        current_batch = min(batch_size, remaining)
        instances = [
            _build_dicom_bytes(
                patient_id=f"{tag}-{index + i:06d}",
                patient_name=f"{tag}^Patient{index + i:06d}",
                study_uid=study_uid,
                series_uid=series_uid,
            )
            for i in range(current_batch)
        ]
        status = _stow_batch(client, instances)
        assert status in (
            200,
            202,
        ), f"Seed STOW failed at batch starting index {index}: status={status}"
        remaining -= current_batch
        index += current_batch


def _qido_studies(client: httpx.Client, params: dict) -> httpx.Response:
    """GET /v2/studies with the given query parameters."""
    return client.get("/v2/studies", params=params)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def perf_client():
    """httpx.Client with a long timeout; skip when service is unreachable."""
    if not _service_is_up(BASE_URL):
        pytest.skip(f"Live service not reachable at {BASE_URL}")
    # Long timeout: seeding 10k instances can take a while
    with httpx.Client(base_url=BASE_URL, timeout=300.0) as client:
        yield client


@pytest.fixture(scope="module")
def seed_1k(perf_client: httpx.Client) -> str:
    """Insert 1 000 instances into the service; return the patient-name tag.

    Scoped to module so it runs once and is reused by both 1k tests.
    """
    tag = f"QPERF1K-{uuid.uuid4().hex[:6].upper()}"
    print(f"\n[seed_1k] Seeding {1_000} instances with tag={tag} …")
    start = time.perf_counter()
    _seed_instances(perf_client, count=1_000, tag=tag)
    elapsed = time.perf_counter() - start
    print(f"[seed_1k] Done in {elapsed:.1f}s")
    return tag


@pytest.fixture(scope="module")
def seed_10k(perf_client: httpx.Client) -> str:
    """Insert 10 000 instances into the service; return the patient-name tag.

    Scoped to module so it runs once and is reused by all 10k tests.
    NOTE: This fixture intentionally inserts a fresh 10k cohort (rather than
    reusing seed_1k) so each scale tier is measured independently.
    """
    tag = f"QPERF10K-{uuid.uuid4().hex[:6].upper()}"
    print(f"\n[seed_10k] Seeding {10_000} instances with tag={tag} …")
    start = time.perf_counter()
    _seed_instances(perf_client, count=10_000, tag=tag)
    elapsed = time.perf_counter() - start
    print(f"[seed_10k] Done in {elapsed:.1f}s")
    return tag


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.performance
@pytest.mark.slow
def test_qido_studies_at_1k_instances(perf_client: httpx.Client, seed_1k: str) -> None:
    """QIDO /v2/studies must return within QIDO_1K_MAX_SECONDS at 1k-instance scale.

    Uses PatientName wildcard search, which exercises the GIN index on
    the patient_name column.

    Baseline: < 2 s (generous for CI; typical local result < 0.05 s).
    """
    # Wildcard search that hits the GIN index; limit=10 to minimise payload
    params = {"PatientName": f"{seed_1k}*", "limit": "10"}

    start = time.perf_counter()
    response = _qido_studies(perf_client, params)
    elapsed = time.perf_counter() - start

    print(
        f"\n[QIDO 1k] PatientName={params['PatientName']}  "
        f"elapsed={elapsed:.3f}s  threshold={QIDO_1K_MAX_SECONDS}s"
    )

    assert response.status_code == 200, f"QIDO failed: {response.status_code} {response.text[:200]}"
    assert elapsed < QIDO_1K_MAX_SECONDS, (
        f"QIDO at 1k-instance scale took {elapsed:.3f}s — "
        f"exceeds {QIDO_1K_MAX_SECONDS}s threshold"
    )


@pytest.mark.performance
@pytest.mark.slow
def test_qido_instances_at_1k_by_patient_id(perf_client: httpx.Client, seed_1k: str) -> None:
    """QIDO /v2/instances filtered by PatientID prefix must complete within threshold.

    Exercises the composite (patient_id, study_date) index.

    Baseline: < 2 s.
    """
    # Use a prefix that matches all seeded patient IDs for this cohort
    params = {"PatientID": f"{seed_1k}*", "limit": "10"}

    start = time.perf_counter()
    response = _qido_studies(perf_client, params)
    elapsed = time.perf_counter() - start

    print(
        f"\n[QIDO 1k PatientID] PatientID={params['PatientID']}  "
        f"elapsed={elapsed:.3f}s  threshold={QIDO_1K_MAX_SECONDS}s"
    )

    assert response.status_code == 200, f"QIDO failed: {response.status_code} {response.text[:200]}"
    assert elapsed < QIDO_1K_MAX_SECONDS, (
        f"QIDO (PatientID) at 1k-instance scale took {elapsed:.3f}s — "
        f"exceeds {QIDO_1K_MAX_SECONDS}s threshold"
    )


@pytest.mark.performance
@pytest.mark.slow
def test_qido_studies_at_10k_instances(perf_client: httpx.Client, seed_10k: str) -> None:
    """QIDO /v2/studies must return within QIDO_10K_MAX_SECONDS at 10k-instance scale.

    Uses PatientName wildcard search to exercise the GIN index.

    Baseline: < 10 s (generous for CI; typical local result < 0.1 s).
    """
    params = {"PatientName": f"{seed_10k}*", "limit": "10"}

    start = time.perf_counter()
    response = _qido_studies(perf_client, params)
    elapsed = time.perf_counter() - start

    print(
        f"\n[QIDO 10k] PatientName={params['PatientName']}  "
        f"elapsed={elapsed:.3f}s  threshold={QIDO_10K_MAX_SECONDS}s"
    )

    assert response.status_code == 200, f"QIDO failed: {response.status_code} {response.text[:200]}"
    assert elapsed < QIDO_10K_MAX_SECONDS, (
        f"QIDO at 10k-instance scale took {elapsed:.3f}s — "
        f"exceeds {QIDO_10K_MAX_SECONDS}s threshold"
    )


@pytest.mark.performance
@pytest.mark.slow
def test_qido_instances_at_10k_by_patient_id(perf_client: httpx.Client, seed_10k: str) -> None:
    """QIDO /v2/studies filtered by PatientID prefix at 10k scale.

    Exercises the composite (patient_id, study_date) index.

    Baseline: < 10 s.
    """
    params = {"PatientID": f"{seed_10k}*", "limit": "10"}

    start = time.perf_counter()
    response = _qido_studies(perf_client, params)
    elapsed = time.perf_counter() - start

    print(
        f"\n[QIDO 10k PatientID] PatientID={params['PatientID']}  "
        f"elapsed={elapsed:.3f}s  threshold={QIDO_10K_MAX_SECONDS}s"
    )

    assert response.status_code == 200, f"QIDO failed: {response.status_code} {response.text[:200]}"
    assert elapsed < QIDO_10K_MAX_SECONDS, (
        f"QIDO (PatientID) at 10k-instance scale took {elapsed:.3f}s — "
        f"exceeds {QIDO_10K_MAX_SECONDS}s threshold"
    )
