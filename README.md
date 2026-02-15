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

## Roadmap

- [x] WADO-RS frames and rendered endpoints
- [x] Fuzzy matching for PatientName in QIDO-RS
- [x] Wildcard matching for QIDO-RS
- [x] UID list queries for QIDO-RS
- [ ] Event Grid emulation (webhook notifications)
- [ ] Auth mock (accept any bearer token)
- [ ] Bulk Update API
- [ ] Worklist Service (UPS-RS)
- [ ] Integration test suite
- [ ] Published Docker image on GHCR

## Not Emulated (Yet)

- Bulk Import/Export
- Worklist Service (UPS-RS) — lower priority
- Data Partitioning
- Azure RBAC / Managed Identity auth enforcement

## Related Projects

- [orthanc-dicomweb-oauth](https://github.com/rhavekost/orthanc-dicomweb-oauth) — OAuth2 plugin for Orthanc DICOMweb
- [Azurite](https://github.com/Azure/Azurite) — Azure Storage emulator (inspiration for this project)
- [microsoft/dicom-server](https://github.com/microsoft/dicom-server) — Archived Microsoft DICOM server

## License

MIT — [KostLabs](https://kostlabs.com)
