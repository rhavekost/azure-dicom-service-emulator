import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from app.services.frame_cache import FrameCache


def test_frame_cache_get_or_extract(tmp_path):
    """Get frames from cache or extract if not cached."""
    storage_dir = tmp_path / "storage"
    cache = FrameCache(storage_dir)

    study_uid = "1.2.3"
    series_uid = "4.5.6"
    instance_uid = "7.8.9"

    # Create fake DICOM file
    dcm_path = storage_dir / study_uid / series_uid / instance_uid / "instance.dcm"
    dcm_path.parent.mkdir(parents=True)
    dcm_path.write_text("fake")

    # Mock extract_frames to create and return fake frame paths
    fake_frames_dir = dcm_path.parent / "frames"
    fake_frame = fake_frames_dir / "1.raw"

    def mock_extract(dcm_path, frames_dir):
        frames_dir.mkdir(parents=True, exist_ok=True)
        fake_frame.write_bytes(b"fake_pixel_data")
        return [fake_frame]

    with patch('app.services.frame_cache.extract_frames') as mock_extract_func:
        mock_extract_func.side_effect = mock_extract

        # First call should extract
        frames = cache.get_or_extract(study_uid, series_uid, instance_uid)

        assert len(frames) == 1
        assert frames[0] == fake_frame
        mock_extract_func.assert_called_once()


def test_frame_cache_uses_cached_frames(tmp_path):
    """Use cached frames if available."""
    storage_dir = tmp_path / "storage"
    cache = FrameCache(storage_dir)

    study_uid = "1.2.3"
    series_uid = "4.5.6"
    instance_uid = "7.8.9"

    # Create fake DICOM file and pre-cached frames
    instance_dir = storage_dir / study_uid / series_uid / instance_uid
    instance_dir.mkdir(parents=True)
    (instance_dir / "instance.dcm").write_text("fake")

    frames_dir = instance_dir / "frames"
    frames_dir.mkdir()
    frame1 = frames_dir / "1.raw"
    frame1.write_bytes(b"cached")

    with patch('app.services.frame_cache.extract_frames') as mock_extract:
        # Should NOT call extract_frames
        frames = cache.get_or_extract(study_uid, series_uid, instance_uid)

        assert len(frames) == 1
        assert frames[0] == frame1
        mock_extract.assert_not_called()


def test_frame_cache_get_specific_frame(tmp_path):
    """Get specific frame by number."""
    storage_dir = tmp_path / "storage"
    cache = FrameCache(storage_dir)

    study_uid = "1.2.3"
    series_uid = "4.5.6"
    instance_uid = "7.8.9"

    # Setup cached frames
    instance_dir = storage_dir / study_uid / series_uid / instance_uid
    frames_dir = instance_dir / "frames"
    frames_dir.mkdir(parents=True)
    (instance_dir / "instance.dcm").write_text("fake")

    frame1 = frames_dir / "1.raw"
    frame2 = frames_dir / "2.raw"
    frame1.write_bytes(b"frame1")
    frame2.write_bytes(b"frame2")

    # Get frame 2
    frame = cache.get_frame(study_uid, series_uid, instance_uid, 2)
    assert frame == frame2


def test_frame_cache_frame_out_of_range(tmp_path):
    """Raise error if frame number out of range."""
    storage_dir = tmp_path / "storage"
    cache = FrameCache(storage_dir)

    study_uid = "1.2.3"
    series_uid = "4.5.6"
    instance_uid = "7.8.9"

    # Setup single frame
    instance_dir = storage_dir / study_uid / series_uid / instance_uid
    frames_dir = instance_dir / "frames"
    frames_dir.mkdir(parents=True)
    (instance_dir / "instance.dcm").write_text("fake")
    (frames_dir / "1.raw").write_bytes(b"frame1")

    with pytest.raises(ValueError, match="Frame 5 out of range"):
        cache.get_frame(study_uid, series_uid, instance_uid, 5)
