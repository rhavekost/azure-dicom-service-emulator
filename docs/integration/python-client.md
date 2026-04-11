# Python Client Integration Guide

Two approaches work well against this emulator: raw `httpx` calls (recommended for full control) and `dicomweb-client` (a higher-level DICOMweb library). Both are shown below alongside `pydicom` for creating test DICOM files.

## Prerequisites

- Emulator running: `docker compose up -d` (listens on `http://localhost:8080`)
- Python 3.11+

## Install Dependencies

```bash
pip install pydicom httpx
# or, for the higher-level client:
pip install pydicom httpx dicomweb-client
```

## Approach 1: httpx (Recommended)

`httpx` gives you direct control over multipart bodies, headers, and response parsing, which mirrors how the Azure SDK communicates internally.

### Store a DICOM File (STOW-RS)

```python
import httpx

BASE_URL = "http://localhost:8080"


def store_dicom(dcm_bytes: bytes, base_url: str = BASE_URL) -> dict:
    """Store a DICOM instance via STOW-RS (POST /v2/studies)."""
    boundary = "dicom-boundary"
    body = (
        f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    with httpx.Client() as client:
        response = client.post(
            f"{base_url}/v2/studies",
            content=body,
            headers={
                "Content-Type": (
                    f"multipart/related; type=application/dicom; boundary={boundary}"
                )
            },
        )

    # 200 = all stored, 202 = stored with warnings, 409 = all failed
    response.raise_for_status()
    return response.json()


def upsert_dicom(dcm_bytes: bytes, base_url: str = BASE_URL) -> dict:
    """Upsert a DICOM instance via PUT /v2/studies (no duplicate warnings)."""
    boundary = "dicom-boundary"
    body = (
        f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    with httpx.Client() as client:
        response = client.put(
            f"{base_url}/v2/studies",
            content=body,
            headers={
                "Content-Type": (
                    f"multipart/related; type=application/dicom; boundary={boundary}"
                )
            },
        )
    response.raise_for_status()
    return response.json()
```

### Search Studies (QIDO-RS)

```python
def search_studies(
    params: dict | None = None,
    base_url: str = BASE_URL,
) -> list[dict]:
    """Search studies via QIDO-RS (GET /v2/studies)."""
    with httpx.Client() as client:
        response = client.get(f"{base_url}/v2/studies", params=params or {})
    response.raise_for_status()
    return response.json()


# Examples
all_studies = search_studies()
by_patient = search_studies({"PatientID": "TEST-001"})
wildcard = search_studies({"PatientID": "PAT*"})
fuzzy = search_studies({"PatientName": "smith", "fuzzymatching": "true"})
uid_list = search_studies({"StudyInstanceUID": "1.2.3,4.5.6,7.8.9"})

# Series within a study
def search_series(study_uid: str, base_url: str = BASE_URL) -> list[dict]:
    with httpx.Client() as client:
        response = client.get(f"{base_url}/v2/studies/{study_uid}/series")
    response.raise_for_status()
    return response.json()

# Instances within a series
def search_instances(
    study_uid: str, series_uid: str, base_url: str = BASE_URL
) -> list[dict]:
    with httpx.Client() as client:
        response = client.get(
            f"{base_url}/v2/studies/{study_uid}/series/{series_uid}/instances"
        )
    response.raise_for_status()
    return response.json()
```

### Retrieve Metadata (WADO-RS)

```python
def get_study_metadata(study_uid: str, base_url: str = BASE_URL) -> list[dict]:
    """Get DICOM JSON metadata for all instances in a study."""
    with httpx.Client() as client:
        response = client.get(f"{base_url}/v2/studies/{study_uid}/metadata")
    response.raise_for_status()
    return response.json()  # list of DICOM JSON datasets


def get_instance_bytes(
    study_uid: str,
    series_uid: str,
    instance_uid: str,
    base_url: str = BASE_URL,
) -> bytes:
    """Download a DICOM instance as raw bytes."""
    with httpx.Client() as client:
        response = client.get(
            f"{base_url}/v2/studies/{study_uid}"
            f"/series/{series_uid}/instances/{instance_uid}",
            headers={"Accept": "multipart/related; type=application/dicom"},
        )
    response.raise_for_status()
    # The response is multipart/related — for a single instance, extract first part
    import re
    content_type = response.headers.get("content-type", "")
    match = re.search(r'boundary="?([^";]+)"?', content_type)
    if not match:
        return response.content  # fallback

    boundary = match.group(1).encode()
    parts = response.content.split(b"--" + boundary)
    for part in parts[1:]:
        if b"\r\n\r\n" in part:
            _, body = part.split(b"\r\n\r\n", 1)
            return body.rstrip(b"\r\n--")
    return response.content
```

### Delete

```python
def delete_study(study_uid: str, base_url: str = BASE_URL) -> None:
    """Delete an entire study (204 No Content on success)."""
    with httpx.Client() as client:
        response = client.delete(f"{base_url}/v2/studies/{study_uid}")
    response.raise_for_status()


def delete_series(
    study_uid: str, series_uid: str, base_url: str = BASE_URL
) -> None:
    with httpx.Client() as client:
        response = client.delete(
            f"{base_url}/v2/studies/{study_uid}/series/{series_uid}"
        )
    response.raise_for_status()


def delete_instance(
    study_uid: str, series_uid: str, instance_uid: str, base_url: str = BASE_URL
) -> None:
    with httpx.Client() as client:
        response = client.delete(
            f"{base_url}/v2/studies/{study_uid}"
            f"/series/{series_uid}/instances/{instance_uid}"
        )
    response.raise_for_status()
```

## Approach 2: dicomweb-client

`dicomweb-client` provides a higher-level API that abstracts multipart encoding.

