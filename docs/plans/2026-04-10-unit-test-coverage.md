# Unit Test Coverage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Raise unit test coverage from 53.96% to ≥85%, passing the `.coveragerc` gate, by adding router and service unit tests using FastAPI's synchronous `TestClient` with an in-memory SQLite database.

**Architecture:** The existing `client` fixture in `tests/conftest.py` already creates a fresh SQLite-backed FastAPI test app per test — new router tests simply declare it as a parameter. A `stored_instance` fixture in `tests/unit/routers/conftest.py` pre-stores a CT DICOM and yields UIDs for reuse. Service gap tests are pure function calls with no HTTP or DB.

**Tech Stack:** pytest, FastAPI `TestClient` (synchronous), `sqlite+aiosqlite`, `DicomFactory` from `tests/fixtures/factories.py`, `pytest-asyncio`

---

## Key Conventions

- Tests use `def test_...` (synchronous) — `TestClient` handles async routes internally
- Multipart body helper (defined once in `tests/unit/routers/conftest.py`):
  ```python
  def make_multipart(dicom_bytes: bytes, boundary: str = "test_boundary") -> bytes:
      return (
          f"--{boundary}\r\n"
          f"Content-Type: application/dicom\r\n"
          f"\r\n"
      ).encode() + dicom_bytes + f"\r\n--{boundary}--\r\n".encode()
  ```
- Multipart Content-Type header: `"multipart/related; type=application/dicom; boundary=test_boundary"`
- All endpoints are prefixed `/v2/` (routers registered with `prefix="/v2"`)
- The `client` fixture from `tests/conftest.py` is automatically available to all tests under `tests/`

---

## Task 1: Router conftest.py with shared fixtures

**Files:**
- Create: `tests/unit/routers/conftest.py`

**Step 1: Write the file**

```python
"""Shared fixtures for router unit tests."""

import pytest
from pydicom.uid import generate_uid
from tests.fixtures.factories import DicomFactory


BOUNDARY = "test_boundary"
CONTENT_TYPE = f"multipart/related; type=application/dicom; boundary={BOUNDARY}"


def make_multipart(dicom_bytes: bytes, boundary: str = BOUNDARY) -> bytes:
    """Wrap DICOM bytes in a minimal multipart/related body."""
    return (
        f"--{boundary}\r\n"
        f"Content-Type: application/dicom\r\n"
        f"\r\n"
    ).encode() + dicom_bytes + f"\r\n--{boundary}--\r\n".encode()


@pytest.fixture
def stored_instance(client, tmp_path, monkeypatch):
    """Store a CT DICOM instance and return its UIDs.

    Yields a dict with study_uid, series_uid, sop_uid.
    Uses the existing `client` fixture from tests/conftest.py.
    """
    import app.services.dicom_engine as dicom_engine
    storage_dir = tmp_path / "dicom_storage"
    storage_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("DICOM_STORAGE_DIR", str(storage_dir))

    study_uid = generate_uid()
    series_uid = generate_uid()
    sop_uid = generate_uid()

    dicom_bytes = DicomFactory.create_ct_image(
        patient_id="STORE-001",
        patient_name="Store^Test",
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        with_pixel_data=True,
    )

    response = client.post(
        "/v2/studies",
        content=make_multipart(dicom_bytes),
        headers={"Content-Type": CONTENT_TYPE},
    )
    assert response.status_code in (200, 202), f"Setup failed: {response.text}"

    yield {"study_uid": study_uid, "series_uid": series_uid, "sop_uid": sop_uid}
```

**Step 2: Verify it imports cleanly**

```bash
cd /Users/rhavekost/Projects/rhavekost/azure-dicom-service-emulator
python -c "import tests.unit.routers.conftest" && echo "OK"
```
Expected: `OK`

**Step 3: Commit**

```bash
git add tests/unit/routers/conftest.py
git commit -m "test: add router conftest with stored_instance fixture and multipart helper"
```

---

## Task 2: STOW-RS tests

**Files:**
- Create: `tests/unit/routers/test_dicomweb_stow.py`

**Step 1: Write the tests**

