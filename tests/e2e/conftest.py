"""E2E test configuration and fixtures.

These tests run against a live service (docker-compose).
Set DICOM_E2E_BASE_URL to override the default http://localhost:8080.
"""

import os

import httpx
import pytest

E2E_BASE_URL = os.environ.get("DICOM_E2E_BASE_URL", "http://localhost:8080")


def pytest_configure(config):
    """Register the e2e mark (also declared in pyproject.toml; safe to repeat)."""
    config.addinivalue_line(
        "markers",
        "e2e: End-to-end workflow tests that require a live service",
    )


@pytest.fixture(scope="session")
def base_url() -> str:
    """Base URL for the live DICOM service."""
    return E2E_BASE_URL


@pytest.fixture(scope="session")
def http_client(base_url: str) -> httpx.Client:
    """Synchronous httpx client pointed at the live service.

    Raises pytest.skip if the service is not reachable.
    """
    try:
        response = httpx.get(f"{base_url}/health", timeout=5.0)
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        pytest.skip(f"Live service not reachable at {base_url}: {exc}")

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client
