# Unit Test Coverage Design

**Goal:** Raise unit test coverage from 53.96% to ~89–92%, passing the 85% gate in `.coveragerc`, using FastAPI `AsyncClient` + in-memory SQLite with dependency injection overrides.

**Architecture:** A shared `conftest.py` in `tests/unit/routers/` provides a per-test isolated `async_client` fixture backed by SQLite (no Docker, no Postgres). Router tests use real HTTP calls. Service gap tests are pure function calls with no HTTP or DB.

**Tech Stack:** pytest-asyncio, httpx `AsyncClient` + `ASGITransport`, SQLAlchemy `aiosqlite`, existing `tests/fixtures/factories.py` DICOM factories.

**SQLite compatibility confirmed:** No PostgreSQL-specific SQL in any router. `ilike()` is the only potential concern — SQLAlchemy translates it to `LIKE` for SQLite automatically.

---

## Shared Fixture

```python
# tests/unit/routers/conftest.py
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.models.dicom import Base
from app.database import get_db
from main import app

@pytest.fixture
async def async_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
```

A `stored_instance` fixture stores a CT DICOM via POST once and yields the UIDs — used by retrieve, search, delete, and changefeed tests to avoid repeating store logic.

---

## File Layout

```
tests/unit/routers/
├── conftest.py                    ← async_client + stored_instance fixtures
├── test_dicomweb_stow.py          ← STOW-RS store/upsert/expiry
├── test_dicomweb_wado.py          ← WADO-RS retrieve/metadata/frames/rendered
├── test_dicomweb_qido.py          ← QIDO-RS search + pure helper functions
├── test_dicomweb_delete.py        ← DELETE + change feed side effects
├── test_changefeed.py             ← change feed query + latest
├── test_extended_query_tags.py    ← tag CRUD + 409 duplicate
├── test_operations.py             ← operation status + 404 + invalid UUID
├── test_debug.py                  ← debug events endpoints + 404 without provider
└── test_ups.py                    ← workitem lifecycle + state machine + 501 stubs

tests/unit/services/
├── test_accept_validation.py      ← new: retrieve vs rendered Accept header logic
├── test_event_config_gaps.py      ← new: disabled/malformed/missing/unknown provider
└── test_event_manager_gaps.py     ← new: timeout + exception + close error paths
```

---

## Test Cases by File

### `test_dicomweb_stow.py`
- `test_store_single_instance_returns_200`
- `test_store_duplicate_returns_409`
- `test_store_missing_required_uid_returns_409`
- `test_store_returns_warning_202_on_bad_searchable_attr`
- `test_upsert_with_expiry_header`
- `test_store_invalid_content_type_returns_415`
- `test_store_multiple_instances_all_succeed`
- `test_store_mixed_success_failure_returns_202`

### `test_dicomweb_wado.py`
- `test_retrieve_study_returns_multipart`
- `test_retrieve_series_returns_multipart`
- `test_retrieve_instance_returns_multipart`
- `test_retrieve_metadata_study`
- `test_retrieve_metadata_series`
- `test_retrieve_metadata_instance`
- `test_retrieve_nonexistent_study_returns_404`
- `test_retrieve_frames_single`
- `test_retrieve_frames_out_of_range_returns_404`
- `test_rendered_jpeg`
- `test_rendered_png`
- `test_invalid_accept_header_returns_406`

### `test_dicomweb_qido.py`
- `test_search_studies_returns_all`
- `test_search_by_patient_id`
- `test_search_by_patient_name_wildcard`
- `test_search_by_date_exact`
- `test_search_by_date_range`
- `test_search_with_limit_offset`
- `test_search_with_includefield`
- `test_search_series_under_study`
- `test_search_instances_under_series`
- `test_filter_dicom_json_by_includefield_all` (pure function)
- `test_parse_date_range_formats` (pure function)

### `test_dicomweb_delete.py`
- `test_delete_study`
- `test_delete_series`
- `test_delete_instance`
- `test_delete_nonexistent_returns_404`
- `test_delete_creates_changefeed_entry`

### `test_changefeed.py`
- `test_changefeed_empty`
- `test_changefeed_after_store`
- `test_changefeed_starttime_filter`
- `test_changefeed_endtime_filter`
- `test_changefeed_latest_empty`
- `test_changefeed_latest_after_store`

### `test_extended_query_tags.py`
- `test_list_tags_empty`
- `test_add_tag`
- `test_add_duplicate_tag_returns_409`
- `test_get_tag_by_path`
- `test_get_tag_not_found`
- `test_delete_tag`
- `test_delete_tag_not_found`

### `test_operations.py`
- `test_get_operation_not_found`
- `test_get_operation_invalid_uuid`
- `test_get_operation_valid`

### `test_debug.py`
- `test_debug_events_no_provider_returns_404`
- `test_debug_clear_no_provider_returns_404`

### `test_ups.py`
- `test_create_workitem`
- `test_create_workitem_duplicate_returns_409`
- `test_create_workitem_invalid_state_returns_400`
- `test_search_workitems_empty`
- `test_search_by_patient_id`
- `test_search_by_patient_name`
- `test_search_by_state`
- `test_retrieve_workitem`
- `test_retrieve_workitem_not_found`
- `test_update_workitem`
- `test_change_state_scheduled_to_inprogress`
- `test_change_state_invalid_transition_returns_400`
- `test_cancel_workitem`
- `test_cancel_non_scheduled_returns_400`
- `test_subscription_endpoints_return_501`

### `test_accept_validation.py`
- `test_retrieve_accepts_multipart_related`
- `test_retrieve_accepts_application_dicom`
- `test_retrieve_accepts_wildcard`
- `test_retrieve_rejects_text_plain`
- `test_retrieve_rejects_image_jpeg`
- `test_rendered_accepts_jpeg`
- `test_rendered_accepts_png`
- `test_rendered_accepts_wildcard`
- `test_rendered_rejects_application_dicom`

### `test_event_config_gaps.py`
- `test_disabled_provider_skipped`
- `test_malformed_json_returns_empty`
- `test_missing_required_fields_returns_none`
- `test_unknown_provider_type_returns_none`
- `test_array_vs_object_config_format`

### `test_event_manager_gaps.py`
- `test_publish_timeout_logs_warning`
- `test_publish_exception_per_provider_logs_warning`
- `test_close_handles_exception`
- `test_batch_publish_with_timeout`

---

## Coverage Projection

| Step | Lines covered | Running total |
|------|--------------|---------------|
| Baseline | — | 53.96% |
| STOW-RS | +200 | ~66% |
| WADO + DELETE | +150 | ~75% |
| QIDO + small routers | +100 | ~82% |
| UPS + services | +120 | ~89–92% |

Target: **≥85%** (gate in `.coveragerc`) with realistic ceiling of ~90%.
