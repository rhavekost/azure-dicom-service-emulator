# Docker Best Practices Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Harden the public Docker image and publishing pipeline to meet community best practices before promoting to the DICOM community.

**Architecture:** Seven discrete tasks covering Dockerfile security, image metadata, health check cleanup, version pinning, GitHub Actions multi-arch publishing, docker-compose usability, and README documentation. Each task is independently committable.

**Tech Stack:** Docker / Buildx, GitHub Actions, FastAPI/Python 3.12, PostgreSQL 16, uv package manager

---

## Context

The image is already live at `hub.docker.com/r/rhavekost/azure-dicom-service-emulator`. These tasks make it production-safe and community-friendly. Key files:

- [`Dockerfile`](../../Dockerfile) — multi-stage builder + runtime
- [`docker-compose.yml`](../../docker-compose.yml) — 3-service dev stack (emulator + postgres + azurite)
- [`.github/workflows/security.yml`](../../.github/workflows/security.yml) — Bandit + Safety scans
- [`.github/workflows/tests.yml`](../../.github/workflows/tests.yml) — test matrix + coverage
- [`README.md`](../../README.md) — public-facing documentation

---

## Task 1: Add Non-Root User to Dockerfile (CRITICAL)

The container currently runs as UID 0 (root). Any compromise gives an attacker full system access. This violates CIS Docker Benchmark 4.1 and is a blocker for enterprise adoption.

**Files:**
- Modify: `Dockerfile`

**Step 1: Read the current Dockerfile**

Open [`Dockerfile`](../../Dockerfile) and locate the runtime stage (starts at line 23: `FROM python:3.12-slim`).

**Step 2: Add group/user creation after the apt-get install block**

In the runtime stage, after line 29 (`rm -rf /var/lib/apt/lists/*`), insert:

```dockerfile
# Create non-root user
RUN groupadd -r dicom && useradd -r -g dicom -d /app -s /sbin/nologin dicom
```

**Step 3: Fix ownership of the storage directory**

Replace line 44:
```dockerfile
RUN mkdir -p /data/dicom
```
with:
```dockerfile
RUN mkdir -p /data/dicom && chown -R dicom:dicom /data/dicom
```

**Step 4: Add USER directive before EXPOSE**

After line 44 (the `mkdir` line), insert:
```dockerfile
USER dicom
```

Final runtime stage order should be:
1. FROM python:3.12-slim
2. WORKDIR /app
3. apt-get install curl
4. groupadd/useradd
5. COPY --from=builder
6. COPY app/ and main.py
7. ENV statements
8. mkdir + chown /data/dicom
9. USER dicom
10. HEALTHCHECK
11. EXPOSE 8080
12. CMD

**Step 5: Build locally to verify**

```bash
docker build -t azure-dicom-emulator:test .
docker inspect azure-dicom-emulator:test | grep -A2 '"User"'
```
Expected output: `"User": "dicom"`

**Step 6: Run a quick smoke test**

```bash
docker run --rm -d --name dicom-test -p 8080:8080 \
  -e DATABASE_URL=sqlite:///test.db \
  azure-dicom-emulator:test
sleep 3
curl -f http://localhost:8080/health
docker stop dicom-test
```
Expected: `{"status": "healthy"}` (or similar)

**Step 7: Commit**

```bash
git add Dockerfile
git commit -m "fix: run container as non-root dicom user"
```

---

## Task 2: Add OCI Label Metadata to Dockerfile

Labels enable Docker Hub to show repository links, and tooling like Renovate and Dependabot to track image sources. Also required for `docker inspect` to return meaningful data.

**Files:**
- Modify: `Dockerfile`

**Step 1: Add LABEL block to the runtime stage**

After the `FROM python:3.12-slim` line (line 24, runtime stage), insert a LABEL block. Use the OCI standard `org.opencontainers.image.*` namespace:

```dockerfile
LABEL org.opencontainers.image.title="Azure DICOM Service Emulator" \
      org.opencontainers.image.description="Local drop-in replacement for Azure Health Data Services DICOM Service. For development, testing, and CI/CD." \
      org.opencontainers.image.url="https://hub.docker.com/r/rhavekost/azure-dicom-service-emulator" \
      org.opencontainers.image.source="https://github.com/rhavekost/azure-dicom-service-emulator" \
      org.opencontainers.image.documentation="https://github.com/rhavekost/azure-dicom-service-emulator#readme" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="KostLabs"
```

Note: Do NOT hardcode `version` here — the publish workflow (Task 5) will inject it dynamically via `--label`.

**Step 2: Verify labels appear in build**