```python
"""Unit tests for STOW-RS (store) endpoints."""

import pytest
from pydicom.uid import generate_uid
from tests.fixtures.factories import DicomFactory
from tests.unit.routers.conftest import CONTENT_TYPE, make_multipart

pytestmark = pytest.mark.unit


def test_store_single_instance_returns_200(client, tmp_path, monkeypatch):
    import app.services.dicom_engine as dicom_engine
    storage_dir = tmp_path / "dicom_storage"
    storage_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))

    dicom_bytes = DicomFactory.create_ct_image()
    response = client.post(
        "/v2/studies",
        content=make_multipart(dicom_bytes),
        headers={"Content-Type": CONTENT_TYPE},
    )
    assert response.status_code == 200


def test_store_duplicate_returns_409(client, tmp_path, monkeypatch):
    import app.services.dicom_engine as dicom_engine
    storage_dir = tmp_path / "dicom_storage"
    storage_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))

    sop_uid = generate_uid()
    dicom_bytes = DicomFactory.create_ct_image(sop_uid=sop_uid)

    client.post("/v2/studies", content=make_multipart(dicom_bytes), headers={"Content-Type": CONTENT_TYPE})
    response = client.post("/v2/studies", content=make_multipart(dicom_bytes), headers={"Content-Type": CONTENT_TYPE})
    assert response.status_code == 409


def test_store_missing_required_uid_returns_409(client, tmp_path, monkeypatch):
    import app.services.dicom_engine as dicom_engine
    storage_dir = tmp_path / "dicom_storage"
    storage_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))

    invalid_bytes = DicomFactory.create_invalid_dicom(missing_tags=["SOPInstanceUID"])
    response = client.post(
        "/v2/studies",
        content=make_multipart(invalid_bytes),
        headers={"Content-Type": CONTENT_TYPE},
    )
    assert response.status_code == 409


def test_store_missing_content_type_returns_400(client):
    response = client.post("/v2/studies", content=b"not multipart")
    assert response.status_code in (400, 422)


def test_store_multiple_instances_all_succeed(client, tmp_path, monkeypatch):
    import app.services.dicom_engine as dicom_engine
    storage_dir = tmp_path / "dicom_storage"
    storage_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))

    study_uid = generate_uid()
    series_uid = generate_uid()
    dicom1 = DicomFactory.create_ct_image(study_uid=study_uid, series_uid=series_uid)
    dicom2 = DicomFactory.create_ct_image(study_uid=study_uid, series_uid=series_uid)

    boundary = "multi_boundary"
    body = (
        f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n"
    ).encode() + dicom1 + (
        f"\r\n--{boundary}\r\nContent-Type: application/dicom\r\n\r\n"
    ).encode() + dicom2 + f"\r\n--{boundary}--\r\n".encode()

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}"},
    )
    assert response.status_code == 200


def test_store_to_study_endpoint(client, tmp_path, monkeypatch):
    import app.services.dicom_engine as dicom_engine
    storage_dir = tmp_path / "dicom_storage"
    storage_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))

    study_uid = generate_uid()
    dicom_bytes = DicomFactory.create_ct_image(study_uid=study_uid)
    response = client.post(
        f"/v2/studies/{study_uid}",
        content=make_multipart(dicom_bytes),
        headers={"Content-Type": CONTENT_TYPE},
    )
    assert response.status_code == 200


def test_store_searchable_attr_warning_returns_202(client, tmp_path, monkeypatch):
    import app.services.dicom_engine as dicom_engine
    storage_dir = tmp_path / "dicom_storage"
    storage_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))

    # Missing PatientID triggers a searchable attribute warning (202)
    dicom_bytes = DicomFactory.create_dicom_with_attrs(patient_id=None)
    response = client.post(
        "/v2/studies",
        content=make_multipart(dicom_bytes),
        headers={"Content-Type": CONTENT_TYPE},
    )
    assert response.status_code in (200, 202)
```

**Step 2: Run and confirm pass**

```bash
python -m pytest tests/unit/routers/test_dicomweb_stow.py -v --no-cov
```
Expected: All tests PASS.

**Step 3: Commit**

```bash
git add tests/unit/routers/test_dicomweb_stow.py
git commit -m "test: add STOW-RS unit tests"
```

---

## Task 3: WADO-RS retrieve tests

**Files:**
- Create: `tests/unit/routers/test_dicomweb_wado.py`

**Step 1: Write the tests**

