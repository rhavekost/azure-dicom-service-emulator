"""Unit tests for the STOW streaming + executor dispatch wiring.

These tests target the orchestration layer in :mod:`app.routers.stow`
that sits between the streaming multipart parser, the parse pool, and
the per-part DB persistence helper.  They use the inline (no
subprocess) parse path so they don't have to spin up a process pool.
"""

from __future__ import annotations

import pytest

from app.routers.stow import _gather_parts_with_records
from app.services.dicom_parse_worker import ParsedPartRecord
from app.services.multipart import MultipartPart
from tests.fixtures.factories import DicomFactory

pytestmark = pytest.mark.unit


def _make_part(dicom_bytes: bytes) -> MultipartPart:
    return MultipartPart(content_type="application/dicom", data=dicom_bytes)


@pytest.mark.asyncio
async def test_gather_parts_dispatches_inline_when_pool_is_none():
    """With pool=None, parsing falls back to the default thread executor.

    This is the path unit tests rely on so they don't have to spin up
    subprocesses.  The records returned must still have ``ok=True``
    and the right UIDs.
    """
    dicom_bytes = DicomFactory.create_ct_image(patient_id="DISP-001")
    parts = [_make_part(dicom_bytes)]

    results = await _gather_parts_with_records(parts, pool=None)

    assert len(results) == 1
    raw_bytes, record = results[0]
    assert raw_bytes == dicom_bytes
    assert isinstance(record, ParsedPartRecord)
    assert record.ok is True
    assert record.sop_uid


@pytest.mark.asyncio
async def test_gather_parts_preserves_ordering():
    """Records come back in the same order parts were yielded.

    Parts are dispatched concurrently to the pool, but
    ``asyncio.gather`` preserves the input ordering of the futures —
    important because the response ``ReferencedSOPSequence`` reflects
    the order parts arrived on the wire.
    """
    parts = [
        _make_part(
            DicomFactory.create_ct_image(
                patient_id=f"DISP-{i:03d}",
                # unique UIDs per part so we can verify ordering
                study_uid=f"1.2.3.{i}",
                series_uid=f"1.2.3.{i}.1",
                sop_uid=f"1.2.3.{i}.1.1",
            )
        )
        for i in range(8)
    ]

    results = await _gather_parts_with_records(parts, pool=None)

    sop_uids = [record.sop_uid for _, record in results]
    assert sop_uids == [f"1.2.3.{i}.1.1" for i in range(8)]


@pytest.mark.asyncio
async def test_gather_parts_handles_empty_source():
    """No parts in → no records out, no exception."""
    results = await _gather_parts_with_records([], pool=None)
    assert results == []


@pytest.mark.asyncio
async def test_gather_parts_accepts_async_iterator():
    """The streaming parser yields parts via async iteration.

    The dispatcher must accept that source shape too — not just a list.
    """

    async def _aiter():
        yield _make_part(DicomFactory.create_ct_image(patient_id="ASYNC-001"))
        yield _make_part(DicomFactory.create_ct_image(patient_id="ASYNC-002"))

    results = await _gather_parts_with_records(_aiter(), pool=None)

    assert len(results) == 2
    assert all(record.ok for _, record in results)