```bash
docker build -t azure-dicom-emulator:test .
docker inspect azure-dicom-emulator:test | python3 -c "import sys,json; labels=json.load(sys.stdin)[0]['Config']['Labels']; [print(k,':',v) for k,v in labels.items()]"
```
Expected: All 7 labels printed.

**Step 3: Commit**

```bash
git add Dockerfile
git commit -m "chore: add OCI image labels to Dockerfile"
```

---

## Task 3: Remove curl from Runtime Image

`curl` is only used for the `HEALTHCHECK` command. Installing it adds ~3MB and an unnecessary network tool to the attack surface. Replace with a Python-based health check that uses only the standard library.

**Files:**
- Modify: `Dockerfile`

**Step 1: Remove the apt-get block from the runtime stage**

In the runtime stage, remove lines 28-29:
```dockerfile
# DELETE these two lines from the runtime stage only:
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
```

Keep the identical block in the builder stage (lines 8-9) — it's needed there.

**Step 2: Replace the HEALTHCHECK command**

Replace the existing HEALTHCHECK (line 47-48):
```dockerfile
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
  CMD curl -f http://localhost:8080/health || exit 1
```

With a Python socket check:
```dockerfile
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1
```

This uses only the Python standard library — no extra packages needed.

**Step 3: Build and verify image size decreased**

```bash
docker build -t azure-dicom-emulator:test .
docker images azure-dicom-emulator:test --format "{{.Size}}"
```
Expected: Size should be smaller than the previous ~93.1 MB reported on Docker Hub.

**Step 4: Verify health check still works**

```bash
docker run --rm -d --name dicom-test -p 8080:8080 azure-dicom-emulator:test
sleep 5
docker inspect dicom-test --format='{{.State.Health.Status}}'
docker stop dicom-test
```
Expected: `healthy`

**Step 5: Commit**

```bash
git add Dockerfile
git commit -m "chore: replace curl healthcheck with python stdlib, remove curl from runtime image"
```

---

## Task 4: Pin uv Version in Dockerfile

The Dockerfile currently uses `ghcr.io/astral-sh/uv:latest`, which means any breaking change in uv would silently break the build. Pin to a specific version for reproducible builds.

**Files:**
- Modify: `Dockerfile`

**Step 1: Find the latest stable uv version**

```bash
# Check the uv GitHub releases
curl -s https://api.github.com/repos/astral-sh/uv/releases/latest | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])"
```
This returns something like `0.6.14`. Note this version.

**Step 2: Update the COPY line in the builder stage**

Replace line 14:
```dockerfile
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
```
With (substituting the version you found):
```dockerfile
COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /usr/local/bin/uv
```

**Step 3: Build to verify**

```bash
docker build -t azure-dicom-emulator:test .
```
Expected: Build succeeds. Docker will pull the specific version tag.

**Step 4: Commit**

```bash
git add Dockerfile
git commit -m "chore: pin uv to specific version for reproducible builds"
```

---

## Task 5: Create GitHub Actions Docker Publish Workflow

Currently there is no automated image publishing. Users must build from source. This task creates a workflow that automatically builds and pushes a multi-arch image (amd64 + arm64) to Docker Hub on every tagged release.

**Files:**
- Create: `.github/workflows/docker-publish.yml`

**Step 1: Set up Docker Hub secrets in GitHub**

Before writing the workflow, you need two GitHub repository secrets:
1. Go to your repo → Settings → Secrets and variables → Actions → New repository secret
2. Add `DOCKERHUB_USERNAME` = `rhavekost`
3. Add `DOCKERHUB_TOKEN` = a Docker Hub access token (not your password)
   - Create at: hub.docker.com → Account Settings → Security → New Access Token
   - Name it `github-actions`, scope: Read & Write

**Step 2: Create the workflow file**

Create `.github/workflows/docker-publish.yml`:

```yaml
name: Publish Docker Image

on:
  push:
    tags:
      - 'v*.*.*'
  workflow_dispatch:
    inputs:
      tag:
        description: 'Image tag to publish (e.g. v1.0.0)'
        required: true

jobs:
  build-and-push:
    name: Build and Push Multi-Arch Image
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up QEMU (for arm64 cross-compilation)
        uses: docker/setup-qemu-action@v3

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to Docker Hub
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}

      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: rhavekost/azure-dicom-service-emulator
          tags: |
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=raw,value=latest,enable={{is_default_branch}}

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          build-args: |
            BUILD_DATE=${{ github.event.repository.updated_at }}
            GIT_COMMIT=${{ github.sha }}

      - name: Verify multi-arch manifest
        run: |
          docker manifest inspect rhavekost/azure-dicom-service-emulator:latest | \
            python3 -c "import sys,json; m=json.load(sys.stdin); [print(x['platform']['os']+'/'+x['platform']['architecture']) for x in m.get('manifests', [])]"
```

