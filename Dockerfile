# syntax=docker/dockerfile:1

# ── Builder Stage ──────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml .
COPY .python-version .
COPY uv.lock .
COPY README.md .

# Install dependencies
RUN uv sync --frozen --no-dev

# ── Runtime Stage ──────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY app/ /app/app/
COPY main.py /app/

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV DICOM_STORAGE_DIR=/data/dicom

# Create storage directory
RUN mkdir -p /data/dicom

# Health check
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
  CMD curl -f http://localhost:8080/health || exit 1

# Expose port
EXPOSE 8080

# Run application
CMD /app/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080
