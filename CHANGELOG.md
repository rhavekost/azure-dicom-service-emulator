# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.1] - 2026-06-01

### Added
- `DISABLE_SSL_VERIFICATION` env var — disables TLS certificate validation for
  event webhook/queue providers in environments that use self-signed certificates.

### Fixed
- mypy type error in `app/services/multipart.py` — bytearray slice now correctly
  cast to `bytes` before passing to `_is_closing_marker`.

### Changed
- GitHub Actions bumped to latest pinned SHAs: `actions/checkout` v6.0.2,
  `actions/setup-python` v6.2.0, `astral-sh/setup-uv` v8.1.0,
  `docker/metadata-action` v6.1.0, `docker/build-push-action` v7.2.0.

## [0.4.0] - 2026-05-05

### Added
- DICOM parse process pool: pydicom parsing and DICOM-JSON conversion now run
  in a `ProcessPoolExecutor` (spawn context) so the asyncio event loop stays
  free to drain the request socket while another core handles parse work.
  New `app/services/dicom_parse_worker.py` is a deliberately
  picklable, FastAPI/SQLAlchemy-free worker module.
- Outbox sweeper for at-least-once event delivery: on startup, replays any
  `change_feed` rows whose `event_published_at` is NULL (rows persisted but
  never published because the previous process died between commit and the
  fire-and-forget publish task completing). New
  `app/services/events/outbox.py`.
- Streaming multipart parsing for STOW-RS and bulk chunking — see new tests
  `tests/unit/services/test_multipart_streaming.py` and
  `tests/unit/routers/test_stow_bulk_chunking.py`.
- Performance regression suite: `tests/performance/test_stow_benchmark.py`
  with 50 / 500 / opt-in 5 000 instance STOW scenarios via `pytest-benchmark`.
- New env vars (all with safe defaults — no migration required):
  - `UVICORN_WORKERS` (default `4`) — uvicorn worker process count
  - `DICOM_PARSE_WORKERS` (default `os.cpu_count()`, `0` disables) — parse
    pool size
  - `DICOM_PARSE_INFLIGHT` (default `2 × DICOM_PARSE_WORKERS`) — peak
    in-flight parts per STOW request
  - `DB_POOL_SIZE` (default `20`), `DB_MAX_OVERFLOW` (default `20`),
    `DB_POOL_TIMEOUT_SECONDS` (default `30`),
    `DB_POOL_RECYCLE_SECONDS` (default `1800`)
  - `EVENT_DRAIN_TIMEOUT_SECONDS` (default `30`) — bounds lifespan shutdown
    while flushing in-flight publishes
  - `OUTBOX_SWEEP_PAGE_SIZE`, `OUTBOX_SWEEP_MAX_AGE_SECONDS`

### Changed
- `app/routers/stow.py` rewritten around the parse pool with bulk chunking
  and streaming dispatch (~1,200 LOC delta).
- SQLAlchemy engine pool sized via env vars (was hardcoded `5/10`).
- Dockerfile / docker-compose updated for multi-worker uvicorn defaults.

### Fixed
- Multi-worker startup race when initializing the PostgreSQL schema. With
  `UVICORN_WORKERS > 1`, two workers could both pass the existence check in
  `CREATE EXTENSION IF NOT EXISTS pg_trgm` (or `CREATE TABLE IF NOT EXISTS`)
  and the loser would crash with `UniqueViolationError` on
  `pg_extension_name_index` / `pg_class_relname_nsp_index`. Uvicorn would
  respawn the worker and it would self-heal on the next boot (because the
  catalog row now exists), but the noisy "Application startup failed" /
  "Child process died" lines made every container start look broken. Schema
  initialization now serializes concurrent workers with
  `pg_advisory_xact_lock`, which is also safe under PgBouncer transaction-mode
  pooling.

## [0.3.3] - 2026-04-12

