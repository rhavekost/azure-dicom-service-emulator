"""Outbox sweeper for ``change_feed`` rows that never had their event delivered.

The lightweight outbox lives on the ``change_feed.event_published_at`` column.
The STOW background publisher stamps the column on a successful
``publish_batch``.  A hard process kill between ``db.commit()`` and that
stamp leaves a window where the change-feed row exists but no event was
emitted.  :func:`sweep_unpublished_events` is run once at startup to
replay anything left behind from a previous boot — at-least-once delivery
on top of the existing in-memory fire-and-forget pipeline.

Deliberately stateless: each sweep iteration loads up to
:data:`OUTBOX_SWEEP_PAGE_SIZE` rows ordered by ``sequence`` and skips
anything older than :data:`OUTBOX_SWEEP_MAX_AGE_SECONDS` (rows that age
out are assumed to have been backfilled via the change-feed API).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy import update as sql_update

from app.config import OUTBOX_SWEEP_MAX_AGE_SECONDS, OUTBOX_SWEEP_PAGE_SIZE
from app.database import outbox_session_factory
from app.models.dicom import ChangeFeedEntry
from app.models.events import DicomEvent
from app.services.events.manager import EventManager

logger = logging.getLogger(__name__)


async def sweep_unpublished_events(
    event_manager: EventManager,
    *,
    service_url: str = "",
    page_size: int = OUTBOX_SWEEP_PAGE_SIZE,
    max_age_seconds: int = OUTBOX_SWEEP_MAX_AGE_SECONDS,
) -> int:
    """Replay any ``change_feed`` rows that haven't been event-published.

    Pages through the ``change_feed`` table looking for rows where
    ``event_published_at IS NULL`` and the row is younger than
    ``max_age_seconds``.  Each page is republished as a single
    :meth:`EventManager.publish_batch` call, then the column is stamped
    so subsequent sweeps skip the row.

    The loop exits when the partial-index scan returns no eligible rows
    or when an unrecoverable error occurs (logged, not raised).  Running
    this on app startup means a process restart will re-emit anything
    that was lost in flight — consumers must already be idempotent on
    the ``(study, series, sop)`` triple to handle that.

    Returns the number of rows successfully replayed (best-effort
    informational; a partial replay still returns the count it managed
    before stopping).
    """
    if page_size <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    total_replayed = 0

    while True:
        async with outbox_session_factory() as session:
            result = await session.execute(
                select(ChangeFeedEntry)
                .where(
                    ChangeFeedEntry.event_published_at.is_(None),
                    ChangeFeedEntry.timestamp >= cutoff,
                )
                .order_by(ChangeFeedEntry.sequence)
                .limit(page_size)
            )
            rows: list[ChangeFeedEntry] = list(result.scalars().all())
            if not rows:
                break

            events: list[DicomEvent] = []
            sequences: list[int] = []
            for row in rows:
                if row.action == "delete":
                    event = DicomEvent.from_instance_deleted(
                        study_uid=row.study_instance_uid,
                        series_uid=row.series_instance_uid,
                        instance_uid=row.sop_instance_uid,
                        sequence_number=row.sequence,
                        service_url=service_url,
                    )
                else:
                    event = DicomEvent.from_instance_created(
                        study_uid=row.study_instance_uid,
                        series_uid=row.series_instance_uid,
                        instance_uid=row.sop_instance_uid,
                        sequence_number=row.sequence,
                        service_url=service_url,
                    )
                events.append(event)
                sequences.append(row.sequence)

            try:
                await event_manager.publish_batch(events)
            except Exception as e:
                logger.warning(
                    "Outbox sweeper: publish_batch failed for %d row(s); will retry next boot: %s",
                    len(events),
                    e,
                )
                # Don't stamp; leave the rows for the next sweep / boot.
                break

            now = datetime.now(timezone.utc)
            await session.execute(
                sql_update(ChangeFeedEntry)
                .where(ChangeFeedEntry.sequence.in_(sequences))
                .values(event_published_at=now)
            )
            await session.commit()
            total_replayed += len(rows)

            # Defensive yield so a long replay doesn't pin the event
            # loop — useful when the sweeper races with the first wave
            # of incoming requests on cold start.
            await asyncio.sleep(0)

    if total_replayed:
        logger.info("Outbox sweeper replayed %d unpublished change_feed row(s)", total_replayed)
    return total_replayed
