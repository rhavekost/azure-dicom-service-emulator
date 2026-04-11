"""
Shared helpers used across multiple DICOMweb router modules.

Contains:
- JSON serialisation helper
- ETag computation
- Storage path and frame cache singleton
- _mark_previous_feed_entries DB helper
- _publish_change_event best-effort event publishing helper
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DICOM_STORAGE_DIR as _DICOM_STORAGE_DIR_STR
from app.dependencies import get_event_manager
from app.models.dicom import ChangeFeedEntry
from app.models.events import DicomEvent
from app.services.frame_cache import FrameCache

logger = logging.getLogger(__name__)

# ── Storage path and frame cache ──────────────────────────────────
DICOM_STORAGE_DIR = Path(_DICOM_STORAGE_DIR_STR)
frame_cache = FrameCache(DICOM_STORAGE_DIR)


# ── JSON serialisation ─────────────────────────────────────────────


def _json_dumps(obj) -> bytes:
    """JSON serialize with support for UUID and datetime."""

    class Encoder(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, uuid.UUID):
                return str(o)
            if isinstance(o, datetime):
                return o.isoformat()
            return super().default(o)

    return json.dumps(obj, cls=Encoder).encode("utf-8")


# ── ETag computation ───────────────────────────────────────────────


def _compute_etag(content: bytes) -> str:
    """Return a quoted SHA-256 ETag for *content* (RFC 7232 strong ETag)."""
    digest = hashlib.sha256(content).hexdigest()
    return f'"{digest}"'


# ── Change feed helpers ────────────────────────────────────────────


async def _mark_previous_feed_entries(db: AsyncSession, sop_uid: str):
    """Mark previous change feed entries as replaced/deleted."""
    prev_entries = await db.execute(
        select(ChangeFeedEntry)
        .where(
            and_(
                ChangeFeedEntry.sop_instance_uid == sop_uid,
                ChangeFeedEntry.state == "current",
            )
        )
        .order_by(ChangeFeedEntry.sequence.desc())
        .offset(1)
    )
    for entry in prev_entries.scalars().all():
        entry.state = "replaced"


# ── Event publishing ───────────────────────────────────────────────


async def _publish_change_event(event: DicomEvent, instance_uid: str) -> None:
    """Publish a single DICOM change event (best-effort).

    Errors are logged but never re-raised so that a DICOM operation already
    committed to the database is never rolled back due to an event-publishing
    failure.

    Args:
        event: The pre-built :class:`DicomEvent` to publish.
        instance_uid: SOP Instance UID used only for the error log message.
    """
    try:
        event_manager = get_event_manager()
        await event_manager.publish(event)
    except Exception as e:
        logger.error("Failed to publish event for instance %s: %s", instance_uid, e)