```python
"""Unit tests for WADO-RS (retrieve) endpoints."""

import pytest
from tests.unit.routers.conftest import stored_instance  # noqa: F401

pytestmark = pytest.mark.unit


def test_retrieve_study_returns_multipart(client, stored_instance):
    uid = stored_instance["study_uid"]
    response = client.get(f"/v2/studies/{uid}")
    assert response.status_code == 200
    assert "multipart/related" in response.headers["content-type"]


def test_retrieve_series_returns_multipart(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    response = client.get(f"/v2/studies/{study}/series/{series}")
    assert response.status_code == 200
    assert "multipart/related" in response.headers["content-type"]


def test_retrieve_instance_returns_multipart(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    sop = stored_instance["sop_uid"]
    response = client.get(f"/v2/studies/{study}/series/{series}/instances/{sop}")
    assert response.status_code == 200
    assert "multipart/related" in response.headers["content-type"]


def test_retrieve_nonexistent_study_returns_404(client):
    response = client.get("/v2/studies/1.2.3.4.5.6.7.8.9.notreal")
    assert response.status_code == 404


def test_retrieve_study_metadata(client, stored_instance):
    uid = stored_instance["study_uid"]
    response = client.get(
        f"/v2/studies/{uid}/metadata",
        headers={"Accept": "application/dicom+json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0


def test_retrieve_series_metadata(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    response = client.get(
        f"/v2/studies/{study}/series/{series}/metadata",
        headers={"Accept": "application/dicom+json"},
    )
    assert response.status_code == 200


def test_retrieve_instance_metadata(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    sop = stored_instance["sop_uid"]
    response = client.get(
        f"/v2/studies/{study}/series/{series}/instances/{sop}/metadata",
        headers={"Accept": "application/dicom+json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)


def test_retrieve_frames(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    sop = stored_instance["sop_uid"]
    response = client.get(f"/v2/studies/{study}/series/{series}/instances/{sop}/frames/1")
    assert response.status_code == 200


def test_retrieve_frames_out_of_range_returns_404(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    sop = stored_instance["sop_uid"]
    response = client.get(f"/v2/studies/{study}/series/{series}/instances/{sop}/frames/999")
    assert response.status_code == 404


def test_rendered_instance_jpeg(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    sop = stored_instance["sop_uid"]
    response = client.get(
        f"/v2/studies/{study}/series/{series}/instances/{sop}/rendered",
        headers={"Accept": "image/jpeg"},
    )
    assert response.status_code in (200, 404)  # 404 if no pixel data path


def test_invalid_accept_returns_406(client, stored_instance):
    study = stored_instance["study_uid"]
    response = client.get(
        f"/v2/studies/{study}",
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 406
```

**Step 2: Run and confirm pass**

```bash
python -m pytest tests/unit/routers/test_dicomweb_wado.py -v --no-cov
```
Expected: All tests PASS.

**Step 3: Commit**

```bash
git add tests/unit/routers/test_dicomweb_wado.py
git commit -m "test: add WADO-RS retrieve unit tests"
```

---

## Task 4: QIDO-RS search tests

**Files:**
- Create: `tests/unit/routers/test_dicomweb_qido.py`

**Step 1: Write the tests**

```python
"""Unit tests for QIDO-RS (search) endpoints and helper functions."""

import pytest
from tests.fixtures.factories import DicomFactory
from tests.unit.routers.conftest import CONTENT_TYPE, make_multipart, stored_instance  # noqa: F401
from app.routers.dicomweb import filter_dicom_json_by_includefield

pytestmark = pytest.mark.unit


def test_search_studies_empty(client):
    response = client.get("/v2/studies")
    assert response.status_code == 200
    assert response.json() == []


def test_search_studies_returns_result(client, stored_instance):
    response = client.get("/v2/studies")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1


def test_search_by_patient_id(client, stored_instance):
    response = client.get("/v2/studies?PatientID=STORE-001")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1


def test_search_by_patient_id_no_match(client, stored_instance):
    response = client.get("/v2/studies?PatientID=DOESNOTEXIST")
    assert response.status_code == 200
    assert response.json() == []


def test_search_by_study_uid(client, stored_instance):
    uid = stored_instance["study_uid"]
    response = client.get(f"/v2/studies?StudyInstanceUID={uid}")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1


def test_search_series_under_study(client, stored_instance):
    study = stored_instance["study_uid"]
    response = client.get(f"/v2/studies/{study}/series")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1


def test_search_instances_under_series(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    response = client.get(f"/v2/studies/{study}/series/{series}/instances")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1


def test_search_with_limit(client, stored_instance, tmp_path, monkeypatch):
    import app.services.dicom_engine as dicom_engine
    storage_dir = tmp_path / "dicom_storage"
    storage_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(dicom_engine, "STORAGE_DIR", str(storage_dir))

    # Store a second instance to test limit
    from pydicom.uid import generate_uid
    dicom2 = DicomFactory.create_ct_image(study_uid=generate_uid())
    client.post("/v2/studies", content=make_multipart(dicom2), headers={"Content-Type": CONTENT_TYPE})

    response = client.get("/v2/studies?limit=1")
    assert response.status_code == 200
    assert len(response.json()) <= 1


def test_search_with_offset(client, stored_instance):
    response = client.get("/v2/studies?offset=999")
    assert response.status_code == 200
    assert response.json() == []


# Pure function tests — no HTTP needed

def test_filter_dicom_json_by_includefield_all():
    dicom_json = {"00100020": {"vr": "LO", "Value": ["P1"]}, "00080060": {"vr": "CS", "Value": ["CT"]}}
    result = filter_dicom_json_by_includefield(dicom_json, "all")
    assert result == dicom_json


def test_filter_dicom_json_by_includefield_none():
    dicom_json = {"00100020": {"vr": "LO", "Value": ["P1"]}}
    result = filter_dicom_json_by_includefield(dicom_json, None)
    assert isinstance(result, dict)


def test_filter_dicom_json_by_includefield_specific():
    dicom_json = {
        "00100020": {"vr": "LO", "Value": ["P1"]},
        "00080060": {"vr": "CS", "Value": ["CT"]},
        "00201208": {"vr": "IS", "Value": [1]},
    }
    result = filter_dicom_json_by_includefield(dicom_json, "00100020")
    assert "00100020" in result
```

