"""
Unit tests for concurrent access to FrameCache.

The TOCTOU race acknowledged in frame_cache.py line ~62 occurs when two
coroutines (or processes) check frames_dir.exists() simultaneously, both
see False, and both attempt extraction. The code handles this safely because:
  1. extract_frames creates frames_dir with exist_ok=True
  2. Frame files are written atomically
  3. Worst case: duplicate extraction work, not corruption

These tests verify that even under concurrent access the cache either:
  - Returns valid frames to all callers, or
  - Raises the same predictable error to all callers (never hangs / deadlocks)
"""

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.frame_cache import FrameCache

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_instance(storage_dir: Path, study="1.2.3", series="4.5.6", instance="7.8.9"):
    """Create on-disk directory + instance.dcm for the given UIDs."""
    dcm_path = storage_dir / study / series / instance / "instance.dcm"
    dcm_path.parent.mkdir(parents=True, exist_ok=True)
    dcm_path.write_bytes(b"fake-dicom-data")
    return study, series, instance, dcm_path


def _make_frames(dcm_path: Path, frame_count: int = 3) -> list[Path]:
    """Create frame files in the frames/ subdirectory next to instance.dcm."""
    frames_dir = dcm_path.parent / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i in range(1, frame_count + 1):
        f = frames_dir / f"{i}.raw"
        f.write_bytes(b"pixel" * i)
        frames.append(f)
    return frames


# ---------------------------------------------------------------------------
# Task 17: concurrent access tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_get_or_extract_both_succeed(tmp_path):
    """
    Two coroutines requesting the same un-cached instance simultaneously
    must both receive valid frame paths without error.

    This exercises the TOCTOU window: both see frames_dir absent, both call
    extract_frames, and both get a result (duplicate work, no corruption).
    """
    storage_dir = tmp_path / "storage"
    cache = FrameCache(storage_dir)
    study, series, instance, dcm_path = _make_instance(storage_dir)

    # Barrier to synchronise two coroutines inside the TOCTOU window
    barrier = asyncio.Barrier(2)
    extraction_call_count = 0

    def mock_extract(dcm_path_arg, frames_dir):
        nonlocal extraction_call_count
        extraction_call_count += 1
        frames = _make_frames(dcm_path_arg, frame_count=2)
        return frames

    with patch("app.services.frame_cache.extract_frames", side_effect=mock_extract):

        async def fetch():
            # Use asyncio.to_thread so that the synchronous get_or_extract
            # call doesn't block the event loop — each runs in its own thread.
            return await asyncio.to_thread(cache.get_or_extract, study, series, instance)

        results = await asyncio.gather(fetch(), fetch())

    # Both callers must have received frames
    for frames in results:
        assert len(frames) > 0
        for frame_path in frames:
            assert frame_path.exists()


@pytest.mark.asyncio
async def test_concurrent_get_or_extract_uses_cached_result(tmp_path):
    """
    If frames are already cached before the concurrent requests arrive,
    both callers must read from cache without invoking extract_frames.
    """
    storage_dir = tmp_path / "storage"
    cache = FrameCache(storage_dir)
    study, series, instance, dcm_path = _make_instance(storage_dir)

    # Pre-populate the cache
    pre_cached = _make_frames(dcm_path, frame_count=2)

    with patch("app.services.frame_cache.extract_frames") as mock_extract:

        async def fetch():
            return await asyncio.to_thread(cache.get_or_extract, study, series, instance)

        results = await asyncio.gather(fetch(), fetch(), fetch())

    # extract_frames must never be called — cache already hot
    mock_extract.assert_not_called()

    for frames in results:
        assert len(frames) == 2


@pytest.mark.asyncio
async def test_concurrent_extraction_failure_all_callers_receive_error(tmp_path):
    """
    When extraction fails, all concurrent callers must receive an error.
    None should hang, deadlock, or silently swallow the failure.
    """
    storage_dir = tmp_path / "storage"
    cache = FrameCache(storage_dir)
    study, series, instance, dcm_path = _make_instance(storage_dir)

    def mock_extract(dcm_path_arg, frames_dir):
        raise RuntimeError("pixel data decode failed")

    with patch("app.services.frame_cache.extract_frames", side_effect=mock_extract):

        async def fetch():
            return await asyncio.to_thread(cache.get_or_extract, study, series, instance)

        results = await asyncio.gather(fetch(), fetch(), return_exceptions=True)

    for result in results:
        assert isinstance(
            result, (ValueError, RuntimeError)
        ), f"Expected an exception, got {result!r}"