```python
from dicomweb_client import DICOMwebClient

client = DICOMwebClient(url="http://localhost:8080/v2")

# Store
import pydicom
dataset = pydicom.dcmread("path/to/file.dcm")
client.store_instances([dataset])

# Search
studies = client.search_for_studies(search_filters={"PatientID": "TEST-001"})

# Retrieve metadata
instances = client.retrieve_study_metadata(study_instance_uid="1.2.3...")

# Retrieve instance
dicom_ds = client.retrieve_instance(
    study_instance_uid="...",
    series_instance_uid="...",
    sop_instance_uid="...",
)
```

## Creating Test DICOM Files with pydicom

Use this helper to generate minimal valid DICOM instances for testing:

```python
from io import BytesIO
import uuid

import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid


def make_test_dicom(
    patient_id: str = "TEST-001",
    patient_name: str = "Test^Patient",
    modality: str = "CT",
) -> tuple[bytes, str, str, str]:
    """
    Create a minimal valid DICOM file.

    Returns:
        (dcm_bytes, study_uid, series_uid, sop_uid)
    """
    study_uid = generate_uid()
    series_uid = generate_uid()
    sop_uid = generate_uid()

    file_meta = Dataset()
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
    ds.PatientID = patient_id
    ds.PatientName = patient_name
    ds.StudyDate = "20260214"
    ds.StudyTime = "120000"
    ds.Modality = modality
    ds.AccessionNumber = f"ACC-{uuid.uuid4().hex[:8]}"
    ds.StudyDescription = "Test Study"
    ds.SeriesDescription = "Test Series"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1

    buf = BytesIO()
    ds.save_as(buf)
    return buf.getvalue(), study_uid, series_uid, sop_uid
```

## Running Smoke Tests Against the Emulator

The repository includes a ready-made smoke test script:

```bash
# Install dependencies
pip install pydicom httpx

# Start the emulator
docker compose up -d

# Wait for health
until curl -sf http://localhost:8080/health; do sleep 1; done

# Run smoke tests
python smoke_test.py http://localhost:8080
```

The script exercises STOW-RS, QIDO-RS, WADO-RS, Change Feed, and Extended Query Tags end-to-end.

### Writing Your Own pytest Tests

```python
import pytest
import httpx
import os


BASE_URL = os.environ.get("DICOM_SERVICE_URL", "http://localhost:8080")


@pytest.fixture(scope="session")
def dicom_client() -> httpx.Client:
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        yield client


@pytest.fixture(scope="session", autouse=True)
def wait_for_emulator(dicom_client: httpx.Client) -> None:
    """Block until the emulator is healthy."""
    import time
    for _ in range(30):
        try:
            r = dicom_client.get("/health")
            if r.status_code == 200:
                return
        except httpx.ConnectError:
            pass
        time.sleep(1)
    pytest.fail("Emulator did not become healthy within 30 seconds")


@pytest.fixture()
def stored_study(dicom_client: httpx.Client) -> tuple[str, str, str]:
    """Upload a DICOM instance and return (study_uid, series_uid, sop_uid)."""
    from make_test_dicom import make_test_dicom  # your helper above

    dcm_bytes, study_uid, series_uid, sop_uid = make_test_dicom()
    boundary = "test-boundary"
    body = (
        f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode()
        + dcm_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )
    r = dicom_client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"},
    )
    assert r.status_code in (200, 202), r.text

    yield study_uid, series_uid, sop_uid

    # Cleanup
    dicom_client.delete(f"/v2/studies/{study_uid}")


def test_qido_returns_stored_study(
    dicom_client: httpx.Client,
    stored_study: tuple[str, str, str],
) -> None:
    study_uid, _, _ = stored_study
    r = dicom_client.get("/v2/studies", params={"StudyInstanceUID": study_uid})
    assert r.status_code == 200
    studies = r.json()
    assert any(
        s.get("0020000D", {}).get("Value", [None])[0] == study_uid
        for s in studies
    )


def test_wado_metadata(
    dicom_client: httpx.Client,
    stored_study: tuple[str, str, str],
) -> None:
    study_uid, _, _ = stored_study
    r = dicom_client.get(f"/v2/studies/{study_uid}/metadata")
    assert r.status_code == 200
    assert len(r.json()) > 0


def test_delete_study(
    dicom_client: httpx.Client,
    stored_study: tuple[str, str, str],
) -> None:
    study_uid, _, _ = stored_study
    r = dicom_client.delete(f"/v2/studies/{study_uid}")
    assert r.status_code == 204

    # Confirm gone
    r = dicom_client.get(f"/v2/studies/{study_uid}/metadata")
    assert r.status_code == 404
```

### pytest.ini / pyproject.toml Markers

```toml
[tool.pytest.ini_options]
markers = [
    "integration: requires a running emulator",
]
```

Tag your emulator tests:

```python
@pytest.mark.integration
def test_store_and_retrieve(...):
    ...
```

Run only unit tests in CI:

```bash
pytest -m "not integration"
```

Run full suite against the emulator:

```bash
DICOM_SERVICE_URL=http://localhost:8080 pytest -m integration
```

## Async Usage (httpx.AsyncClient)

```python
import httpx
import asyncio


async def async_search(patient_id: str, base_url: str = "http://localhost:8080") -> list:
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        response = await client.get("/v2/studies", params={"PatientID": patient_id})
        response.raise_for_status()
        return response.json()


asyncio.run(async_search("TEST-001"))
```

## Environment Variable Conventions

| Variable | Example value | Notes |
|----------|--------------|-------|
| `DICOM_SERVICE_URL` | `http://localhost:8080` | No trailing slash, no `/v2` suffix (add per-call) |

Switch between emulator and production by changing only this variable — no code changes needed.