**Step 3: Verify the workflow file is valid YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/docker-publish.yml'))" && echo "Valid YAML"
```
Expected: `Valid YAML`

**Step 4: Commit and tag to trigger**

```bash
git add .github/workflows/docker-publish.yml
git commit -m "ci: add multi-arch Docker publish workflow"
git tag v1.0.0
git push origin main --tags
```

**Step 5: Verify on GitHub**

Go to your repo → Actions → "Publish Docker Image" — confirm it runs and both `linux/amd64` and `linux/arm64` appear in the build output.

**Step 6: Verify on Docker Hub**

```bash
docker manifest inspect rhavekost/azure-dicom-service-emulator:latest
```
Expected: Two manifests — one for `amd64`, one for `arm64`.

---

## Task 6: Improve docker-compose.yml for End Users

When someone `docker pull`s the image, they'll use a docker-compose.yml from the repo. It should be safe, clear about credentials, and resilient.

**Files:**
- Modify: `docker-compose.yml`

**Step 1: Add a warning comment about default credentials**

At the top of `docker-compose.yml`, after line 7 (the blank line after the header comment), add:

```yaml
# ⚠️  WARNING: Default credentials below are for LOCAL DEVELOPMENT ONLY.
#     Never deploy this configuration to production.
#     Change POSTGRES_PASSWORD and DATABASE_URL before any shared use.
```

**Step 2: Update emulator service to support published image**

Change the `emulator` service to use the published image by default, while keeping local build as an option. Replace the current `build: .` line with:

```yaml
  emulator:
    image: rhavekost/azure-dicom-service-emulator:latest
    # To build from source instead, comment out the line above and uncomment below:
    # build: .
```

**Step 3: Add restart policies to all three services**

Add `restart: unless-stopped` to `emulator`, `postgres`, and `azurite` services. Example for emulator:

```yaml
  emulator:
    image: rhavekost/azure-dicom-service-emulator:latest
    restart: unless-stopped
    ports:
      - "8081:8080"
    ...
```

Apply the same to `postgres` and `azurite`.

**Step 4: Verify compose file is valid**

```bash
docker compose config --quiet && echo "Valid"
```
Expected: `Valid` (no errors)

**Step 5: Test full stack comes up**

```bash
docker compose up -d
sleep 10
curl -f http://localhost:8081/health
docker compose down
```
Expected: `{"status": "healthy"}`

**Step 6: Commit**

```bash
git add docker-compose.yml
git commit -m "chore: use published image in compose, add restart policies and dev credentials warning"
```

---

## Task 7: Update README with Docker Usage + Troubleshooting

The README has no `docker pull` or `docker run` instructions. Anyone finding the Docker Hub image has no guidance on how to use it standalone (without cloning the repo).

**Files:**
- Modify: `README.md`

**Step 1: Read the current README Quick Start section**

Open [`README.md`](../../README.md) and find the "Quick Start" section. Note exactly where it ends so you can insert the new section after it.

**Step 2: Add a "Docker Hub" usage section**

After the Quick Start section, insert:

```markdown
## Docker Hub

The emulator is published as a multi-arch image (amd64 + arm64) on Docker Hub.

### Pull the image

```bash
docker pull rhavekost/azure-dicom-service-emulator:latest
```

### Run with Docker Compose (recommended)

The easiest way to start the full stack (emulator + PostgreSQL + Azurite):

```bash
# Download the compose file
curl -O https://raw.githubusercontent.com/rhavekost/azure-dicom-service-emulator/main/docker-compose.yml

# Start all services
docker compose up -d

# Verify
curl http://localhost:8081/health
```

### Run standalone (bring your own PostgreSQL)

```bash
docker run -d \
  --name dicom-emulator \
  -p 8080:8080 \
  -e DATABASE_URL=postgresql+asyncpg://user:pass@your-postgres:5432/dicom_db \
  -v dicom-data:/data/dicom \
  rhavekost/azure-dicom-service-emulator:latest
```

### Image tags

| Tag | Description |
|-----|-------------|
| `latest` | Latest stable release |
| `v1.0.0`, `v1.1.0`, etc. | Pinned semantic versions (recommended for production use) |
```

**Step 3: Add a Troubleshooting section**

Find the end of the README (before any license/contributing section) and insert:

```markdown
## Troubleshooting

### Port already in use

