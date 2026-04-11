# Troubleshooting

Common problems and how to resolve them.

## Database Issues

### When to Wipe and Recreate the Database

Wipe the database when:

- The schema changed (new columns, new tables) and you want a clean slate rather than writing a migration
- The emulator is returning unexpected data from a previous test run
- You want a guaranteed-empty state before running integration tests

The emulator uses SQLAlchemy `Base.metadata.create_all()` on startup. Wiping and restarting re-creates all tables automatically.

### Database Reset Procedure

**Docker Compose (recommended)**

```bash
# Stop the stack
docker compose down

# Remove the postgres volume (this deletes all DICOM metadata)
docker volume rm azure-dicom-service-emulator_postgres_data

# Also clear stored DCM files if needed
docker volume rm azure-dicom-service-emulator_dicom_data

# Start fresh
docker compose up -d
```

**Verify the volumes that exist before deleting:**

```bash
docker volume ls | grep azure-dicom
```

**Standalone container (bring-your-own PostgreSQL)**

```bash
psql -U emulator -d dicom_emulator -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

Then restart the emulator container so SQLAlchemy re-creates the tables:

```bash
docker restart <container-name>
```

**Access the database directly for inspection:**

```bash
docker compose exec postgres psql -U emulator -d dicom_emulator

# Useful queries:
\dt                                         -- list all tables
SELECT COUNT(*) FROM dicom_instances;
SELECT * FROM change_feed_entries ORDER BY sequence DESC LIMIT 10;
```

---

## Port Conflicts

### Port 8080 (Emulator API)

**Symptom:** `docker compose up` fails with `address already in use` or the emulator is not reachable.

**Find what is using port 8080:**

```bash
lsof -i :8080
```

**Option A — Change the host port in docker-compose.yml:**

```yaml
services:
  emulator:
    ports:
      - "9090:8080"   # was "8080:8080"
```

Then update your `DICOM_SERVICE_URL` to `http://localhost:9090`.

**Option B — Stop the conflicting process:**

```bash
# macOS / Linux
kill $(lsof -ti :8080)
```

### Port 5432 (PostgreSQL)

If you have a local PostgreSQL instance already running on 5432:

```yaml
services:
  postgres:
    ports:
      - "5433:5432"   # map to a different host port
```

The emulator container connects to PostgreSQL on the internal Docker network (`postgres:5432`), so the host-side mapping does not affect it. Only change the host port if you need direct `psql` access from your machine.

### Ports 10000–10002 (Azurite)

The default `docker-compose.yml` does not include Azurite. If you are running Azurite alongside the emulator (for Azure Storage Queue event publishing), the default Azurite ports are:

| Port | Service |
|------|---------|
| 10000 | Blob Service |
| 10001 | Queue Service |
| 10002 | Table Service |

If another service occupies these ports, edit the Azurite section in your compose file:

```yaml
services:
  azurite:
    ports:
      - "11000:10000"
      - "11001:10001"
      - "11002:10002"
```

Update the `EVENT_PROVIDERS` environment variable to point to the new host port.

---

## Docker Issues

### Container Does Not Start / Exits Immediately

**Check the logs first:**

```bash
docker compose logs emulator
```

**Common causes:**

| Symptom in logs | Cause | Fix |
|-----------------|-------|-----|
| `could not connect to server` | PostgreSQL not ready | Increase `start_period` or retry delay in healthcheck |
| `DATABASE_URL` not set | Missing env var | Set `DATABASE_URL` in `.env` or `docker-compose.yml` |
| `permission denied: /data/dicom` | Host volume permission | `chmod 777 ./dicom-data` (see below) |
| `ModuleNotFoundError` | Bad image build | Rebuild: `docker compose build --no-cache` |

### Permission Denied on /data/dicom

The container runs as a non-root `dicom` user (UID 1001). When mounting a host directory, the directory must be writable by that UID:

```bash
mkdir -p ./dicom-data
chmod 777 ./dicom-data

docker run -d \
  -v ./dicom-data:/data/dicom \
  rhavekost/azure-dicom-service-emulator:latest
```

### M1 / M2 / M3 Mac — arm64 Image Issues

The published Docker Hub image is multi-arch (amd64 + arm64), so `docker pull` should work transparently on Apple Silicon.

If you are building locally and deploying to a cloud service (Azure Container Apps, AWS ECS, etc.) that runs amd64:

```bash
# Always specify linux/amd64 for cloud targets
docker buildx build --platform linux/amd64 \
  -t myregistry/azure-dicom-emulator:latest \
  --push .
```

**Verify image architecture:**

```bash
docker manifest inspect myregistry/azure-dicom-emulator:latest
# Look for "architecture": "amd64"
```

An arm64 image deployed to an amd64 cluster will fail with `ImagePullFailure: no matching manifest` even though the image exists in the registry.

### Image Pull Failures

```
Error response from daemon: manifest unknown
```

Causes and fixes:

1. **Wrong tag** — run `docker pull rhavekost/azure-dicom-service-emulator:latest` to confirm the tag exists.
2. **Registry auth** — run `docker login` if using a private registry.
3. **arm64 image on amd64 host** — rebuild with `--platform linux/amd64` (see above).

### Stale Container After Code Change

```bash
# Rebuild the image and recreate the container
docker compose up -d --build emulator
```

---

## Common DICOM Client Errors

### 409 Conflict — Missing Required Attributes

**Error:** Store returns 409 with a failure reason.

**Cause:** The DICOM file is missing one or more required attributes. Required attributes for the emulator:

- `SOPClassUID` (0008,0016)
- `SOPInstanceUID` (0008,0018)
- `StudyInstanceUID` (0020,000D)
- `SeriesInstanceUID` (0020,000E)

**Fix:** Ensure your DICOM file includes all required UIDs. If generating test files with pydicom, see the `make_test_dicom` helper in [python-client.md](integration/python-client.md).

### 400 Bad Request — Wrong Content-Type

**Error:** Store returns 400.

**Cause:** The `Content-Type` header does not match the required multipart format.

**Required header:**

```
Content-Type: multipart/related; type="application/dicom"; boundary=<your-boundary>
```

**Common mistakes:**

- Sending `application/dicom` instead of `multipart/related`
- Omitting the `type` parameter
- Mismatched boundary value between header and body

### 409 Conflict — UID Already Exists (Duplicate)

**Error:** Store returns HTTP 202 with a `WarningReason` tag (0x00081196) containing `45070`.

**This is expected behavior** — Azure v2 stores the instance but warns about duplicates when using `POST /v2/studies`. If you want a silent overwrite, use `PUT /v2/studies` instead:

```bash
curl -X PUT http://localhost:8080/v2/studies \
  -H "Content-Type: multipart/related; type=application/dicom; boundary=b" \
  --data-binary @instance.dcm
```

### 404 Not Found — Wrong UID

Double-check that you are using the correct Study, Series, and SOP Instance UIDs. The emulator stores UIDs exactly as received — there is no normalization.

**List all studies to find valid UIDs:**

```bash
curl http://localhost:8080/v2/studies | python3 -m json.tool
```

### Large File Uploads Timing Out

Default `httpx` and `requests` timeouts may be too short for large DICOM files. Increase the timeout:

```python
# httpx
client = httpx.Client(timeout=120)

# requests
response = requests.post(url, data=body, headers=headers, timeout=120)
```

---

## Enabling Debug Logging

### Container Logs (Docker)

```bash
# Follow logs in real time
docker compose logs -f emulator

# Show last 100 lines
docker compose logs --tail=100 emulator
```

### FastAPI / Uvicorn Verbose Logging

Set the log level to `DEBUG` via environment variable:

```bash
docker run -e LOG_LEVEL=debug rhavekost/azure-dicom-service-emulator:latest
```

Or in `docker-compose.yml`:

```yaml
services:
  emulator:
    environment:
      LOG_LEVEL: debug
```

This enables verbose SQLAlchemy query logging and request/response tracing.

### Inspecting Raw HTTP Traffic

Use `curl` with `-v` to see full request and response headers:

```bash
curl -v -X POST http://localhost:8080/v2/studies \
  -H "Content-Type: multipart/related; type=application/dicom; boundary=b" \
  --data-binary @instance.dcm
```

Or use `httpx` debug mode in Python:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

import httpx
with httpx.Client() as client:
    response = client.get("http://localhost:8080/v2/studies")
```

### Interactive API Explorer

The emulator exposes a Swagger UI at `http://localhost:8080/docs` and a ReDoc UI at `http://localhost:8080/redoc`. Both let you call endpoints directly from the browser and inspect full request/response details without any client setup.