**Step 2: Run and confirm pass**

```bash
python -m pytest tests/unit/routers/test_dicomweb_qido.py -v --no-cov
```
Expected: All tests PASS.

**Step 3: Commit**

```bash
git add tests/unit/routers/test_dicomweb_qido.py
git commit -m "test: add QIDO-RS search unit tests"
```

---

## Task 5: DELETE tests

**Files:**
- Create: `tests/unit/routers/test_dicomweb_delete.py`

**Step 1: Write the tests**

```python
"""Unit tests for DELETE endpoints."""

import pytest
from tests.unit.routers.conftest import stored_instance  # noqa: F401

pytestmark = pytest.mark.unit


def test_delete_study(client, stored_instance):
    uid = stored_instance["study_uid"]
    response = client.delete(f"/v2/studies/{uid}")
    assert response.status_code == 204


def test_delete_study_twice_returns_404(client, stored_instance):
    uid = stored_instance["study_uid"]
    client.delete(f"/v2/studies/{uid}")
    response = client.delete(f"/v2/studies/{uid}")
    assert response.status_code == 404


def test_delete_series(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    response = client.delete(f"/v2/studies/{study}/series/{series}")
    assert response.status_code == 204


def test_delete_instance(client, stored_instance):
    study = stored_instance["study_uid"]
    series = stored_instance["series_uid"]
    sop = stored_instance["sop_uid"]
    response = client.delete(f"/v2/studies/{study}/series/{series}/instances/{sop}")
    assert response.status_code == 204


def test_delete_nonexistent_study_returns_404(client):
    response = client.delete("/v2/studies/1.2.3.4.5.notreal")
    assert response.status_code == 404


def test_delete_creates_changefeed_entry(client, stored_instance):
    uid = stored_instance["study_uid"]
    client.delete(f"/v2/studies/{uid}")
    # Verify changefeed has a delete entry
    cf_response = client.get("/v2/changefeed")
    assert cf_response.status_code == 200
    entries = cf_response.json()
    actions = [e["Action"] for e in entries]
    assert "Delete" in actions or "delete" in actions or any("delet" in a.lower() for a in actions)
```

**Step 2: Run and confirm pass**

```bash
python -m pytest tests/unit/routers/test_dicomweb_delete.py -v --no-cov
```
Expected: All tests PASS.

**Step 3: Commit**

```bash
git add tests/unit/routers/test_dicomweb_delete.py
git commit -m "test: add DELETE endpoint unit tests"
```

---

## Task 6: Change Feed tests

**Files:**
- Create: `tests/unit/routers/test_changefeed.py`

**Step 1: Write the tests**

```python
"""Unit tests for Change Feed API."""

import pytest
from tests.unit.routers.conftest import stored_instance  # noqa: F401

pytestmark = pytest.mark.unit


def test_changefeed_empty(client):
    response = client.get("/v2/changefeed")
    assert response.status_code == 200
    assert response.json() == []


def test_changefeed_latest_empty(client):
    response = client.get("/v2/changefeed/latest")
    assert response.status_code == 200
    assert response.json() == {"Sequence": 0}


def test_changefeed_after_store_has_entry(client, stored_instance):
    response = client.get("/v2/changefeed")
    assert response.status_code == 200
    entries = response.json()
    assert len(entries) >= 1
    entry = entries[0]
    assert "Sequence" in entry
    assert "StudyInstanceUID" in entry
    assert "Action" in entry
    assert "State" in entry


def test_changefeed_latest_after_store(client, stored_instance):
    response = client.get("/v2/changefeed/latest")
    assert response.status_code == 200
    data = response.json()
    assert data["Sequence"] >= 1
    assert "StudyInstanceUID" in data


def test_changefeed_starttime_filter(client, stored_instance):
    # Start time far in the future — should return empty
    response = client.get("/v2/changefeed?startTime=2099-01-01T00:00:00Z")
    assert response.status_code == 200
    assert response.json() == []


def test_changefeed_endtime_filter(client, stored_instance):
    # End time far in the past — should return empty
    response = client.get("/v2/changefeed?endTime=2000-01-01T00:00:00Z")
    assert response.status_code == 200
    assert response.json() == []


def test_changefeed_pagination_offset(client, stored_instance):
    response = client.get("/v2/changefeed?offset=999")
    assert response.status_code == 200
    assert response.json() == []


def test_changefeed_pagination_limit(client, stored_instance):
    response = client.get("/v2/changefeed?limit=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) <= 1
```