```bash
# Use a different host port
docker compose up -d --env HOST_PORT=9090
# or edit docker-compose.yml and change "8081:8080" to "9090:8080"
```

### Container exits immediately

Check logs:
```bash
docker compose logs emulator
```

Common causes:
- PostgreSQL not yet ready — the emulator retries on startup, but if postgres is very slow to start, increase `start_period` in the healthcheck
- `DATABASE_URL` env var not set when running standalone

### Permission denied on /data/dicom

The container runs as a non-root `dicom` user. If mounting a host directory, set permissions:
```bash
mkdir -p ./dicom-data
chmod 777 ./dicom-data
docker run -v ./dicom-data:/data/dicom ...
```

### Self-signed certificates

If your client rejects the emulator's TLS certificate in a reverse-proxy setup, you may need to disable certificate verification in your client. For `curl`:
```bash
curl -k https://localhost:8081/health
```
```

**Step 4: Update the Environment Variables table**

Find the configuration section in the README (the table with `DATABASE_URL` and `DICOM_STORAGE_DIR`). Add the missing `EVENT_PROVIDERS` row:

```markdown
| `EVENT_PROVIDERS` | (empty) | JSON array of event provider configs. See docker-compose.yml for Azure Storage Queue example. |
```

**Step 5: Commit**

```bash
git add README.md
git commit -m "docs: add Docker Hub usage, standalone run instructions, and troubleshooting guide"
```

---

## Task 8: Fix Security Workflow (Broken Safety Check)

The `safety` job in `.github/workflows/security.yml` references `requirements.txt` which does not exist — the project uses `uv` and `pyproject.toml`. This job silently fails or errors.

**Files:**
- Modify: `.github/workflows/security.yml`

**Step 1: Read the current security.yml**

Open [`.github/workflows/security.yml`](.github/workflows/security.yml) and note the `safety` job starting at line 52.

**Step 2: Replace the safety job**

Replace lines 52–81 (the entire `safety` job) with a uv-based version:

```yaml
  safety:
    name: Safety Dependency Check
    runs-on: ubuntu-latest
    permissions:
      contents: read

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install uv
        uses: astral-sh/setup-uv@v3

      - name: Install dependencies
        run: uv sync --frozen

      - name: Run Safety check
        run: uv run safety check --json --output safety-report.json || true

      - name: Upload Safety report
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: safety-report
          path: safety-report.json
```

**Step 3: Fix Bandit Python version**

On line 28, change:
```yaml
          python-version: '3.11'
```
to:
```yaml
          python-version: '3.12'
```
Apply the same fix to the `safety` job's Python version (already done in step 2 above).

**Step 4: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/security.yml'))" && echo "Valid"
```
Expected: `Valid`

**Step 5: Commit**

```bash
git add .github/workflows/security.yml
git commit -m "fix: update security workflow to use uv instead of missing requirements.txt, pin Python to 3.12"
```

---

## Final Verification

After all tasks are complete:

```bash
# 1. Build the final image locally
docker build -t azure-dicom-emulator:final .

# 2. Verify non-root user
docker inspect azure-dicom-emulator:final | python3 -c "import sys,json; print('User:', json.load(sys.stdin)[0]['Config']['User'])"
# Expected: User: dicom

# 3. Verify labels
docker inspect azure-dicom-emulator:final | python3 -c "import sys,json; labels=json.load(sys.stdin)[0]['Config']['Labels']; [print(k) for k in labels]"
# Expected: all org.opencontainers.image.* labels listed

# 4. Verify image size (should be smaller than 93.1MB)
docker images azure-dicom-emulator:final --format "{{.Size}}"

# 5. Full stack smoke test
docker compose up -d
sleep 10
curl -f http://localhost:8081/health
curl -f http://localhost:8081/docs > /dev/null && echo "API docs reachable"
docker compose down

# 6. Confirm all GitHub Actions workflows pass (after push)
gh run list --limit 5
```

---

## Summary

| Task | Severity | Effort | Impact |
|------|----------|--------|--------|
| 1. Non-root user | CRITICAL | ~10 min | Security |
| 2. OCI Labels | HIGH | ~5 min | Discoverability |
| 3. Remove curl | HIGH | ~5 min | Security + size |
| 4. Pin uv version | MEDIUM | ~5 min | Reproducibility |
| 5. Publish workflow | HIGH | ~20 min | Community access |
| 6. docker-compose improvements | MEDIUM | ~10 min | Usability |
| 7. README Docker docs | HIGH | ~15 min | Onboarding |
| 8. Fix security workflow | HIGH | ~10 min | CI reliability |
