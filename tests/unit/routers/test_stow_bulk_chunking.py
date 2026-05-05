"""Unit tests for the bulk-INSERT chunking in :mod:`app.database` and the
multi-row INSERT call sites in :mod:`app.routers.stow`.

PostgreSQL caps a single statement at 32767 bind parameters; asyncpg
surfaces overruns as ``InterfaceError: the number of query arguments
cannot exceed 32767``.  STOW requests with many instances fan out one
row's worth of bind params per ``VALUES (...)`` tuple, so the bulk
INSERT must be split into chunks small enough to stay under the cap.

These tests exercise the shared helper that computes the chunk size and
verify that the multi-row INSERT paths actually issue multiple
statements when a batch exceeds one chunk.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import database as db_module
from app.routers import stow as stow_module
from tests.fixtures.factories import DicomFactory
from tests.integration.test_stow_rs_success import build_multipart_request

pytestmark = pytest.mark.unit


# ── bulk_chunk_size helper ─────────────────────────────────────────


def test_chunk_size_fits_under_pg_cap_for_dicom_instance_row():
    """22-column rows should produce a chunk size that fits under 32767 - headroom."""
    rows = [{f"c{i}": i for i in range(22)}]
    chunk = db_module.bulk_chunk_size(rows)
    assert chunk * 22 <= db_module.PG_MAX_BIND_PARAMS - db_module.PG_BIND_HEADROOM
    # Sanity: with 22 cols the per-statement budget should still hold ~1480 rows.
    assert chunk >= 1000


def test_chunk_size_fits_under_pg_cap_for_change_feed_row():
    """7-column rows allow a much larger chunk than 22-column rows."""
    rows = [{f"c{i}": i for i in range(7)}]
    chunk = db_module.bulk_chunk_size(rows)
    assert chunk * 7 <= db_module.PG_MAX_BIND_PARAMS - db_module.PG_BIND_HEADROOM
    assert chunk >= 4000


def test_chunk_size_empty_rows_returns_one():
    """An empty rows list shouldn't divide-by-zero — return a sentinel of 1."""
    assert db_module.bulk_chunk_size([]) == 1


def test_chunk_size_pathological_zero_columns_does_not_divide_by_zero():
    """A row dict with zero entries must not crash the chunk-size math."""
    chunk = db_module.bulk_chunk_size([{}])
    assert chunk >= 1


# ── End-to-end chunking via STOW POST ──────────────────────────────


def _spy_dicom_instance_inserts(monkeypatch) -> list[str]:
    """Wrap ``AsyncSession.execute`` to count ``dicom_instances`` INSERTs.

    Returns a list that the test asserts against — its length equals the
    number of INSERT statements the patched ``execute`` observed against
    the ``dicom_instances`` table.  Other traffic (SELECTs, change_feed
    INSERTs, UPDATEs) is filtered out so the count cleanly reflects
    chunking on the path under test.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    insert_calls: list[str] = []
    original_execute = AsyncSession.execute

    async def spy_execute(self, statement, *args, **kwargs):
        sql = str(statement)
        if sql.lstrip().lower().startswith("insert into dicom_instances"):
            insert_calls.append(sql)
        return await original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", spy_execute)
    return insert_calls


def test_bulk_insert_post_chunks_when_param_cap_is_tight(
    client: TestClient, monkeypatch
):
    """Lower the bind-param cap so a small N=8 batch must chunk.

    With ``_PG_MAX_BIND_PARAMS`` set so the chunk can hold only 2 rows,
    an 8-row POST should issue 4 INSERT statements into
    ``dicom_instances`` rather than one.  All 8 instances must still
    land in the response so chunking doesn't drop survivors.
    """
    # 22 cols × 2 rows = 44 params; leave 200 headroom so cap = 244.
    monkeypatch.setattr(db_module, "PG_MAX_BIND_PARAMS", 244)
    monkeypatch.setattr(db_module, "PG_BIND_HEADROOM", 200)
    # Sanity: helper now caps at 2 rows per chunk for dicom_instances.
    assert db_module.bulk_chunk_size([{f"c{i}": i for i in range(22)}]) == 2

    insert_calls = _spy_dicom_instance_inserts(monkeypatch)

    dcms = [
        DicomFactory.create_ct_image(patient_id=f"CHUNK-{i:02d}")
        for i in range(8)
    ]
    body, content_type = build_multipart_request(dcms)
    response = client.post(
        "/v2/studies", content=body, headers={"Content-Type": content_type}
    )

    assert response.status_code == 200, response.text
    referenced = response.json()["00081199"]["Value"]
    assert len(referenced) == 8
    # Eight rows split into chunks of two ⇒ four bulk INSERT statements.
    assert len(insert_calls) == 4, (
        f"expected 4 chunked INSERTs, got {len(insert_calls)}: "
        f"{[s[:80] for s in insert_calls]}"
    )


def test_bulk_insert_post_single_chunk_when_batch_fits(
    client: TestClient, monkeypatch
):
    """At the default cap a small batch should issue a single INSERT.

    Regression guard so we don't accidentally chunk small batches —
    the chunked loop must collapse to one iteration when the whole batch
    fits inside one ``VALUES (...)`` block.
    """
    insert_calls = _spy_dicom_instance_inserts(monkeypatch)

    dcms = [
        DicomFactory.create_ct_image(patient_id=f"NOCHUNK-{i:02d}")
        for i in range(3)
    ]
    body, content_type = build_multipart_request(dcms)
    response = client.post(
        "/v2/studies", content=body, headers={"Content-Type": content_type}
    )

    assert response.status_code == 200, response.text
    assert len(insert_calls) == 1, (
        f"expected 1 INSERT for a 3-row batch under default cap, got "
        f"{len(insert_calls)}"
    )