**Step 2: Run and confirm pass**

```bash
python -m pytest tests/unit/routers/test_changefeed.py -v --no-cov
```
Expected: All tests PASS.

**Step 3: Commit**

```bash
git add tests/unit/routers/test_changefeed.py
git commit -m "test: add Change Feed unit tests"
```

---

## Task 7: Extended Query Tags tests

**Files:**
- Create: `tests/unit/routers/test_extended_query_tags.py`

**Step 1: Write the tests**

```python
"""Unit tests for Extended Query Tags API."""

import pytest

pytestmark = pytest.mark.unit

TAG_PAYLOAD = {"tags": [{"path": "00101010", "vr": "AS", "level": "Study"}]}


def test_list_tags_empty(client):
    response = client.get("/v2/extendedquerytags")
    assert response.status_code == 200
    assert response.json() == []


def test_add_tag_returns_202(client):
    response = client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "succeeded"
    assert "id" in data


def test_add_tag_creates_entry(client):
    client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    response = client.get("/v2/extendedquerytags")
    assert response.status_code == 200
    tags = response.json()
    assert len(tags) == 1
    assert tags[0]["Path"] == "00101010"
    assert tags[0]["Status"] == "Ready"
    assert tags[0]["QueryStatus"] == "Enabled"


def test_add_duplicate_tag_returns_409(client):
    client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    response = client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    assert response.status_code == 409


def test_get_tag_by_path(client):
    client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    response = client.get("/v2/extendedquerytags/00101010")
    assert response.status_code == 200
    data = response.json()
    assert data["Path"] == "00101010"
    assert data["VR"] == "AS"


def test_get_tag_not_found(client):
    response = client.get("/v2/extendedquerytags/FFFFFFFF")
    assert response.status_code == 404


def test_delete_tag_returns_204(client):
    client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    response = client.delete("/v2/extendedquerytags/00101010")
    assert response.status_code == 204


def test_delete_tag_removes_from_list(client):
    client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    client.delete("/v2/extendedquerytags/00101010")
    response = client.get("/v2/extendedquerytags")
    assert response.json() == []


def test_delete_tag_not_found_returns_404(client):
    response = client.delete("/v2/extendedquerytags/FFFFFFFF")
    assert response.status_code == 404
```

**Step 2: Run and confirm pass**

```bash
python -m pytest tests/unit/routers/test_extended_query_tags.py -v --no-cov
```
Expected: All tests PASS.

**Step 3: Commit**

```bash
git add tests/unit/routers/test_extended_query_tags.py
git commit -m "test: add Extended Query Tags unit tests"
```

---

## Task 8: Operations tests

**Files:**
- Create: `tests/unit/routers/test_operations.py`

**Step 1: Write the tests**

```python
"""Unit tests for Operations API."""

import uuid
import pytest

pytestmark = pytest.mark.unit

TAG_PAYLOAD = {"tags": [{"path": "00101020", "vr": "DS", "level": "Study"}]}


def test_get_operation_invalid_uuid(client):
    response = client.get("/v2/operations/not-a-uuid")
    assert response.status_code == 400


def test_get_operation_not_found(client):
    random_uuid = str(uuid.uuid4())
    response = client.get(f"/v2/operations/{random_uuid}")
    assert response.status_code == 404


def test_get_operation_valid(client):
    # Create an operation by adding an extended query tag (returns operation ID)
    post_response = client.post("/v2/extendedquerytags", json=TAG_PAYLOAD)
    assert post_response.status_code == 202
    operation_id = post_response.json()["id"]

    response = client.get(f"/v2/operations/{operation_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == operation_id
    assert data["status"] == "succeeded"
    assert "createdTime" in data
    assert "lastUpdatedTime" in data
```

**Step 2: Run and confirm pass**

```bash
python -m pytest tests/unit/routers/test_operations.py -v --no-cov
```
Expected: All tests PASS.

**Step 3: Commit**

```bash
git add tests/unit/routers/test_operations.py
git commit -m "test: add Operations unit tests"
```

---

## Task 9: Debug endpoint tests

**Files:**
- Create: `tests/unit/routers/test_debug.py`

