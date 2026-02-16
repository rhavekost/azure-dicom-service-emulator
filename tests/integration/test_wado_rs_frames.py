"""Integration tests for WADO-RS frame retrieval endpoint."""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_retrieve_single_frame(client: TestClient):
    """Retrieve single frame from instance."""
    # Upload instance first (need to create test helper)
    pytest.skip("Requires DICOM test data - will implement after endpoint exists")


def test_retrieve_multiple_frames(client: TestClient):
    """Retrieve multiple frames from instance."""
    pytest.skip("Requires DICOM test data - will implement after endpoint exists")


def test_retrieve_frame_not_found(client: TestClient):
    """Test 404 when instance not found."""
    pytest.skip("Requires DICOM test data - will implement after endpoint exists")


def test_retrieve_frame_out_of_range(client: TestClient):
    """Test 404 when frame number out of range."""
    pytest.skip("Requires DICOM test data - will implement after endpoint exists")


# Helper to be implemented
def upload_test_instance(client):
    """Helper to upload test instance."""
    pytest.skip("Requires DICOM test data")
