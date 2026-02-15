# Azure Healthcare Workspace Emulator - Development Guide

## Project Overview

A Docker-based local emulator for [Azure Health Data Services DICOM Service](https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/). Drop-in replacement for development, testing, and CI/CD — like [Azurite](https://github.com/Azure/Azurite) for Storage, but for Healthcare DICOM Service.

**Purpose**: Eliminate the need for live Azure subscriptions during development and testing of DICOM-based healthcare applications.

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

## Project Structure

The project should follow this structure:

```
azure-dicom-service-emulator/
├── app/
│   ├── __init__.py
│   ├── database.py           # SQLAlchemy async engine + session
│   ├── models/
│   │   ├── __init__.py
│   │   └── dicom.py          # DB models (DicomInstance, ChangeFeedEntry, etc.)
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── dicomweb.py       # STOW/WADO/QIDO-RS endpoints
│   │   ├── changefeed.py     # Azure change feed API
│   │   ├── extended_query_tags.py  # Extended query tags CRUD
│   │   └── operations.py     # Async operation status
│   └── services/
│       ├── __init__.py
│       ├── dicom_engine.py   # pydicom parsing/validation
│       └── multipart.py      # multipart/related handling
├── main.py                   # FastAPI app entry point
├── Dockerfile
├── docker-compose.yml
├── requirements.txt or pyproject.toml
├── smoke_test.py
├── README.md
├── LICENSE
└── CLAUDE.md (this file)
```

## API Endpoints Implemented

### Standard DICOMweb (v2 API)

- **STOW-RS**: `POST /v2/studies` - Store DICOM instances
- **WADO-RS**: `GET /v2/studies/{study}` - Retrieve study/series/instances
- **WADO-RS Metadata**: `GET /v2/studies/{study}/metadata` - Get metadata without pixel data
- **QIDO-RS**: `GET /v2/studies` - Search studies/series/instances
- **DELETE**: `DELETE /v2/studies/{study}` - Remove studies/series/instances

### Azure-Specific APIs

- **Change Feed**: `GET /v2/changefeed` - Track all changes with time-window queries
- **Change Feed Latest**: `GET /v2/changefeed/latest` - Get latest sequence number
- **Extended Query Tags**: `GET/POST /v2/extendedquerytags` - Manage custom searchable tags
- **Operations**: `GET /v2/operations/{id}` - Check async operation status

## Development Workflow

### Prerequisites

- Docker Desktop installed and running
- Python 3.11+ for local development/testing
- Basic understanding of DICOM and DICOMweb standards

### Local Development Setup

1. **Clone and setup environment**:
   ```bash
   git clone <repo-url>
   cd azure-dicom-service-emulator
   python -m venv venv
   source venv/bin/activate  # or venv\Scripts\activate on Windows
   pip install -r requirements.txt
   ```

2. **Run with Docker Compose**:
   ```bash
   docker compose up -d
   ```

3. **Verify health**:
   ```bash
   curl http://localhost:8080/health
   ```

4. **Run smoke tests**:
   ```bash
   pip install pydicom httpx
   python smoke_test.py http://localhost:8080
   ```

### Docker Build Instructions

**CRITICAL**: Always build for `linux/amd64` when deploying to cloud services (Azure Container Apps, AWS ECS, etc.):

```bash
# For cloud deployment
docker buildx build --platform linux/amd64 -t <registry>/<image>:latest --push .

# For local testing only
docker build -t azure-dicom-emulator:local .
```

### Database Migrations

- The app uses SQLAlchemy with `Base.metadata.create_all()` on startup
- Schema changes require database reset or manual migration scripts
- For development, dropping and recreating the DB is acceptable

### Testing Strategy

1. **Smoke tests**: Run `smoke_test.py` after any API changes
2. **Manual testing**: Use FastAPI's `/docs` endpoint for interactive testing
3. **Integration tests**: Test with real DICOM clients (Orthanc, OHIF viewer)

## Code Style and Conventions

### Python Style

- Follow PEP 8
- Use type hints for all function signatures
- Use async/await for all database and I/O operations
- Keep functions focused and single-purpose

### DICOM JSON Format

- Follow PS3.18 F.2 specification
- Tag format: `"00100010"` (8 hex digits)
- Each tag contains: `{"vr": "VR_TYPE", "Value": [...]}`
- Person names use: `{"Alphabetic": "Last^First"}`

### Error Handling

- Azure v2 behavior: Only fail on required attribute validation
- Searchable attribute issues return HTTP 202 with warnings
- Use `WarningReason` tag (0x00081196) for coercion warnings
- Operation status uses `"succeeded"` (not `"completed"`)

## Azure v2 Specific Behaviors

The emulator mimics Azure DICOM Service v2 behaviors:

1. **Store (STOW-RS)**:
   - Returns 200 if all instances succeed
   - Returns 202 if warnings (searchable attribute issues)
   - Returns 409 if all instances fail
   - Only validates required attributes strictly

2. **Change Feed**:
   - Supports `startTime` and `endTime` query parameters
   - Entries have `state`: current, replaced, deleted
   - Sequence numbers are monotonically increasing

3. **Extended Query Tags**:
   - Status: Adding → Ready → Deleting
   - Supports reindexing via operation ID
   - Query status: Enabled/Disabled

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://emulator:emulator@postgres:5432/dicom_emulator` | PostgreSQL connection |
| `DICOM_STORAGE_DIR` | `/data/dicom` | DCM file storage path |

### Port Mapping

- **8080**: Emulator API (mapped from container)
- **5432**: PostgreSQL (exposed for debugging)

## Common Development Tasks

### Adding a New Endpoint

1. Define route in appropriate router (`app/routers/`)
2. Add any new DB models to `app/models/dicom.py`
3. Implement business logic in service layer if complex
4. Add smoke test case
5. Update README.md API table

### Adding Database Fields

1. Update model in `app/models/dicom.py`
2. Drop and recreate database (dev) or write migration (prod)
3. Update DICOM engine extraction logic if needed

### Debugging

- Check logs: `docker compose logs -f emulator`
- Access database: `docker compose exec postgres psql -U emulator -d dicom_emulator`
- Interactive API docs: http://localhost:8080/docs

## Deployment

### Local Docker

```bash
docker compose up -d
```

### Cloud Deployment (Azure Container Apps, AWS ECS, etc.)

**ALWAYS use linux/amd64 platform**:

```bash
docker buildx build --platform linux/amd64 -t <registry>/azure-dicom-emulator:v1.0.0 --push .
```

### Environment-Specific Configuration

- **Development**: Use docker-compose.yml as-is
- **Production**:
  - Use managed PostgreSQL instance
  - Mount persistent volume for DICOM files
  - Configure proper health checks
  - Consider authentication middleware

## Known Limitations

- No bulk import/export (yet)
- No Worklist Service (UPS-RS) support (yet)
- No data partitioning
- No Azure RBAC/Managed Identity auth enforcement
- Pixel data retrieval is basic (no frame-level or rendered endpoints yet)

## Contributing Guidelines

1. **Before starting**: Check existing issues and README roadmap
2. **Code changes**: Ensure smoke tests pass
3. **New features**: Add smoke test coverage
4. **Documentation**: Update README.md and this CLAUDE.md
5. **Commits**: Use conventional commits format

## Resources

- [DICOM Standard](https://www.dicomstandard.org/)
- [DICOMweb Standard (PS3.18)](https://dicom.nema.org/medical/dicom/current/output/html/part18.html)
- [Azure DICOM Service Docs](https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/)
- [pydicom Documentation](https://pydicom.github.io/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)

## License

MIT - [KostLabs](https://kostlabs.com)