**Step 1: Write the tests**

```python
"""Unit tests for debug endpoints."""

import pytest

pytestmark = pytest.mark.unit


def test_get_debug_events_no_provider_returns_404(client):
    """Debug endpoint returns 404 when no InMemoryEventProvider is configured."""
    response = client.get("/debug/events")
    assert response.status_code == 404


def test_delete_debug_events_no_provider_returns_404(client):
    """Clear debug events returns 404 when no InMemoryEventProvider is configured."""
    response = client.delete("/debug/events")
    assert response.status_code == 404
```

**Step 2: Run and confirm pass**

```bash
python -m pytest tests/unit/routers/test_debug.py -v --no-cov
```
Expected: Both tests PASS.

**Step 3: Commit**

```bash
git add tests/unit/routers/test_debug.py
git commit -m "test: add debug endpoint unit tests"
```

---

## Task 10: UPS-RS workitem tests

**Files:**
- Create: `tests/unit/routers/test_ups.py`

**Step 1: Write the tests**

```python
"""Unit tests for UPS-RS (Unified Procedure Step) endpoints."""

import pytest
from pydicom.uid import generate_uid

pytestmark = pytest.mark.unit

# Minimal valid workitem payload (DICOM JSON format)
def make_workitem(uid: str | None = None, state: str = "SCHEDULED") -> dict:
    uid = uid or generate_uid()
    payload = {
        "00080018": {"vr": "UI", "Value": [uid]},   # SOP Instance UID
        "00741000": {"vr": "CS", "Value": [state]},  # Procedure Step State
        "00100020": {"vr": "LO", "Value": ["PAT-UPS-001"]},  # Patient ID
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "UPS^Test"}]},  # Patient Name
    }
    return uid, payload


def test_create_workitem_returns_201(client):
    uid, payload = make_workitem()
    response = client.post("/v2/workitems", json=payload)
    assert response.status_code == 201


def test_create_workitem_sets_location_header(client):
    uid, payload = make_workitem()
    response = client.post("/v2/workitems", json=payload)
    assert response.status_code == 201
    assert "Location" in response.headers


def test_create_workitem_duplicate_returns_409(client):
    uid, payload = make_workitem()
    client.post("/v2/workitems", json=payload)
    response = client.post("/v2/workitems", json=payload)
    assert response.status_code == 409


def test_create_workitem_invalid_state_returns_400(client):
    uid, payload = make_workitem(state="IN PROGRESS")
    response = client.post("/v2/workitems", json=payload)
    assert response.status_code == 400


def test_create_workitem_with_transaction_uid_returns_400(client):
    uid, payload = make_workitem()
    payload["00081195"] = {"vr": "UI", "Value": [generate_uid()]}  # Transaction UID
    response = client.post("/v2/workitems", json=payload)
    assert response.status_code == 400


def test_search_workitems_empty(client):
    response = client.get("/v2/workitems")
    assert response.status_code == 200
    assert response.json() == []


def test_search_workitems_returns_result(client):
    uid, payload = make_workitem()
    client.post("/v2/workitems", json=payload)
    response = client.get("/v2/workitems")
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_search_by_patient_id(client):
    uid, payload = make_workitem()
    client.post("/v2/workitems", json=payload)
    response = client.get("/v2/workitems?PatientID=PAT-UPS-001")
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_search_by_patient_id_no_match(client):
    uid, payload = make_workitem()
    client.post("/v2/workitems", json=payload)
    response = client.get("/v2/workitems?PatientID=NOBODY")
    assert response.status_code == 200
    assert response.json() == []


def test_search_by_state(client):
    uid, payload = make_workitem()
    client.post("/v2/workitems", json=payload)
    response = client.get("/v2/workitems?ProcedureStepState=SCHEDULED")
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_retrieve_workitem(client):
    uid, payload = make_workitem()
    client.post("/v2/workitems", json=payload)
    response = client.get(f"/v2/workitems/{uid}")
    assert response.status_code == 200


def test_retrieve_workitem_not_found(client):
    response = client.get(f"/v2/workitems/{generate_uid()}")
    assert response.status_code == 404


def test_cancel_workitem(client):
    uid, payload = make_workitem()
    client.post("/v2/workitems", json=payload)
    cancel_payload = {"00741000": {"vr": "CS", "Value": ["CANCELED"]}}
    response = client.post(f"/v2/workitems/{uid}/cancelrequest", json=cancel_payload)
    assert response.status_code in (200, 202, 204)


def test_subscription_endpoints_return_501(client):
    uid = generate_uid()
    response = client.post(f"/v2/workitems/{uid}/subscribers/test")
    assert response.status_code == 501
```