### Fixed
- UPS-RS `POST /v2/workitems/{uid}` on an existing SCHEDULED workitem without a
  transaction-uid now correctly updates the workitem (200) instead of returning 409.
  The 409 guard introduced in 0.3.2 was too broad — it should only fire when the
  payload includes `ProcedureStepState` (00741000), which signals a duplicate create
  attempt rather than a legitimate attribute update.
- QIDO-RS query parameters with an empty value (e.g. `?PatientName=`) are now skipped
  instead of generating a `column = ''` filter condition. Per DICOM PS3.18, bare
  parameter names without a value are `includefield` hints, not search filters; the
  previous behavior caused legitimate matches to be excluded from results.

## [0.3.2] - 2026-04-11

### Fixed
- UPS-RS duplicate workitem `POST /v2/workitems/{uid}` without a transaction-uid now
  correctly returns 409 Conflict instead of falling through to the update path (400)
- Restored `_UNSET` sentinel identity in test factories — broken sentinel caused
  `study_date=None` to be indistinguishable from the default, producing wrong STOW status codes

### Changed
- Dependency updates: uvicorn 0.44, numpy 2.4.4, aiofiles 25.1, safety 3.7, ruff 0.15,
  pytest-xdist 3.8; GitHub Actions SHA pins refreshed

## [0.3.1] - 2026-04-11

### Fixed
- STOW-RS `POST` with all-duplicate instances now returns 202 (not 409) — DICOM code 45070
  (0xB00E) is a warning, not a hard failure; only C-range codes drive 409
- Extended Query Tags `POST` now accepts a bare JSON array (Azure API standard) in addition
  to the wrapped `{"tags": [...]}` format
- UPS-RS workitem `GET` returning 404 after create when using `?workitem=UID` query form —
  query string is now parsed correctly for both bare `?{uid}` and named `?workitem={uid}`
- `aiofiles` missing from `uv.lock` caused `ModuleNotFoundError` in Docker builds

## [0.3.0] - 2026-04-11

### Added
- GIN/composite indexes on all major search columns for QIDO-RS performance
- E2E pytest suite covering STOW → WADO → QIDO → DELETE workflow
- E2E tests for Change Feed, Extended Query Tags, and UPS-RS workflow
- Integration tests for `AzureStorageQueueProvider` and upsert atomicity
- Unit tests for `expiry_cleanup_task` and frame cache concurrent access
- Performance baseline tests for STOW-RS and QIDO-RS

### Changed
- Split `dicomweb.py` (1,843 lines) into focused routers: `stow`, `wado`, `qido`, `delete`
- Centralized all environment variable reads into `app/config.py`
- Injected `event_manager` via `app/dependencies.py`, eliminating circular imports
- Migrated all SQLAlchemy models to `Mapped[T]` style annotations

### Fixed
- Replaced sync file I/O with `aiofiles` in all async handlers
- Replaced blocking sync I/O with async equivalents in `FileEventProvider`
- Eliminated TOCTOU races in file deletion using EAFP pattern
- Made upsert delete+store atomic within a single transaction
- Removed double-commit and fixed filesystem-before-commit ordering in upsert service
- Replaced silent `except pass` with `logger.debug` in `dicom_engine`

## [0.2.0] - 2025-01-01

### Added
- **Bulk Update**: `POST /v2/studies/$bulkUpdate` endpoint for batch attribute changes
- **ETag / If-None-Match**: Conditional GET support on WADO-RS metadata endpoints (RFC 7232)
- **QIDO-RS root-level search**: `GET /v2/instances` and `GET /v2/series` endpoints
- **UPS-RS POST alias**: `POST /v2/workitems/{uid}` alias for update to match Azure SDK conformance
- Multi-arch Docker image (linux/amd64 + linux/arm64) published to Docker Hub
- `mypy` type checking with zero errors across all app modules
- Comprehensive unit test suite (225+ tests) using AsyncClient + SQLite backend
- CI/CD: test matrix across Python 3.12, 3.13, 3.14 with per-version coverage thresholds
- CI/CD: GitHub Actions workflow for multi-arch Docker publish
- Pre-commit hooks for code quality enforcement
- Security scanning with Bandit and GitHub Actions
- Non-root `dicom` container user for improved security
- OCI image labels on Dockerfile
- Docker Hub usage documentation with standalone run instructions

