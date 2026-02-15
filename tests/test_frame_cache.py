import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
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


def test_frame_cache_failed_extraction(tmp_path):
    """Failed extraction marks instance and prevents retry."""
    storage_dir = tmp_path / "storage"
    cache = FrameCache(storage_dir)

    study_uid = "1.2.3"
    series_uid = "4.5.6"
    instance_uid = "7.8.9"

    # Create fake DICOM file
    dcm_path = storage_dir / study_uid / series_uid / instance_uid / "instance.dcm"
    dcm_path.parent.mkdir(parents=True)
    dcm_path.write_text("fake")

    # Mock extract_frames to raise error
    with patch('app.services.frame_cache.extract_frames') as mock_extract:
        mock_extract.side_effect = ValueError("Cannot decode pixel data")

        # First attempt should fail and mark as failed
        with pytest.raises(ValueError, match="Failed to extract frames"):
            cache.get_or_extract(study_uid, series_uid, instance_uid)

        # Verify it was marked as failed
        assert instance_uid in cache._extraction_failures

        # Second attempt should fail fast (cached failure)
        with pytest.raises(ValueError, match="failed extraction in last hour"):
            cache.get_or_extract(study_uid, series_uid, instance_uid)

        # extract_frames should only have been called once
        assert mock_extract.call_count == 1


def test_frame_cache_retry_after_failure(tmp_path):
    """Subsequent calls within TTL fail without re-attempting extraction."""
    storage_dir = tmp_path / "storage"
    cache = FrameCache(storage_dir, failure_ttl_seconds=3600)

    study_uid = "1.2.3"
    series_uid = "4.5.6"
    instance_uid = "7.8.9"

    # Create fake DICOM file
    dcm_path = storage_dir / study_uid / series_uid / instance_uid / "instance.dcm"
    dcm_path.parent.mkdir(parents=True)
    dcm_path.write_text("fake")

    # Mock extract_frames to raise error
    with patch('app.services.frame_cache.extract_frames') as mock_extract:
        mock_extract.side_effect = ValueError("Cannot decode pixel data")

        # First attempt fails
        with pytest.raises(ValueError, match="Failed to extract frames"):
            cache.get_or_extract(study_uid, series_uid, instance_uid)

        # Multiple subsequent attempts within TTL should fail fast
        for _ in range(3):
            with pytest.raises(ValueError, match="failed extraction in last hour"):
                cache.get_or_extract(study_uid, series_uid, instance_uid)

        # extract_frames should only have been called once
        assert mock_extract.call_count == 1


def test_frame_cache_retry_after_ttl(tmp_path):
    """Calls after TTL expiry succeed and re-attempt extraction."""
    storage_dir = tmp_path / "storage"
    cache = FrameCache(storage_dir, failure_ttl_seconds=3600)

    study_uid = "1.2.3"
    series_uid = "4.5.6"
    instance_uid = "7.8.9"

    # Create fake DICOM file
    dcm_path = storage_dir / study_uid / series_uid / instance_uid / "instance.dcm"
    dcm_path.parent.mkdir(parents=True)
    dcm_path.write_text("fake")

    # Mock extract_frames to raise error initially
    with patch('app.services.frame_cache.extract_frames') as mock_extract:
        mock_extract.side_effect = ValueError("Cannot decode pixel data")

        # First attempt fails
        with pytest.raises(ValueError, match="Failed to extract frames"):
            cache.get_or_extract(study_uid, series_uid, instance_uid)

        # Verify marked as failed
        assert instance_uid in cache._extraction_failures

    # Mock time advancement by setting failure time to 2 hours ago
    old_failure_time = datetime.now(timezone.utc) - timedelta(hours=2)
    cache._extraction_failures[instance_uid] = old_failure_time

    # Now mock extract_frames to succeed
    fake_frames_dir = dcm_path.parent / "frames"
    fake_frame = fake_frames_dir / "1.raw"

    def mock_extract_success(dcm_path, frames_dir):
        frames_dir.mkdir(parents=True, exist_ok=True)
        fake_frame.write_bytes(b"fake_pixel_data")
        return [fake_frame]

    with patch('app.services.frame_cache.extract_frames') as mock_extract:
        mock_extract.side_effect = mock_extract_success

        # After TTL expiry, should retry and succeed
        frames = cache.get_or_extract(study_uid, series_uid, instance_uid)

        assert len(frames) == 1
        assert frames[0] == fake_frame
        mock_extract.assert_called_once()

        # Verify failure entry was cleaned up
        assert instance_uid not in cache._extraction_failures