**Step 2: Run and confirm pass**

```bash
python -m pytest tests/unit/routers/test_ups.py -v --no-cov
```
Expected: All tests PASS (or note any that need adjustment based on actual route behavior).

**Step 3: Commit**

```bash
git add tests/unit/routers/test_ups.py
git commit -m "test: add UPS-RS workitem unit tests"
```

---

## Task 11: Accept validation tests (services)

**Files:**
- Create: `tests/unit/services/test_accept_validation.py`

**Step 1: Write the tests**

```python
"""Unit tests for Accept header validation."""

import pytest
from app.services.accept_validation import validate_accept_for_rendered, validate_accept_for_retrieve

pytestmark = pytest.mark.unit


# --- validate_accept_for_retrieve ---

def test_retrieve_accepts_none():
    assert validate_accept_for_retrieve(None) is True

def test_retrieve_accepts_empty_string():
    assert validate_accept_for_retrieve("") is True

def test_retrieve_accepts_multipart_related():
    assert validate_accept_for_retrieve("multipart/related") is True

def test_retrieve_accepts_multipart_related_with_type():
    assert validate_accept_for_retrieve("multipart/related; type=application/dicom") is True

def test_retrieve_accepts_application_dicom():
    assert validate_accept_for_retrieve("application/dicom") is True

def test_retrieve_accepts_wildcard():
    assert validate_accept_for_retrieve("*/*") is True

def test_retrieve_accepts_case_insensitive():
    assert validate_accept_for_retrieve("MULTIPART/RELATED") is True

def test_retrieve_rejects_text_plain():
    assert validate_accept_for_retrieve("text/plain") is False

def test_retrieve_rejects_image_jpeg():
    assert validate_accept_for_retrieve("image/jpeg") is False

def test_retrieve_rejects_text_html():
    assert validate_accept_for_retrieve("text/html") is False


# --- validate_accept_for_rendered ---

def test_rendered_accepts_none():
    assert validate_accept_for_rendered(None) is True

def test_rendered_accepts_empty_string():
    assert validate_accept_for_rendered("") is True

def test_rendered_accepts_jpeg():
    assert validate_accept_for_rendered("image/jpeg") is True

def test_rendered_accepts_png():
    assert validate_accept_for_rendered("image/png") is True

def test_rendered_accepts_wildcard():
    assert validate_accept_for_rendered("*/*") is True

def test_rendered_accepts_case_insensitive():
    assert validate_accept_for_rendered("IMAGE/JPEG") is True

def test_rendered_rejects_application_dicom():
    assert validate_accept_for_rendered("application/dicom") is False

def test_rendered_rejects_text_plain():
    assert validate_accept_for_rendered("text/plain") is False

def test_rendered_rejects_multipart():
    assert validate_accept_for_rendered("multipart/related") is False
```

**Step 2: Run and confirm pass**

```bash
python -m pytest tests/unit/services/test_accept_validation.py -v --no-cov
```
Expected: All 20 tests PASS.

**Step 3: Commit**

```bash
git add tests/unit/services/test_accept_validation.py
git commit -m "test: add accept header validation unit tests"
```

---

## Task 12: Event config gap tests

**Files:**
- Create: `tests/unit/services/test_event_config_gaps.py`

**Step 1: Write the tests**

```python
"""Unit tests for event provider config loading (gap coverage)."""

import pytest
from unittest.mock import patch
from app.services.events.config import load_providers_from_config
from app.services.events.providers import InMemoryEventProvider, FileEventProvider

pytestmark = pytest.mark.unit


def test_no_env_var_returns_empty(monkeypatch):
    monkeypatch.delenv("EVENT_PROVIDERS", raising=False)
    result = load_providers_from_config()
    assert result == []


def test_malformed_json_returns_empty(monkeypatch):
    monkeypatch.setenv("EVENT_PROVIDERS", "not valid json {{")
    result = load_providers_from_config()
    assert result == []


def test_disabled_provider_is_skipped(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '[{"type": "in_memory", "enabled": false}]',
    )
    result = load_providers_from_config()
    assert result == []


def test_enabled_in_memory_provider_loaded(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '[{"type": "in_memory", "enabled": true}]',
    )
    result = load_providers_from_config()
    assert len(result) == 1
    assert isinstance(result[0], InMemoryEventProvider)


def test_object_format_with_providers_key(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '{"providers": [{"type": "in_memory", "enabled": true}]}',
    )
    result = load_providers_from_config()
    assert len(result) == 1


def test_unknown_provider_type_returns_empty(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '[{"type": "unknown_provider", "enabled": true}]',
    )
    result = load_providers_from_config()
    assert result == []


def test_file_provider_missing_path_returns_empty(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '[{"type": "file", "enabled": true}]',
    )
    result = load_providers_from_config()
    assert result == []


def test_webhook_provider_missing_url_returns_empty(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '[{"type": "webhook", "enabled": true}]',
    )
    result = load_providers_from_config()
    assert result == []


def test_azure_queue_missing_connection_string_returns_empty(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '[{"type": "azure_storage_queue", "enabled": true, "queue_name": "q"}]',
    )
    result = load_providers_from_config()
    assert result == []


def test_provider_missing_type_is_skipped(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '[{"enabled": true}]',
    )
    result = load_providers_from_config()
    assert result == []
```