### Changed
- STOW-RS `POST` returns 409 for duplicate instances (correct Azure v2 behavior)
- Coverage thresholds: 85% on Python 3.14, 72% on 3.12/3.13

### Fixed
- `Operation` model migrated to `Mapped[]` style to resolve mypy `Column` assignment errors
- `changeDataset` tag structure validation in bulk update handler
- QIDO-RS root-level search pagination, ordering, and query parameter conflicts
- `If-None-Match` header parsing per RFC 7232 (multi-value support)
- Operations endpoint converts string UUIDs to UUID objects for database queries
- Storage path consistency: instance files stored in `{study}/{series}/{instance}/` subdirectories
- Validated searchable attributes (PatientID, Modality, StudyDate) per Azure v2 spec
- Event sequence numbers sourced from `ChangeFeedEntry` in STOW-RS and DELETE
- Malformed multipart request error handling
- Circular import in dicomweb router

## [0.1.0] - 2024-12-01

### Added
- **STOW-RS**: `POST /v2/studies` to store DICOM instances with Azure v2 warning/error semantics
- **STOW-RS Upsert**: `PUT /v2/studies` with expiry header support (`msdicom-expiry-*`)
- **WADO-RS**: Study, series, and instance retrieval with metadata endpoints
- **WADO-RS Frames**: Single and multi-frame retrieval (`/frames/{n}`)
- **WADO-RS Rendered**: JPEG/PNG rendering for instances and frames
- **QIDO-RS**: Search studies, series, and instances with fuzzy matching, wildcard, UID list, date range, and `includefield` support
- **DELETE**: Study, series, and instance deletion
- **Change Feed**: `GET /v2/changefeed` with `startTime`/`endTime` query parameters; `GET /v2/changefeed/latest`
- **Extended Query Tags**: Full CRUD at `GET/POST /v2/extendedquerytags` and `GET/PATCH/DELETE /v2/extendedquerytags/{tag}`
- **Operations**: `GET /v2/operations/{id}` for async operation status
- **UPS-RS Worklist Service**: Create, retrieve, update, state machine (SCHEDULED → IN PROGRESS → COMPLETED/CANCELED), cancel request, search, and subscription stubs (501)
- **Event System**: Pluggable event providers — `InMemoryEventProvider`, `FileEventProvider`, `WebhookEventProvider`, `AzureStorageQueueProvider`
- **Expiry Service**: Background task for automatic study deletion based on `expires_at`
- **Frame Cache**: Concurrent-safe frame extraction and caching
- FastAPI application with PostgreSQL (asyncpg) backend and local filesystem DICOM storage
- Docker Compose stack including PostgreSQL and Azurite (Azure Storage Queue emulation)
- `pydicom`-based DICOM parsing, validation, and metadata extraction
- DICOM JSON format per PS3.18 F.2 with person-name and sequence support
- `/health` endpoint and `/debug/events` endpoint for testing
- Smoke test script (`scripts/smoke_test.py`)
- Layered test directory structure: `tests/unit/`, `tests/integration/`, `tests/e2e/`, `tests/performance/`

[0.4.0]: https://github.com/rhavekost/azure-dicom-service-emulator/compare/v0.3.4...v0.4.0
[0.3.4]: https://github.com/rhavekost/azure-dicom-service-emulator/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/rhavekost/azure-dicom-service-emulator/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/rhavekost/azure-dicom-service-emulator/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/rhavekost/azure-dicom-service-emulator/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/rhavekost/azure-dicom-service-emulator/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/rhavekost/azure-dicom-service-emulator/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/rhavekost/azure-dicom-service-emulator/releases/tag/v0.1.0
