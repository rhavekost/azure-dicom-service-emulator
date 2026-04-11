# Azure DICOM Service Emulator

A local drop-in replacement for [Azure Health Data Services DICOM Service](https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/) — like [Azurite](https://github.com/Azure/Azurite) for Storage, but for DICOM.

**Why?** Microsoft archived `microsoft/dicom-server` and there's no official local emulator. If you're building against Azure's DICOM Service API, you currently need a live Azure subscription for every dev/test cycle. This project fills that gap.

---

## Quick Start

```bash
# Download the compose file
curl -O https://raw.githubusercontent.com/rhavekost/azure-dicom-service-emulator/main/docker-compose.yml

# Start emulator + PostgreSQL + Azurite
docker compose up -d

# Verify
curl http://localhost:8080/health
# {"status":"healthy","service":"azure-healthcare-workspace-emulator"}

# Open interactive API docs
open http://localhost:8080/docs
```

---

## What's Emulated

### DICOMweb (v2 API)

| Operation | Method | Endpoint |
|-----------|--------|----------|
| **STOW-RS** | `POST` | `/v2/studies` — store instances |
| **STOW-RS upsert** | `PUT` | `/v2/studies` — replace instances |
| **WADO-RS** | `GET` | `/v2/studies/{study}` — retrieve |
| **WADO-RS metadata** | `GET` | `/v2/studies/{study}/metadata` |
| **WADO-RS frames** | `GET` | `.../instances/{instance}/frames/{frames}` |
| **WADO-RS rendered** | `GET` | `.../rendered` — JPEG/PNG output |
| **QIDO-RS** | `GET` | `/v2/studies`, `/v2/.../series`, `/v2/.../instances` |
| **DELETE** | `DELETE` | Study / series / instance |

### Azure-Specific APIs

| Feature | Endpoint |
|---------|----------|
| **Change Feed** | `GET /v2/changefeed` — time-windowed audit log |
| **Extended Query Tags** | `GET/POST/DELETE /v2/extendedquerytags` |
| **Async Operations** | `GET /v2/operations/{id}` |
| **UPS-RS Worklist** | `POST/GET/PUT /v2/workitems` — full state machine |

### Advanced QIDO Features

- **Fuzzy matching** — prefix word search on person names (`fuzzymatching=true`)
- **Wildcards** — `*` (any chars) and `?` (single char) in query parameters
- **UID list queries** — comma-separated UID lists
- **Extended tag search** — query on custom registered tags

---

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://emulator:emulator@postgres:5432/dicom_emulator` | PostgreSQL connection |
| `DICOM_STORAGE_DIR` | `/data/dicom` | Where `.dcm` files are stored |
| `EXPIRY_INTERVAL_SECONDS` | `3600` | How often expired studies are cleaned up |
| `EVENT_PROVIDERS` | _(empty)_ | JSON array of event provider configs (Azure Storage Queue, webhook, etc.) |

---

## Run Standalone (bring your own PostgreSQL)

```bash
docker run -d \
  --name dicom-emulator \
  -p 8080:8080 \
  -e DATABASE_URL=postgresql+asyncpg://user:pass@your-postgres:5432/dicom_db \
  -v dicom-data:/data/dicom \
  rhavekost/azure-dicom-service-emulator:latest
```

---

## Image Tags

| Tag | Description |
|-----|-------------|
| `latest` | Latest stable release |
| `v0.3.0`, `v0.2.0`, … | Pinned semantic versions (recommended) |

Multi-arch: **linux/amd64** and **linux/arm64** (M1/M2/M3 Mac native).

---

## Use in CI/CD

```yaml
# GitHub Actions example
services:
  dicom-emulator:
    image: rhavekost/azure-dicom-service-emulator:latest
    ports:
      - 8080:8080
    env:
      DATABASE_URL: postgresql+asyncpg://emulator:emulator@postgres:5432/dicom_emulator
  postgres:
    image: postgres:16
    env:
      POSTGRES_USER: emulator
      POSTGRES_PASSWORD: emulator
      POSTGRES_DB: dicom_emulator
```

Then point your Azure DICOM SDK at `http://localhost:8080` instead of your Azure subscription URL.

---

## Links

- **GitHub:** [rhavekost/azure-dicom-service-emulator](https://github.com/rhavekost/azure-dicom-service-emulator)
- **C# integration guide:** [docs/integration/csharp-client.md](https://github.com/rhavekost/azure-dicom-service-emulator/blob/main/docs/integration/csharp-client.md)
- **Python integration guide:** [docs/integration/python-client.md](https://github.com/rhavekost/azure-dicom-service-emulator/blob/main/docs/integration/python-client.md)
- **Troubleshooting:** [docs/troubleshooting.md](https://github.com/rhavekost/azure-dicom-service-emulator/blob/main/docs/troubleshooting.md)
- **Azure DICOM Service docs:** [learn.microsoft.com](https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/)

---

MIT License — Rob Havekost
