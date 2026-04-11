"""
Centralized configuration for the Azure Healthcare Workspace Emulator.

All environment variable reads live here. Import from this module rather than
calling os.getenv() directly in application code.
"""

import os

# Path where DICOM DCM files are stored on the filesystem.
DICOM_STORAGE_DIR: str = os.getenv("DICOM_STORAGE_DIR", "/data/dicom")

# PostgreSQL async connection string used by SQLAlchemy.
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://emulator:emulator@postgres:5432/dicom_emulator",
)

# How often (in seconds) the background expiry cleanup task runs.
EXPIRY_INTERVAL_SECONDS: int = int(os.getenv("EXPIRY_INTERVAL_SECONDS", "3600"))

# EVENT_PROVIDERS — JSON-encoded event provider configuration (optional).
# This is intentionally read fresh on each call inside
# app/services/events/config.load_providers_from_config() so that tests can
# override it via monkeypatch.setenv without reloading modules.
# See app/services/events/config.py for the expected JSON schema.
