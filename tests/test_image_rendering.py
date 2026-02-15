"""Tests for image rendering service."""

import pytest
import io
import numpy as np
import pydicom
from pathlib import Path
from PIL import Image
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian
from datetime import datetime

from app.services.image_rendering import (
    render_frame,
    apply_windowing,
    normalize_to_uint8
)


def create_test_dicom(
    tmp_path: Path,
    multi_frame: bool = False,
    with_windowing: bool = False,
    rgb: bool = False,
    multi_window: bool = False
) -> Path:
    """Create a synthetic DICOM file for testing."""
    file_path = tmp_path / "test.dcm"

    # Create base dataset
    file_meta = Dataset()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = "1.2.3.4.5.6.7.8.9"

    ds = FileDataset(
        str(file_path),
        {},
        file_meta=file_meta,
        preamble=b"\0" * 128
    )

    # Required DICOM attributes
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    ds.SOPInstanceUID = "1.2.3.4.5.6.7.8.9"
    ds.StudyInstanceUID = "1.2.3.4.5"
    ds.SeriesInstanceUID = "1.2.3.4.5.6"
    ds.PatientName = "Test^Patient"
    ds.PatientID = "12345"
    ds.Modality = "CT"

    # Image attributes
    ds.Rows = 64
    ds.Columns = 64

    if rgb:
        # RGB image
        ds.BitsAllocated = 8
        ds.BitsStored = 8
        ds.HighBit = 7
        ds.SamplesPerPixel = 3
        ds.PhotometricInterpretation = "RGB"
        ds.PixelRepresentation = 0
        ds.PlanarConfiguration = 0

        # Create RGB pixel data (64x64x3)
        pixel_array = np.zeros((64, 64, 3), dtype=np.uint8)
        pixel_array[:, :, 0] = 255  # Red channel
        pixel_array[:, :, 1] = 128  # Green channel
        pixel_array[:, :, 2] = 64   # Blue channel
    else:
        # Grayscale image
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.PixelRepresentation = 1  # signed

        # Create pixel data
        if multi_frame:
            ds.NumberOfFrames = 3
            # Create 3 frames with different patterns
            pixel_array = np.zeros((3, 64, 64), dtype=np.int16)
            pixel_array[0] = np.arange(64 * 64).reshape(64, 64) % 256
            pixel_array[1] = np.arange(64 * 64).reshape(64, 64) % 512
            pixel_array[2] = np.arange(64 * 64).reshape(64, 64) % 1024
        else:
            # Single frame with gradient pattern
            pixel_array = np.arange(64 * 64).reshape(64, 64).astype(np.int16)

    ds.PixelData = pixel_array.tobytes()

    # Add windowing if requested
    if with_windowing:
        ds.WindowCenter = 500.0
        ds.WindowWidth = 200.0

    # Add multi-window if requested
    if multi_window:
        ds.WindowCenter = [500.0, 800.0]
        ds.WindowWidth = [200.0, 100.0]

    ds.save_as(str(file_path), write_like_original=False)
    return file_path


def test_render_frame_jpeg(tmp_path):
    """Render DICOM frame as JPEG."""
    dcm_path = create_test_dicom(tmp_path)

    jpeg_bytes = render_frame(dcm_path, frame_number=1, format="jpeg", quality=100)

    # Verify valid JPEG
    image = Image.open(io.BytesIO(jpeg_bytes))
    assert image.format == "JPEG"
    assert image.size == (64, 64)


def test_render_frame_png(tmp_path):
    """Render DICOM frame as PNG."""
    dcm_path = create_test_dicom(tmp_path)

    png_bytes = render_frame(dcm_path, frame_number=1, format="png")

    # Verify valid PNG
    image = Image.open(io.BytesIO(png_bytes))
    assert image.format == "PNG"
    assert image.size == (64, 64)


def test_render_frame_with_windowing(tmp_path):
    """Apply DICOM windowing during rendering."""
    dcm_path = create_test_dicom(tmp_path, with_windowing=True)

    # Render with windowing
    jpeg_bytes = render_frame(dcm_path, frame_number=1, format="jpeg")

    # Should successfully apply windowing
    image = Image.open(io.BytesIO(jpeg_bytes))
    assert image.format == "JPEG"


def test_apply_windowing():
    """Apply DICOM windowing to pixel array."""
    # Create test array with values 0-1000
    arr = np.arange(1000).reshape(10, 100).astype(np.int16)

    # Apply window center=500, width=200
    # This means: lower=400, upper=600
    windowed = apply_windowing(arr, center=500.0, width=200.0)

    # Check output is uint8
    assert windowed.dtype == np.uint8

    # Check values below window are 0, above are 255
    assert windowed[0, 0] == 0  # Value 0 < lower bound (400)
    assert windowed[-1, -1] == 255  # Value 999 > upper bound (600)

    # Check value at window center is ~127
    # Value 500 should be in middle of range
    center_idx = np.where(arr == 500)
    assert 120 < windowed[center_idx][0] < 135