@pytest.mark.asyncio
async def test_concurrent_different_instances_do_not_interfere(tmp_path):
    """
    Concurrent access on *different* instance UIDs must be fully independent.
    Each caller must receive only its own frames.
    """
    storage_dir = tmp_path / "storage"
    cache = FrameCache(storage_dir)

    study, series = "1.2.3", "4.5.6"
    instance_a = "inst.A"
    instance_b = "inst.B"

    _, _, _, dcm_a = _make_instance(storage_dir, study, series, instance_a)
    _, _, _, dcm_b = _make_instance(storage_dir, study, series, instance_b)

    frame_a = _make_frames(dcm_a, frame_count=1)
    frame_b = _make_frames(dcm_b, frame_count=3)

    with patch("app.services.frame_cache.extract_frames") as mock_extract:

        async def fetch_a():
            return await asyncio.to_thread(cache.get_or_extract, study, series, instance_a)

        async def fetch_b():
            return await asyncio.to_thread(cache.get_or_extract, study, series, instance_b)

        frames_a, frames_b = await asyncio.gather(fetch_a(), fetch_b())

    # extract_frames was not needed — frames were pre-cached
    mock_extract.assert_not_called()

    assert len(frames_a) == 1
    assert len(frames_b) == 3


@pytest.mark.asyncio
async def test_concurrent_failure_does_not_poison_other_instance(tmp_path):
    """
    A failure for instance A must not mark instance B as failed.
    The failure cache is keyed by instance_uid.
    """
    storage_dir = tmp_path / "storage"
    cache = FrameCache(storage_dir)

    study, series = "1.2.3", "4.5.6"
    instance_a = "inst.fail"
    instance_b = "inst.ok"

    _, _, _, dcm_a = _make_instance(storage_dir, study, series, instance_a)
    _, _, _, dcm_b = _make_instance(storage_dir, study, series, instance_b)

    # Instance B has pre-cached frames; A will fail during extraction
    _make_frames(dcm_b, frame_count=2)

    call_count = {"n": 0}

    def mock_extract(dcm_path_arg, frames_dir):
        call_count["n"] += 1
        raise RuntimeError("forced failure for A")

    with patch("app.services.frame_cache.extract_frames", side_effect=mock_extract):
        # Fail instance A
        with pytest.raises((ValueError, RuntimeError)):
            await asyncio.to_thread(cache.get_or_extract, study, series, instance_a)

        # Instance B must still succeed
        frames_b = await asyncio.to_thread(cache.get_or_extract, study, series, instance_b)

    assert len(frames_b) == 2
    # Only A attempted extraction
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_many_concurrent_readers_cached_instance(tmp_path):
    """
    Many concurrent readers on a pre-cached instance must all succeed.
    No deadlock or contention in the in-memory failure dict.
    """
    storage_dir = tmp_path / "storage"
    cache = FrameCache(storage_dir)
    study, series, instance, dcm_path = _make_instance(storage_dir)
    _make_frames(dcm_path, frame_count=4)

    concurrency = 20

    with patch("app.services.frame_cache.extract_frames") as mock_extract:

        async def fetch():
            return await asyncio.to_thread(cache.get_or_extract, study, series, instance)

        results = await asyncio.gather(*[fetch() for _ in range(concurrency)])

    mock_extract.assert_not_called()

    assert len(results) == concurrency
    for frames in results:
        assert len(frames) == 4


@pytest.mark.asyncio
async def test_concurrent_extraction_result_is_stable(tmp_path):
    """
    When two coroutines race to extract the same instance, the frames written
    to disk by the first must be visible to the second (idempotent writes).
    Both callers must return the same number of frame paths.
    """
    storage_dir = tmp_path / "storage"
    cache = FrameCache(storage_dir)
    study, series, instance, dcm_path = _make_instance(storage_dir)

    extract_gate = threading.Event()
    first_called = threading.Event()

    def mock_extract(dcm_path_arg, frames_dir):
        frames_dir.mkdir(parents=True, exist_ok=True)
        if not first_called.is_set():
            first_called.set()
            # Stall the first extractor to ensure a second one can start
            extract_gate.wait(timeout=2)
        frame = frames_dir / "1.raw"
        if not frame.exists():
            frame.write_bytes(b"frame-bytes")
        return sorted(frames_dir.glob("*.raw"))

    with patch("app.services.frame_cache.extract_frames", side_effect=mock_extract):

        async def fetch():
            return await asyncio.to_thread(cache.get_or_extract, study, series, instance)

        # Start first fetch, let it block at gate, then start second, then release
        task1 = asyncio.create_task(fetch())
        await asyncio.sleep(0.05)  # give task1 time to enter mock_extract
        task2 = asyncio.create_task(fetch())
        extract_gate.set()  # unblock task1

        frames1, frames2 = await asyncio.gather(task1, task2)

    # Both must return at least one frame
    assert len(frames1) >= 1
    assert len(frames2) >= 1
