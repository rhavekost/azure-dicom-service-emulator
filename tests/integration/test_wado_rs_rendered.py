import pytest
import io
from PIL import Image
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

def test_render_instance_jpeg(client: TestClient):
    """Render entire instance as JPEG."""
    pytest.skip("Requires DICOM test data - will implement later")


def test_render_instance_png(client: TestClient):
    """Render instance as PNG."""
    pytest.skip("Requires DICOM test data - will implement later")


def test_render_frame_with_quality(client: TestClient):
    """Render frame with JPEG quality parameter."""
    pytest.skip("Requires DICOM test data - will implement later")
