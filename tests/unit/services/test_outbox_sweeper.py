"""Unit tests for the change_feed outbox sweeper.

Verifies that :func:`sweep_unpublished_events` correctly:

* republishes ``change_feed`` rows whose ``event_published_at`` is NULL,
* stamps those rows after a successful ``publish_batch`` call,
* skips rows older than the configured max-age,
* tolerates ``publish_batch`` failures (rows stay unstamped, sweep stops),
* dispatches ``deleted`` events when a feed row's action is ``"delete"``.

The sweeper opens its own ``AsyncSession`` via ``outbox_session_factory``
so the test patches the factory to point at an in-memory SQLite engine
shared with the test setup.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.dicom import ChangeFeedEntry
from app.services.events import outbox as outbox_module
from app.services.events.providers import InMemoryEventProvider

pytestmark = pytest.mark.unit


class _StubEventManager:
    """Minimal EventManager stand-in that captures published batches."""

    def __init__(self, *, fail: bool = False) -> None:
        self.batches: list[list] = []
        self.fail = fail

    async def publish_batch(self, events) -> None:
        if self.fail:
            raise RuntimeError("simulated publish failure")
        self.batches.append(list(events))


@pytest.fixture
async def patched_outbox_factory(tmp_path, monkeypatch):
    """Patch the outbox module's ``outbox_session_factory`` to a temp DB.

    Yields the session factory so individual tests can seed
    ``change_feed`` rows directly before invoking the sweeper.
    """
    db_path = tmp_path / "outbox_test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setattr(outbox_module, "outbox_session_factory", factory)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed_feed_entries(
    factory,
    *,
    rows: list[dict],
) -> None:
    """Insert raw ``change_feed`` rows for a sweep test."""
    async with factory() as session:
        for row in rows:
            session.add(ChangeFeedEntry(**row))
        await session.commit()


@pytest.mark.asyncio
async def test_sweeper_replays_and_stamps_unpublished_rows(patched_outbox_factory):
    """Unpublished change_feed rows are dispatched and stamped."""
    now = datetime.now(timezone.utc)
    await _seed_feed_entries(
        patched_outbox_factory,
        rows=[
            {
                "study_instance_uid": "1.2.3",
                "series_instance_uid": "1.2.3.4",
                "sop_instance_uid": "1.2.3.4.5",
                "action": "create",
                "state": "current",
                "timestamp": now,
                "event_published_at": None,
            },
            {
                "study_instance_uid": "1.2.3",
                "series_instance_uid": "1.2.3.4",
                "sop_instance_uid": "1.2.3.4.6",
                "action": "create",
                "state": "current",
                "timestamp": now,
                "event_published_at": None,
            },
        ],
    )

    manager = _StubEventManager()
    replayed = await outbox_module.sweep_unpublished_events(manager, page_size=10)

    assert replayed == 2
    assert len(manager.batches) == 1
    assert len(manager.batches[0]) == 2

    async with patched_outbox_factory() as session:
        rows = (await session.execute(select(ChangeFeedEntry))).scalars().all()
    assert all(r.event_published_at is not None for r in rows)


@pytest.mark.asyncio
async def test_sweeper_skips_already_stamped_rows(patched_outbox_factory):
    """Rows whose event_published_at is already set are not republished."""
    now = datetime.now(timezone.utc)
    await _seed_feed_entries(
        patched_outbox_factory,
        rows=[
            {
                "study_instance_uid": "9.9",
                "series_instance_uid": "9.9.9",
                "sop_instance_uid": "9.9.9.9",
                "action": "create",
                "state": "current",
                "timestamp": now,
                "event_published_at": now,
            },
        ],
    )

    manager = _StubEventManager()
    replayed = await outbox_module.sweep_unpublished_events(manager, page_size=10)

    assert replayed == 0
    assert manager.batches == []


@pytest.mark.asyncio
async def test_sweeper_respects_max_age_window(patched_outbox_factory):
    """Rows older than max_age_seconds are skipped — assumed already backfilled."""
    now = datetime.now(timezone.utc)
    old_timestamp = now - timedelta(days=30)
    await _seed_feed_entries(
        patched_outbox_factory,
        rows=[
            {
                "study_instance_uid": "stale.1",
                "series_instance_uid": "stale.1.1",
                "sop_instance_uid": "stale.1.1.1",
                "action": "create",
                "state": "current",
                "timestamp": old_timestamp,
                "event_published_at": None,
            },
        ],
    )

    manager = _StubEventManager()
    replayed = await outbox_module.sweep_unpublished_events(
        manager, page_size=10, max_age_seconds=3600
    )

    assert replayed == 0
    assert manager.batches == []


@pytest.mark.asyncio
async def test_sweeper_leaves_rows_unstamped_on_publish_failure(patched_outbox_factory):
    """A failing publish_batch must leave the rows untouched for the next boot."""
    now = datetime.now(timezone.utc)
    await _seed_feed_entries(
        patched_outbox_factory,
        rows=[
            {
                "study_instance_uid": "fail.1",
                "series_instance_uid": "fail.1.1",
                "sop_instance_uid": "fail.1.1.1",
                "action": "create",
                "state": "current",
                "timestamp": now,
                "event_published_at": None,
            },
        ],
    )

    manager = _StubEventManager(fail=True)
    replayed = await outbox_module.sweep_unpublished_events(manager, page_size=10)

    assert replayed == 0

    async with patched_outbox_factory() as session:
        rows = (await session.execute(select(ChangeFeedEntry))).scalars().all()
    # The single seeded row must still be unstamped.
    assert all(r.event_published_at is None for r in rows)


@pytest.mark.asyncio
async def test_sweeper_dispatches_deleted_events(patched_outbox_factory):
    """A change_feed row with action=delete fires the DicomImageDeleted event."""
    now = datetime.now(timezone.utc)
    await _seed_feed_entries(
        patched_outbox_factory,
        rows=[
            {
                "study_instance_uid": "del.1",
                "series_instance_uid": "del.1.1",
                "sop_instance_uid": "del.1.1.1",
                "action": "delete",
                "state": "deleted",
                "timestamp": now,
                "event_published_at": None,
            },
        ],
    )

    manager = _StubEventManager()
    replayed = await outbox_module.sweep_unpublished_events(manager, page_size=10)

    assert replayed == 1
    assert len(manager.batches) == 1
    [event] = manager.batches[0]
    assert event.event_type.endswith("DicomImageDeleted")


@pytest.mark.asyncio
async def test_sweeper_pages_until_partial_index_empty(patched_outbox_factory):
    """The sweep loops until no more eligible rows remain, regardless of page_size."""
    now = datetime.now(timezone.utc)
    rows = [
        {
            "study_instance_uid": "page.1",
            "series_instance_uid": "page.1.1",
            "sop_instance_uid": f"page.1.1.{i}",
            "action": "create",
            "state": "current",
            "timestamp": now,
            "event_published_at": None,
        }
        for i in range(7)
    ]
    await _seed_feed_entries(patched_outbox_factory, rows=rows)

    manager = _StubEventManager()
    replayed = await outbox_module.sweep_unpublished_events(manager, page_size=3)

    assert replayed == 7
    # 7 rows / 3 per page = 3 batches (3, 3, 1).
    assert [len(b) for b in manager.batches] == [3, 3, 1]


@pytest.mark.asyncio
async def test_sweeper_with_in_memory_provider_round_trip(patched_outbox_factory):
    """End-to-end: real EventManager + InMemoryProvider receives the events."""
    from app.services.events.manager import EventManager

    provider = InMemoryEventProvider()
    manager = EventManager([provider])

    now = datetime.now(timezone.utc)
    await _seed_feed_entries(
        patched_outbox_factory,
        rows=[
            {
                "study_instance_uid": "rt.1",
                "series_instance_uid": "rt.1.1",
                "sop_instance_uid": "rt.1.1.1",
                "action": "create",
                "state": "current",
                "timestamp": now,
                "event_published_at": None,
            },
        ],
    )

    replayed = await outbox_module.sweep_unpublished_events(manager, page_size=10)

    assert replayed == 1
    events = provider.get_events()
    assert len(events) == 1
    assert events[0].subject.endswith("rt.1.1.1")
