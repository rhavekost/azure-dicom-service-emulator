"""
Unit tests for the expiry_cleanup_task background task in main.py.

The background task loops indefinitely, sleeping 1 hour between runs and
calling delete_expired_studies(). These tests mock the dependencies so no
real database or asyncio.sleep is needed.
"""

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_task():
    """Import expiry_cleanup_task fresh to pick up any module-level patches."""
    from main import expiry_cleanup_task

    return expiry_cleanup_task


# ---------------------------------------------------------------------------
# Task 16 tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expiry_cleanup_task_calls_delete_expired_studies():
    """
    expiry_cleanup_task must call delete_expired_studies after each sleep.

    Strategy: let the task run one iteration, then cancel it. We do this by
    making asyncio.sleep return immediately and delete_expired_studies raise
    CancelledError on the first invocation so the loop exits cleanly.
    """
    expiry_cleanup_task = _import_task()

    deleted_count = 5
    call_count = 0

    async def fake_delete(db, storage_dir):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return deleted_count
        # Let the test cancel the task after the first iteration
        raise asyncio.CancelledError

    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        patch("main.AsyncSessionLocal", return_value=mock_session_ctx),
        patch("main.delete_expired_studies", side_effect=fake_delete),
    ):
        with pytest.raises(asyncio.CancelledError):
            await expiry_cleanup_task()

    # sleep should have been called at least once with 3600 seconds
    mock_sleep.assert_called_with(3600)
    assert call_count >= 1


@pytest.mark.asyncio
async def test_expiry_cleanup_task_logs_when_studies_deleted(caplog):
    """expiry_cleanup_task logs the deleted count when studies are removed."""
    expiry_cleanup_task = _import_task()

    async def fake_delete(db, storage_dir):
        return 3  # 3 studies deleted

    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    iteration = 0

    async def fake_sleep(seconds):
        nonlocal iteration
        iteration += 1
        if iteration >= 2:
            raise asyncio.CancelledError

    with (
        patch("main.asyncio.sleep", side_effect=fake_sleep),
        patch("main.AsyncSessionLocal", return_value=mock_session_ctx),
        patch("main.delete_expired_studies", side_effect=fake_delete),
        caplog.at_level(logging.INFO, logger="main"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await expiry_cleanup_task()

    assert any("3" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_expiry_cleanup_task_no_log_when_zero_deleted(caplog):
    """expiry_cleanup_task does NOT log when zero studies are deleted."""
    expiry_cleanup_task = _import_task()

    async def fake_delete(db, storage_dir):
        return 0

    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    iteration = 0

    async def fake_sleep(seconds):
        nonlocal iteration
        iteration += 1
        if iteration >= 2:
            raise asyncio.CancelledError

    with (
        patch("main.asyncio.sleep", side_effect=fake_sleep),
        patch("main.AsyncSessionLocal", return_value=mock_session_ctx),
        patch("main.delete_expired_studies", side_effect=fake_delete),
        caplog.at_level(logging.INFO, logger="main"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await expiry_cleanup_task()

    # Should NOT have logged a deletion message
    deletion_logs = [r for r in caplog.records if "deleted" in r.message.lower()]
    assert len(deletion_logs) == 0


@pytest.mark.asyncio
async def test_expiry_cleanup_task_continues_after_error(caplog):
    """
    expiry_cleanup_task must swallow exceptions from delete_expired_studies
    and continue looping — a single failure must not kill the task.
    """
    expiry_cleanup_task = _import_task()

    call_count = 0

    async def fake_delete(db, storage_dir):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("DB connection lost")
        return 2

    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    iteration = 0

    async def fake_sleep(seconds):
        nonlocal iteration
        iteration += 1
        if iteration >= 3:
            raise asyncio.CancelledError

    with (
        patch("main.asyncio.sleep", side_effect=fake_sleep),
        patch("main.AsyncSessionLocal", return_value=mock_session_ctx),
        patch("main.delete_expired_studies", side_effect=fake_delete),
        caplog.at_level(logging.ERROR, logger="main"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await expiry_cleanup_task()

    # The task should have run at least twice (first errored, second succeeded)
    assert call_count >= 2

    # Error should have been logged
    error_logs = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_logs) >= 1
    assert any("DB connection lost" in r.message for r in error_logs)


@pytest.mark.asyncio
async def test_expiry_cleanup_task_sleep_precedes_work():
    """
    The task must sleep BEFORE doing any work (not after) — this matches the
    main.py implementation and prevents doing unnecessary work at startup.
    """
    expiry_cleanup_task = _import_task()

    events: list[str] = []

    async def fake_sleep(seconds):
        events.append("sleep")
        if len(events) >= 4:
            raise asyncio.CancelledError

    async def fake_delete(db, storage_dir):
        events.append("delete")
        return 0

    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("main.asyncio.sleep", side_effect=fake_sleep),
        patch("main.AsyncSessionLocal", return_value=mock_session_ctx),
        patch("main.delete_expired_studies", side_effect=fake_delete),
    ):
        with pytest.raises(asyncio.CancelledError):
            await expiry_cleanup_task()

    # sleep must appear before every delete in the event list
    assert events[0] == "sleep"
    for i, event in enumerate(events):
        if event == "delete":
            assert events[i - 1] == "sleep", f"delete at index {i} was not preceded by sleep"


@pytest.mark.asyncio
async def test_expiry_cleanup_task_sleep_interval_is_one_hour():
    """The cleanup interval must be exactly 3600 seconds (1 hour)."""
    expiry_cleanup_task = _import_task()

    sleep_args: list[int] = []

    async def fake_sleep(seconds):
        sleep_args.append(seconds)
        raise asyncio.CancelledError

    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("main.asyncio.sleep", side_effect=fake_sleep),
        patch("main.AsyncSessionLocal", return_value=mock_session_ctx),
        patch("main.delete_expired_studies", new_callable=AsyncMock, return_value=0),
    ):
        with pytest.raises(asyncio.CancelledError):
            await expiry_cleanup_task()

    assert sleep_args == [3600]


@pytest.mark.asyncio
async def test_expiry_cleanup_task_uses_dicom_storage_dir():
    """The task must pass the DICOM_STORAGE_DIR path to delete_expired_studies."""
    expiry_cleanup_task = _import_task()

    import main as main_module

    captured_storage_dir: list[Path] = []
    iteration = 0

    async def fake_sleep(seconds):
        nonlocal iteration
        iteration += 1
        # Let the first iteration complete so delete_expired_studies is called
        if iteration >= 2:
            raise asyncio.CancelledError

    async def fake_delete(db, storage_dir):
        captured_storage_dir.append(storage_dir)
        return 0

    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("main.asyncio.sleep", side_effect=fake_sleep),
        patch("main.AsyncSessionLocal", return_value=mock_session_ctx),
        patch("main.delete_expired_studies", side_effect=fake_delete),
    ):
        with pytest.raises(asyncio.CancelledError):
            await expiry_cleanup_task()

    assert len(captured_storage_dir) >= 1
    assert captured_storage_dir[0] == main_module.DICOM_STORAGE_DIR