def test_frame_cache_configurable_ttl(tmp_path):
    """Custom failure TTL is respected."""
    storage_dir = tmp_path / "storage"
    # Set very short TTL for testing
    cache = FrameCache(storage_dir, failure_ttl_seconds=60)

    study_uid = "1.2.3"
    series_uid = "4.5.6"
    instance_uid = "7.8.9"

    # Create fake DICOM file
    dcm_path = storage_dir / study_uid / series_uid / instance_uid / "instance.dcm"
    dcm_path.parent.mkdir(parents=True)
    dcm_path.write_text("fake")

    # Mock extract_frames to raise error
    with patch('app.services.frame_cache.extract_frames') as mock_extract:
        mock_extract.side_effect = ValueError("Cannot decode pixel data")

        # First attempt fails
        with pytest.raises(ValueError, match="Failed to extract frames"):
            cache.get_or_extract(study_uid, series_uid, instance_uid)

    # Mock time advancement by setting failure time to 90 seconds ago (> 60s TTL)
    old_failure_time = datetime.now(timezone.utc) - timedelta(seconds=90)
    cache._extraction_failures[instance_uid] = old_failure_time

    # Now mock extract_frames to succeed
    fake_frames_dir = dcm_path.parent / "frames"
    fake_frame = fake_frames_dir / "1.raw"

    def mock_extract_success(dcm_path, frames_dir):
        frames_dir.mkdir(parents=True, exist_ok=True)
        fake_frame.write_bytes(b"fake_pixel_data")
        return [fake_frame]

    with patch('app.services.frame_cache.extract_frames') as mock_extract:
        mock_extract.side_effect = mock_extract_success

        # After custom TTL expiry, should retry
        frames = cache.get_or_extract(study_uid, series_uid, instance_uid)
        assert len(frames) == 1


def test_frame_cache_memory_cleanup(tmp_path):
    """Old failure entries are cleaned up during _is_failed check."""
    storage_dir = tmp_path / "storage"
    cache = FrameCache(storage_dir, failure_ttl_seconds=3600)

    # Manually add some old failure entries
    old_instance_1 = "old.instance.1"
    old_instance_2 = "old.instance.2"
    recent_instance = "recent.instance"

    # Old entries (2 hours ago)
    old_time = datetime.now(timezone.utc) - timedelta(hours=2)
    cache._extraction_failures[old_instance_1] = old_time
    cache._extraction_failures[old_instance_2] = old_time

    # Recent entry (30 minutes ago)
    recent_time = datetime.now(timezone.utc) - timedelta(minutes=30)
    cache._extraction_failures[recent_instance] = recent_time

    # Verify initial state
    assert len(cache._extraction_failures) == 3

    # Check old instance - should clean it up
    is_failed_1 = cache._is_failed(old_instance_1)
    assert not is_failed_1
    assert old_instance_1 not in cache._extraction_failures

    # Check another old instance - should clean it up
    is_failed_2 = cache._is_failed(old_instance_2)
    assert not is_failed_2
    assert old_instance_2 not in cache._extraction_failures

    # Check recent instance - should still be marked as failed
    is_failed_recent = cache._is_failed(recent_instance)
    assert is_failed_recent
    assert recent_instance in cache._extraction_failures

    # Final state: only 1 entry remains
    assert len(cache._extraction_failures) == 1
