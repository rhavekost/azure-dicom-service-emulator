# Azure Healthcare Workspace Emulator

A Docker-based local emulator for [Azure Health Data Services DICOM Service](https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/). Drop-in replacement for development, testing, and CI/CD — like [Azurite](https://github.com/Azure/Azurite) for Storage, but for Healthcare DICOM Service.

## Why?

Microsoft archived `microsoft/dicom-server` and there's no official local emulator. If you're building against Azure's DICOM Service API, you currently need a live Azure subscription for every dev/test cycle. This project fills that gap.

## What's Emulated

### Standard DICOMweb (v2 API)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v2/studies` | `POST` | STOW-RS — store instances (duplicate warning 45070) |
| `/v2/studies` | `PUT` | STOW-RS — upsert instances (no duplicate warning) |
| `/v2/studies/{study}` | `GET` | WADO-RS — retrieve study |
| `/v2/studies/{study}/metadata` | `GET` | WADO-RS — study metadata |
| `/v2/studies/{study}/series/{series}` | `GET` | WADO-RS — retrieve series |
| `/v2/studies/{study}/series/{series}/instances/{instance}` | `GET` | WADO-RS — retrieve instance |
| `/v2/studies/{study}/series/{series}/instances/{instance}/frames/{frames}` | `GET` | WADO-RS — retrieve frames (single or comma-separated) |
| `/v2/studies/{study}/series/{series}/instances/{instance}/rendered` | `GET` | WADO-RS — rendered instance as JPEG/PNG |
| `/v2/studies/{study}/series/{series}/instances/{instance}/frames/{frames}/rendered` | `GET` | WADO-RS — rendered frame as JPEG/PNG |
| `/v2/studies` | `GET` | QIDO-RS — search studies |
| `/v2/studies/{study}/series` | `GET` | QIDO-RS — search series |
| `/v2/studies/{study}/series/{series}/instances` | `GET` | QIDO-RS — search instances |
| `/v2/studies/{study}` | `DELETE` | Delete study |
| `/v2/studies/{study}/series/{series}` | `DELETE` | Delete series |
| `/v2/studies/{study}/series/{series}/instances/{instance}` | `DELETE` | Delete instance |

### Azure-Specific APIs

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v2/changefeed` | `GET` | Change Feed with time-window queries |
| `/v2/changefeed/latest` | `GET` | Latest change feed entry |
| `/v2/extendedquerytags` | `GET/POST` | Extended Query Tags CRUD |
| `/v2/extendedquerytags/{tag}` | `GET/PATCH/DELETE` | Manage individual tags |
| `/v2/operations/{id}` | `GET` | Async operation status |

### UPS-RS Worklist Service

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v2/workitems` | `POST` | Create workitem (SCHEDULED state) |
| `/v2/workitems/{workitem_uid}` | `GET` | Retrieve workitem |
| `/v2/workitems/{workitem_uid}` | `PUT` | Update workitem attributes |
| `/v2/workitems/{workitem_uid}/state` | `PUT` | Change workitem state (claim, complete, cancel) |
| `/v2/workitems/{workitem_uid}/cancelrequest` | `POST` | Request cancellation (SCHEDULED only) |
| `/v2/workitems` | `GET` | Search workitems by filters |
| `/v2/workitems/{uid}/subscribers/{aet}` | `POST/DELETE` | Subscribe/unsubscribe (501 stub) |
| `/v2/workitems/subscriptions` | `GET` | List subscriptions (501 stub) |

### Azure v2 Behaviors

- Store only fails on missing required attributes (not searchable ones)
- Warnings return HTTP 202 with `WarningReason` tag
- Change Feed supports `startTime`/`endTime` parameters
- Operation status uses `"succeeded"` (not `"completed"`)

## Quick Start

```bash
# Clone and start
git clone https://github.com/kostlabs/azure-healthcare-workspace-emulator.git
cd azure-healthcare-workspace-emulator
docker compose up -d

# Verify
curl http://localhost:8080/health

# Open API docs
open http://localhost:8080/docs
```

### With Orthanc (for plugin development)

```bash
docker compose -f docker-compose.with-orthanc.yml up -d

# Emulator: http://localhost:8080 (Azure DICOM Service API)
# Orthanc:  http://localhost:8042 (Orthanc Explorer)
```

## Run Smoke Tests

```bash
pip install pydicom httpx
python scripts/smoke_test.py http://localhost:8080
```

### Expiry Headers Example

**Upload with 24-hour expiry:**
```bash
curl -X PUT http://localhost:8080/v2/studies \
  -H "Content-Type: multipart/related; type=application/dicom" \
  -H "msdicom-expiry-time-milliseconds: 86400000" \
  -H "msdicom-expiry-option: RelativeToNow" \
  -H "msdicom-expiry-level: Study" \
  -F "file=@instance.dcm"
```

Expired studies are automatically deleted every hour.

### Frame Retrieval Examples

**Retrieve single frame:**
```bash
curl http://localhost:8080/v2/studies/{study}/series/{series}/instances/{instance}/frames/1 \
  -H "Accept: application/octet-stream" \
  -o frame1.raw
```

**Retrieve multiple frames:**
```bash
curl http://localhost:8080/v2/studies/{study}/series/{series}/instances/{instance}/frames/1,3,5 \
  -H "Accept: multipart/related; type=application/octet-stream"
```

**Render instance as JPEG:**
```bash
curl http://localhost:8080/v2/studies/{study}/series/{series}/instances/{instance}/rendered \
  -H "Accept: image/jpeg" \
  -o rendered.jpg
```

**Render specific frame as PNG:**
```bash
curl http://localhost:8080/v2/studies/{study}/series/{series}/instances/{instance}/frames/1/rendered?quality=85 \
  -H "Accept: image/png" \
  -o frame1.png
```

## Architecture

```
┌─────────────────────────────────┐
│     Your Application / Tests     │
│   (Azure.Health.Dicom SDK,       │
│    curl, OHIF, Orthanc, etc.)    │
└──────────────┬──────────────────┘
               │ HTTP (DICOMweb v2)
┌──────────────▼──────────────────┐
│   Azure Healthcare Workspace     │
│          Emulator                │
│                                  │
│  FastAPI  ←→  pydicom engine     │
│     │                            │
│     ▼                            │
│  PostgreSQL    Filesystem        │
│  (metadata,    (DCM files)       │
│   change feed,                   │
│   query tags)                    │
└─────────────────────────────────┘
```

### Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| API Server | FastAPI | DICOMweb + Azure custom endpoints |
| DICOM Engine | pydicom | Parse, validate, extract metadata |
| Database | PostgreSQL (asyncpg) | Metadata index, change feed, query tags |
| Storage | Local filesystem | DCM file storage |
| Container | Docker + Compose | Deployment |

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://emulator:emulator@postgres:5432/dicom_emulator` | PostgreSQL connection |
| `DICOM_STORAGE_DIR` | `/data/dicom` | Where DCM files are stored |

## Storage Layout

```
{DICOM_STORAGE_DIR}/
  {study_uid}/
    {series_uid}/
      {instance_uid}/
        instance.dcm          # Original DICOM file
        frames/               # Cached extracted frames
          1.raw
          2.raw
```

## Features

### QIDO-RS Advanced Search

- ✅ **Fuzzy Matching** - Prefix word search on person names (PatientName, ReferringPhysicianName)
- ✅ **Wildcard Matching** - `*` (zero or more chars) and `?` (single char) support
- ✅ **UID List Queries** - Comma or backslash-separated UID lists
- ✅ **Extended Query Tag Search** - Search on custom tags

### Advanced Search Examples

**Fuzzy name search:**
```bash
curl "http://localhost:8080/v2/studies?PatientName=joh&fuzzymatching=true"
# Matches "John^Doe", "Johnson^Mary", etc.
```

**Wildcard search:**
```bash
curl "http://localhost:8080/v2/studies?PatientID=PAT*"
# Matches PAT123, PATIENT, PAT_001, etc.

curl "http://localhost:8080/v2/studies?StudyDescription=CT?Head"
# Matches "CT-Head", "CT_Head", "CT1Head", etc.
```

**UID list search:**
```bash
curl "http://localhost:8080/v2/studies?StudyInstanceUID=1.2.3,4.5.6,7.8.9"
# Returns studies matching any of the three UIDs
```

### UPS-RS Worklist Service

Unified Procedure Step (UPS) for managing scheduled procedures and worklists.

**Create a workitem:**
```bash
curl -X POST "http://localhost:8080/v2/workitems?1.2.3.workitem1" \
  -H "Content-Type: application/dicom+json" \
  -d '{
    "00080018": {"vr": "UI", "Value": ["1.2.3.workitem1"]},
    "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Doe^John"}]},
    "00100020": {"vr": "LO", "Value": ["PAT001"]}
  }'
```

**Search workitems:**
```bash
# By patient ID
curl "http://localhost:8080/v2/workitems?PatientID=PAT001"

# By state
curl "http://localhost:8080/v2/workitems?ProcedureStepState=SCHEDULED"

# With pagination
curl "http://localhost:8080/v2/workitems?limit=10&offset=0"
```

**Claim a workitem (SCHEDULED → IN PROGRESS):**
```bash
curl -X PUT "http://localhost:8080/v2/workitems/1.2.3.workitem1/state" \
  -H "Content-Type: application/dicom+json" \
  -d '{
    "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]},
    "00081195": {"vr": "UI", "Value": ["1.2.3.txn123"]}
  }'
```

**Update a workitem:**
```bash
curl -X PUT "http://localhost:8080/v2/workitems/1.2.3.workitem1" \
  -H "Content-Type: application/dicom+json" \
  -H "Transaction-UID: 1.2.3.txn123" \
  -d '{
    "00400100": {"vr": "SQ", "Value": [{"00400002": {"vr": "DA", "Value": ["20260215"]}}]}
  }'
```

**Complete a workitem (IN PROGRESS → COMPLETED):**
```bash
curl -X PUT "http://localhost:8080/v2/workitems/1.2.3.workitem1/state" \
  -H "Content-Type: application/dicom+json" \
  -d '{
    "00741000": {"vr": "CS", "Value": ["COMPLETED"]},
    "00081195": {"vr": "UI", "Value": ["1.2.3.txn123"]}
  }'
```

**Cancel a SCHEDULED workitem:**
```bash
curl -X POST "http://localhost:8080/v2/workitems/1.2.3.workitem1/cancelrequest"
```

**Key Features:**
- ✅ **State Machine** - SCHEDULED → IN PROGRESS → COMPLETED/CANCELED
- ✅ **Transaction UID Security** - Workitem ownership via transaction UIDs (never exposed in responses)
- ✅ **Search & Filter** - By patient, state, scheduled time
- ✅ **Atomic Updates** - Update attributes with transaction UID validation

## Testing

Comprehensive test suite with 225+ tests and 61%+ code coverage.

```bash
# Run all tests
pytest

# Run by layer
pytest tests/unit/ -m unit           # Unit tests
pytest tests/integration/ -m integration  # Integration tests

# With coverage report
pytest --cov=app --cov-report=html
open htmlcov/index.html

# Run in parallel (faster)
pytest -n auto
```

**Test Organization:**
- `tests/unit/` - Unit tests for models, services, utilities
- `tests/integration/` - API endpoint integration tests
- `tests/e2e/` - End-to-end workflow tests
- `tests/performance/` - Benchmarks and load tests
- `tests/security/` - Security scans

See [tests/README.md](tests/README.md) for complete testing guide.

**CI/CD:**
- Automated testing on every push and pull request
- Security scanning (Bandit, Safety)
- Pre-commit hooks for code quality

## Roadmap

- [x] WADO-RS frames and rendered endpoints
- [x] Fuzzy matching for PatientName in QIDO-RS
- [x] Wildcard matching for QIDO-RS
- [x] UID list queries for QIDO-RS
- [x] Worklist Service (UPS-RS) — full CRUD, state machine, search
- [x] Comprehensive test suite (225+ tests, 61%+ coverage)
- [x] CI/CD workflows (tests + security)
- [ ] Event Grid emulation (webhook notifications)
- [ ] Auth mock (accept any bearer token)
- [ ] Bulk Update API
- [ ] Published Docker image on GHCR

## Not Emulated (Yet)

- Bulk Import/Export
- Data Partitioning
- Azure RBAC / Managed Identity auth enforcement
- UPS-RS subscriptions (endpoints return 501 Not Implemented)

## Related Projects

- [orthanc-dicomweb-oauth](https://github.com/rhavekost/orthanc-dicomweb-oauth) — OAuth2 plugin for Orthanc DICOMweb
- [Azurite](https://github.com/Azure/Azurite) — Azure Storage emulator (inspiration for this project)
- [microsoft/dicom-server](https://github.com/microsoft/dicom-server) — Archived Microsoft DICOM server

## License

MIT — [KostLabs](https://kostlabs.com)