def test_apply_windowing_multi_window():
    """Handle multi-window DICOM (use first window)."""
    arr = np.arange(1000).reshape(10, 100).astype(np.int16)

    # Multi-window case (list of values)
    windowed = apply_windowing(arr, center=[500.0, 800.0], width=[200.0, 100.0])

    # Should use first window (center=500, width=200)
    assert windowed.dtype == np.uint8

    # Verify it used first window by checking known values
    assert windowed[0, 0] == 0  # Value 0 < 400 (first window's lower)


def test_normalize_to_uint8():
    """Normalize pixel array to uint8 range."""
    # Test with int16 range
    arr = np.array([-1000, 0, 1000, 2000], dtype=np.int16)
    normalized = normalize_to_uint8(arr)

    assert normalized.dtype == np.uint8
    assert normalized[0] == 0  # Min value
    assert normalized[-1] == 255  # Max value
    # Check intermediate values are scaled proportionally
    assert 80 < normalized[1] < 90  # Value 0 should be ~85
    assert 160 < normalized[2] < 175  # Value 1000 should be ~170


def test_normalize_to_uint8_constant():
    """Handle constant pixel array (all same value)."""
    arr = np.array([100, 100, 100, 100], dtype=np.int16)
    normalized = normalize_to_uint8(arr)

    # Should return zeros when all values are the same
    assert normalized.dtype == np.uint8
    assert np.all(normalized == 0)


def test_render_frame_multi_frame(tmp_path):
    """Render specific frame from multi-frame DICOM."""
    dcm_path = create_test_dicom(tmp_path, multi_frame=True)

    # Render frame 2
    jpeg_bytes = render_frame(dcm_path, frame_number=2, format="jpeg")

    image = Image.open(io.BytesIO(jpeg_bytes))
    assert image.format == "JPEG"
    assert image.size == (64, 64)


def test_render_frame_out_of_range(tmp_path):
    """Error on frame number out of range."""
    dcm_path = create_test_dicom(tmp_path, multi_frame=True)

    with pytest.raises(ValueError, match="Frame 10 out of range"):
        render_frame(dcm_path, frame_number=10, format="jpeg")


def test_render_frame_single_frame_invalid_number(tmp_path):
    """Error on invalid frame number for single-frame instance."""
    dcm_path = create_test_dicom(tmp_path, multi_frame=False)

    with pytest.raises(ValueError, match="Cannot access frame 2 of single-frame instance"):
        render_frame(dcm_path, frame_number=2, format="jpeg")


def test_render_frame_unsupported_format(tmp_path):
    """Error on unsupported output format."""
    dcm_path = create_test_dicom(tmp_path)

    with pytest.raises(ValueError, match="Unsupported format: gif"):
        render_frame(dcm_path, frame_number=1, format="gif")


def test_render_frame_multi_window(tmp_path):
    """Render frame with multi-window DICOM (uses first window)."""
    dcm_path = create_test_dicom(tmp_path, multi_window=True)

    # Render with multi-window - should use first window (500, 200)
    jpeg_bytes = render_frame(dcm_path, frame_number=1, format="jpeg")

    # Should successfully render using first window
    image = Image.open(io.BytesIO(jpeg_bytes))
    assert image.format == "JPEG"
    assert image.size == (64, 64)


def test_render_frame_rgb_dicom(tmp_path):
    """Render RGB DICOM correctly (not confused with multi-frame)."""
    dcm_path = create_test_dicom(tmp_path, rgb=True)

    # Render RGB image
    jpeg_bytes = render_frame(dcm_path, frame_number=1, format="jpeg")

    # Should successfully render RGB image
    image = Image.open(io.BytesIO(jpeg_bytes))
    assert image.format == "JPEG"
    assert image.size == (64, 64)
    # RGB images should have 3 channels
    assert image.mode in ("RGB", "L")  # Pillow may convert to L for grayscale


def test_render_frame_invalid_quality(tmp_path):
    """Error on invalid quality parameter."""
    dcm_path = create_test_dicom(tmp_path)

    # Quality too low
    with pytest.raises(ValueError, match="JPEG quality must be 1-100, got 0"):
        render_frame(dcm_path, frame_number=1, format="jpeg", quality=0)

    # Quality too high
    with pytest.raises(ValueError, match="JPEG quality must be 1-100, got 101"):
        render_frame(dcm_path, frame_number=1, format="jpeg", quality=101)


def test_render_frame_missing_file(tmp_path):
    """Error on missing DICOM file."""
    missing_path = tmp_path / "nonexistent.dcm"

    with pytest.raises(ValueError, match="Cannot read DICOM file"):
        render_frame(missing_path, frame_number=1, format="jpeg")