**Step 2: Run and confirm pass**

```bash
python -m pytest tests/unit/services/test_event_config_gaps.py -v --no-cov
```
Expected: All tests PASS.

**Step 3: Commit**

```bash
git add tests/unit/services/test_event_config_gaps.py
git commit -m "test: add event config gap coverage tests"
```

---

## Task 13: Event manager gap tests

**Files:**
- Create: `tests/unit/services/test_event_manager_gaps.py`

**Step 1: Write the tests**

```python
"""Unit tests for EventManager gap coverage (timeout, exception, close paths)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.events.manager import EventManager
from app.models.events import DicomEvent

pytestmark = pytest.mark.unit


def make_event() -> DicomEvent:
    return DicomEvent(
        study_instance_uid="1.2.3",
        series_instance_uid="1.2.3.4",
        sop_instance_uid="1.2.3.4.5",
        action="Create",
        state="current",
    )


@pytest.mark.asyncio
async def test_publish_timeout_does_not_raise():
    """Provider timeout is caught and logged — no exception propagates."""
    provider = MagicMock()
    provider.publish = AsyncMock(side_effect=asyncio.TimeoutError())
    provider.__class__.__name__ = "MockProvider"

    manager = EventManager(providers=[provider], timeout=0.001)
    event = make_event()
    # Should not raise
    await manager.publish(event)


@pytest.mark.asyncio
async def test_publish_exception_does_not_raise():
    """Provider exception is caught and logged — no exception propagates."""
    provider = MagicMock()
    provider.publish = AsyncMock(side_effect=RuntimeError("boom"))
    provider.__class__.__name__ = "MockProvider"

    manager = EventManager(providers=[provider], timeout=5.0)
    await manager.publish(make_event())


@pytest.mark.asyncio
async def test_publish_batch_timeout_does_not_raise():
    provider = MagicMock()
    provider.publish_batch = AsyncMock(side_effect=asyncio.TimeoutError())
    provider.__class__.__name__ = "MockProvider"

    manager = EventManager(providers=[provider], timeout=0.001)
    await manager.publish_batch([make_event(), make_event()])


@pytest.mark.asyncio
async def test_close_exception_does_not_raise():
    """Provider close() error is caught — no exception propagates."""
    provider = MagicMock()
    provider.close = AsyncMock(side_effect=RuntimeError("close failed"))
    provider.__class__.__name__ = "MockProvider"

    manager = EventManager(providers=[provider])
    await manager.close()


@pytest.mark.asyncio
async def test_publish_successful_provider():
    provider = MagicMock()
    provider.publish = AsyncMock(return_value=None)

    manager = EventManager(providers=[provider])
    await manager.publish(make_event())
    provider.publish.assert_called_once()


@pytest.mark.asyncio
async def test_no_providers_publish_does_not_raise():
    manager = EventManager(providers=[])
    await manager.publish(make_event())
```

**Step 2: Run and confirm pass**

```bash
python -m pytest tests/unit/services/test_event_manager_gaps.py -v --no-cov
```
Expected: All tests PASS.

**Step 3: Commit**

```bash
git add tests/unit/services/test_event_manager_gaps.py
git commit -m "test: add event manager gap coverage tests"
```

---

## Task 14: Final coverage check

**Step 1: Run the full unit test suite with coverage**

```bash
cd /Users/rhavekost/Projects/rhavekost/azure-dicom-service-emulator
python -m pytest tests/unit/ -v --cov=app --cov-report=term-missing 2>&1 | tail -30
```

Expected: Total coverage ≥ 85%. The `FAIL Required test coverage` message should be gone.

**Step 2: If coverage is below 85%, identify the remaining gaps**

```bash
python -m pytest tests/unit/ --cov=app --cov-report=term-missing 2>&1 | grep -E "^\s+app.*[0-9]+%\s" | sort -t% -k1 -n | head -20
```

Look at the lowest-covered files and add targeted tests for specific missing lines.

**Step 3: Commit final state**

```bash
git add -A
git commit -m "test: unit test suite complete — coverage ≥85%"
```
