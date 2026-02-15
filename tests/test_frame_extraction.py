import pytest
from pathlib import Path
from app.services.frame_extraction import extract_frames


def test_extract_single_frame(tmp_path):
    """Extract single frame from DICOM instance."""
    dcm_path = Path("tests/data/single_frame.dcm")

    if not dcm_path.exists():
        pytest.skip("Test data not available")

    frames = extract_frames(dcm_path, tmp_path)

    assert len(frames) == 1
    assert frames[0].exists()
    assert frames[0].stat().st_size > 0


def test_extract_multi_frame(tmp_path):
    """Extract multiple frames from DICOM instance."""
    dcm_path = Path("tests/data/multi_frame.dcm")

    if not dcm_path.exists():
        pytest.skip("Test data not available")

    frames = extract_frames(dcm_path, tmp_path)

    assert len(frames) > 1
    for frame_path in frames:
        assert frame_path.exists()
        assert frame_path.stat().st_size > 0


def test_extract_frames_no_pixel_data(tmp_path):
    """Raise error if instance has no pixel data."""
    dcm_path = Path("tests/data/no_pixels.dcm")

    if not dcm_path.exists():
        pytest.skip("Test data not available")

    with pytest.raises(ValueError, match="no pixel data"):
        extract_frames(dcm_path, tmp_path)
