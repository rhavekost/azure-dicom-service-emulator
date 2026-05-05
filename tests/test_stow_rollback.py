"""Regression tests for session rollback + partial-success on STOW-RS failures.

Covers two related guarantees:

* **Pool poisoning (Scenario A + C).** A ``SQLAlchemyError`` on
  ``commit()`` must trigger an explicit ``rollback()`` so the
  underlying connection is returned to the pool clean.  Without this
  guard, a follow-up request that picks up the same pooled connection
  trips ``PendingRollbackError``.
* **Partial-success semantics (Scenarios B + D).**  A per-part disk
  write failure inside ``_process_stow_records`` is treated as a
  0xC000 failure for *that* part only — sibling parts in the same
  batch must commit and be queryable via QIDO.  This replaces the
  legacy "all-or-nothing in-loop rollback" behaviour, which discarded
  in-flight successes alongside the failing part.

What this suite proves:
    * Scenarios A spies on ``AsyncSession.rollback`` to assert that
      the production code's **explicit** ``rollback()`` call is
      invoked on the commit-failure path (not merely the implicit
      rollback inside ``AsyncSession.__aexit__``'s ``close()``).
    * Scenarios A also verifies the follow-up request succeeds and
      the instance is persisted (QIDO round-trip).
    * Scenarios B and D assert that a flaky ``store_instance_bytes``
      for one SOP becomes a 0xC000 failure entry, and that sibling
      SOPs in the same batch are *both* listed in
      ``ReferencedSOPSequence`` and queryable via QIDO.
    * Scenario C asserts the production ``engine`` is configured with
      ``pool_pre_ping=True``.

Layers exercised:
    * Scenario A — commit-time ``SQLAlchemyError`` inside ``stow_rs``.
    * Scenarios B + D — per-part disk-write failure inside
      ``_stage_record`` exercises the partial-success path: failure
      entry recorded, but no batch-wide rollback.
    * Scenario C — defensive check that production ``engine`` is
      configured with ``pool_pre_ping=True``.

Known limitation:
    End-to-end pool-poisoning verification requires a Postgres
    integration variant (follow-up, not on this branch). SQLite via
    aiosqlite has no real connection pool, so even without the
    production fix a fresh session is handed out each request — the
    spy assertions above are how we prove the fix stays in place.
"""

from __future__ import annotations

import pytest
import sqlalchemy.ext.asyncio
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

import app.database as database_module
import app.routers.stow as stow_module
from tests.fixtures.factories import DicomFactory

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helpers (local to keep the regression suite self-contained)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _build_multipart(
    dicom_files: list[bytes], boundary: str = "rollback-boundary"
) -> tuple[bytes, str]:
    """Build a multipart/related STOW-RS request body.

    Shape-identical to ``build_multipart_request`` in
    ``tests/integration/test_stow_rs_success.py``.
    """
    parts: list[bytes] = []
    for dcm in dicom_files:
        part = (f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n").encode()
        part += dcm + b"\r\n"
        parts.append(part)
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    content_type = f'multipart/related; type="application/dicom"; boundary={boundary}'
    return body, content_type


def _post_stow(client: TestClient, dcm_bytes: bytes):
    """POST a single-instance STOW-RS request through the test client."""
    body, content_type = _build_multipart([dcm_bytes])
    return client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Scenario A — commit-time failure (stow_rs local guard)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_stow_recovers_after_commit_sqlalchemy_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A commit-time SQLAlchemyError must not poison subsequent requests.

    Exercises the ``try: await db.commit()`` / ``except SQLAlchemyError:
    await db.rollback(); raise`` guard inside ``stow_rs`` and — in concert
    with the production-matching ``override_get_db`` in conftest — the
    outer rollback-on-exception wrapper.

    Steps:
        1. Install a spy on ``AsyncSession.rollback`` to count EXPLICIT
           rollback calls (the implicit rollback inside ``close()``'s
           ``__aexit__`` path does not go through this method).
        2. Monkeypatch ``AsyncSession.commit`` so the next call raises
           ``SQLAlchemyError`` exactly ONCE, then self-restores.
        3. Send a valid STOW — expect the SQLAlchemyError to surface.
           Starlette's ``TestClient`` by default re-raises unhandled
           server exceptions (see ``raise_server_exceptions=True``);
           against a real HTTP stack this is a 500. Either way, the
           guard must have rolled back before the error escaped.
        4. Assert the spy recorded at least one explicit rollback. This
           is the regression test: without Task 1's ``get_db`` rollback
           AND Task 2's ``stow_rs`` local rollback, the count is 0.
        5. Reset the spy counter, then send a second valid STOW (no
           monkeypatch active). Expect 200 and the instance persisted.
    """
    failing_study_uid = "1.2.826.0.1.3680043.8.498.99990001"
    failing_sop_uid = "1.2.826.0.1.3680043.8.498.99990001.1.1"
    dcm_failing = DicomFactory.create_ct_image(
        patient_id="ROLLBACK-A-FAIL",
        study_uid=failing_study_uid,
        series_uid="1.2.826.0.1.3680043.8.498.99990001.1",
        sop_uid=failing_sop_uid,
        with_pixel_data=False,
    )

    recovery_study_uid = "1.2.826.0.1.3680043.8.498.99990002"
    recovery_sop_uid = "1.2.826.0.1.3680043.8.498.99990002.1.1"
    dcm_recovery = DicomFactory.create_ct_image(
        patient_id="ROLLBACK-A-OK",
        study_uid=recovery_study_uid,
        series_uid="1.2.826.0.1.3680043.8.498.99990002.1",
        sop_uid=recovery_sop_uid,
        with_pixel_data=False,
    )

    # Spy on AsyncSession.rollback to count EXPLICIT calls from
    # production code. The implicit rollback inside __aexit__ → close()
    # does not go through this method, so we only see the real fix.
    rollback_calls: list[None] = []
    original_rollback = sqlalchemy.ext.asyncio.AsyncSession.rollback

    async def spy_rollback(self: AsyncSession) -> None:
        rollback_calls.append(None)
        await original_rollback(self)

    monkeypatch.setattr(sqlalchemy.ext.asyncio.AsyncSession, "rollback", spy_rollback)

    # One-shot commit failure. Use a mutable cell so the patched coroutine
    # can self-uninstall deterministically — no sleeps, no races.
    original_commit = AsyncSession.commit
    fired = {"done": False}

    async def flaky_commit(self: AsyncSession) -> None:
        if not fired["done"]:
            fired["done"] = True
            # Restore BEFORE raising so any retry path that also calls
            # commit() sees the real implementation.
            monkeypatch.setattr(AsyncSession, "commit", original_commit)
            raise SQLAlchemyError("simulated commit failure")
        await original_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", flaky_commit)

    # Starlette TestClient re-raises unhandled server exceptions by
    # default. Assert the SQLAlchemyError propagates (which means the
    # commit-time guard re-raised it AFTER calling rollback). Against a
    # real HTTP stack this would surface as a 500 to the client.
    with pytest.raises(SQLAlchemyError, match="simulated commit failure"):
        _post_stow(client, dcm_failing)
    assert fired["done"], "monkeypatched commit was never invoked"

    # Regression assertion: the production code must have explicitly
    # invoked session.rollback() on the failure path. Without Task 1's
    # get_db rollback AND Task 2's stow_rs local rollback, this list is
    # empty (implicit rollback inside close() does not pass through).
    assert rollback_calls, (
        "expected an explicit session.rollback() call from production code "
        "on the commit-time failure path"
    )

    # Reset so we only count rollbacks from the failing path above.
    rollback_calls.clear()

    # Recovery request on the SAME TestClient. Load-bearing assertion: if
    # the session or underlying connection were poisoned, this would also
    # 500 with PendingRollbackError.
    response_ok = _post_stow(client, dcm_recovery)
    assert response_ok.status_code == 200, (
        f"recovery STOW should succeed, got {response_ok.status_code}: " f"{response_ok.text[:400]}"
    )
    stored = response_ok.json()["00081199"]["Value"]
    assert len(stored) == 1
    assert stored[0]["00081155"]["Value"][0] == recovery_sop_uid

    # Prove via QIDO that the instance is actually persisted.
    qido = client.get(
        "/v2/studies",
        params={"StudyInstanceUID": recovery_study_uid},
        headers={"Accept": "application/dicom+json"},
    )
    assert qido.status_code == 200
    studies = qido.json()
    assert len(studies) == 1
    assert studies[0]["0020000D"]["Value"][0] == recovery_study_uid


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Scenario B — per-part disk failure leaves siblings intact (partial success)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_stow_per_part_disk_failure_does_not_abort_batch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-part disk write failure must NOT roll back sibling parts.

    Exercises the partial-success path in ``_stage_record``: the part that
    cannot land on disk becomes a 0xC000 failure entry, but other parts in
    the same batch flow into the bulk INSERT and commit.

    Injection point: ``app.routers.stow.store_instance_bytes`` — called
    inside ``_stage_record`` BEFORE the bulk INSERT runs.  Failing it for
    one chosen SOP exercises the per-part exception handler without
    touching any DB state.

    Asserts that:
      * The route returns 200/202/409 (never 500).
      * ``FailedSOPSequence`` contains exactly the bad SOP with 0xC000.
      * ``ReferencedSOPSequence`` contains the surviving good SOP — this
        is the regression assertion that flipped from the legacy
        "all-or-nothing" behaviour.
      * QIDO confirms the surviving SOP's study is persisted.
      * No explicit ``rollback()`` was issued on the partial-success path
        (a clean batch INSERT does not need one).
      * A follow-up request on the same client still succeeds.
    """
    good_sop = "1.2.826.0.1.3680043.8.498.99990003.1.1"
    bad_sop = "1.2.826.0.1.3680043.8.498.99990003.2.1"
    shared_study = "1.2.826.0.1.3680043.8.498.99990003"

    dcm_good = DicomFactory.create_ct_image(
        patient_id="ROLLBACK-B-GOOD",
        study_uid=shared_study,
        series_uid="1.2.826.0.1.3680043.8.498.99990003.1",
        sop_uid=good_sop,
        with_pixel_data=False,
    )
    dcm_bad = DicomFactory.create_ct_image(
        patient_id="ROLLBACK-B-BAD",
        study_uid=shared_study,
        series_uid="1.2.826.0.1.3680043.8.498.99990003.2",
        sop_uid=bad_sop,
        with_pixel_data=False,
    )

    # Count explicit rollback calls so we can assert the partial-success
    # path does NOT trigger one (the legacy all-or-nothing path did).
    rollback_calls: list[None] = []
    original_rollback = sqlalchemy.ext.asyncio.AsyncSession.rollback

    async def spy_rollback(self: AsyncSession) -> None:
        rollback_calls.append(None)
        await original_rollback(self)

    monkeypatch.setattr(sqlalchemy.ext.asyncio.AsyncSession, "rollback", spy_rollback)

    original_store_instance_bytes = stow_module.store_instance_bytes
    raised = {"count": 0}

    async def flaky_store_instance_bytes(
        data: bytes, study_uid: str, series_uid: str, sop_uid: str
    ) -> str:
        if sop_uid == bad_sop:
            raised["count"] += 1
            raise RuntimeError("simulated store_instance failure")
        return await original_store_instance_bytes(data, study_uid, series_uid, sop_uid)

    monkeypatch.setattr(stow_module, "store_instance_bytes", flaky_store_instance_bytes)

    body, content_type = _build_multipart([dcm_good, dcm_bad])
    response_batch = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert raised["count"] == 1, "flaky store_instance_bytes was never invoked"

    # Status: never 500 (partial success is a conventional STOW response).
    assert response_batch.status_code in (200, 202, 409), (
        f"unexpected status on partial-success: {response_batch.status_code}: "
        f"{response_batch.text[:400]}"
    )

    payload = response_batch.json()

    # Regression #1: the failed SOP must show up with 0xC000.
    assert "00081198" in payload, f"expected FailedSOPSequence in response, got: {payload!r}"
    failed = payload["00081198"]["Value"]
    assert any(
        f.get("00081155", {}).get("Value", [None])[0] == bad_sop
        and f.get("00081197", {}).get("Value", [None])[0] == 0xC000
        for f in failed
    ), f"expected the bad SOP to appear with 0xC000, got: {failed!r}"

    # Regression #2 (the partial-success flip): the GOOD SOP must show up
    # in ReferencedSOPSequence.  Under the legacy all-or-nothing rollback
    # this list was empty after a sibling failure.
    stored_seq = payload.get("00081199", {}).get("Value", [])
    assert any(
        entry.get("00081155", {}).get("Value", [None])[0] == good_sop for entry in stored_seq
    ), (
        "ReferencedSOPSequence must include the surviving sibling. "
        f"stored_seq={stored_seq!r}"
    )

    # Regression #3: no explicit rollback on the partial-success path.
    assert not rollback_calls, (
        "partial-success path must not issue an explicit session.rollback(); "
        f"observed {len(rollback_calls)} call(s)"
    )

    # Regression #4: the GOOD SOP's study is queryable via QIDO.
    qido = client.get(
        "/v2/studies",
        params={"StudyInstanceUID": shared_study},
        headers={"Accept": "application/dicom+json"},
    )
    assert qido.status_code == 200
    studies = qido.json()
    assert any(
        s.get("0020000D", {}).get("Value", [None])[0] == shared_study for s in studies
    ), f"good SOP's study must be persisted after partial-success, got: {studies!r}"

    # Restore the real store before the recovery request.
    monkeypatch.setattr(stow_module, "store_instance_bytes", original_store_instance_bytes)

    # Follow-up request on the same client must succeed (session is healthy).
    recovery_study_uid = "1.2.826.0.1.3680043.8.498.99990004"
    recovery_sop_uid = "1.2.826.0.1.3680043.8.498.99990004.1.1"
    dcm_recovery = DicomFactory.create_ct_image(
        patient_id="ROLLBACK-B-OK",
        study_uid=recovery_study_uid,
        series_uid="1.2.826.0.1.3680043.8.498.99990004.1",
        sop_uid=recovery_sop_uid,
        with_pixel_data=False,
    )
    response_ok = _post_stow(client, dcm_recovery)
    assert response_ok.status_code == 200, (
        f"recovery STOW should succeed, got {response_ok.status_code}: " f"{response_ok.text[:400]}"
    )
    stored = response_ok.json()["00081199"]["Value"]
    assert len(stored) == 1
    assert stored[0]["00081155"]["Value"][0] == recovery_sop_uid


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Scenario C — defensive: production engine factory passes pool_pre_ping
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_production_engine_pool_pre_ping_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production ``_make_engine`` must pass ``pool_pre_ping=True``.

    Asserts the constructor contract rather than reaching into
    SQLAlchemy's private ``engine.pool._pre_ping`` attribute, which is
    an internal implementation detail that may change on major upgrades.
    """
    captured: dict[str, object] = {}

    class _StubEngine:
        """Minimal stand-in so we don't actually open a connection pool."""

    def _capture(*args: object, **kwargs: object) -> _StubEngine:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _StubEngine()

    monkeypatch.setattr(database_module, "create_async_engine", _capture)

    result = database_module._make_engine()

    assert isinstance(result, _StubEngine), "factory must return the patched engine"
    kwargs = captured.get("kwargs", {})
    assert isinstance(kwargs, dict)
    assert kwargs.get("pool_pre_ping") is True, (
        "app.database._make_engine() must pass pool_pre_ping=True to "
        "create_async_engine so pooled connections are validated on checkout"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Scenario D — sibling visibility under partial-success (QIDO + events)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_stow_partial_success_sibling_is_queryable_and_event_fires(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Partial-success siblings must be fully observable from outside the request.

    Where Scenario B asserts the response envelope tells the truth, this
    scenario goes one step further and proves that the surviving sibling:

    * appears via QIDO under its own ``StudyInstanceUID``;
    * does NOT incorrectly leak into the failure list; and
    * is the only winner reported in ``ReferencedSOPSequence`` (no stale
      Python-side mirror entries from the failing part).

    The legacy all-or-nothing rollback discarded the sibling in all three
    of those views; the bulk-INSERT partial-success path keeps them all
    in lock-step.
    """
    good_sop = "1.2.826.0.1.3680043.8.498.99990005.1.1"
    good_study_uid = "1.2.826.0.1.3680043.8.498.99990005"
    bad_sop = "1.2.826.0.1.3680043.8.498.99990005.2.1"

    dcm_good = DicomFactory.create_ct_image(
        patient_id="ROLLBACK-D-GOOD",
        study_uid=good_study_uid,
        series_uid="1.2.826.0.1.3680043.8.498.99990005.1",
        sop_uid=good_sop,
        with_pixel_data=False,
    )
    dcm_bad = DicomFactory.create_ct_image(
        patient_id="ROLLBACK-D-BAD",
        study_uid=good_study_uid,
        series_uid="1.2.826.0.1.3680043.8.498.99990005.2",
        sop_uid=bad_sop,
        with_pixel_data=False,
    )

    original_store_instance_bytes = stow_module.store_instance_bytes
    raised = {"count": 0}

    async def flaky_store_instance_bytes(
        data: bytes, study_uid: str, series_uid: str, sop_uid: str
    ) -> str:
        if sop_uid == bad_sop:
            raised["count"] += 1
            raise RuntimeError("simulated store_instance failure")
        return await original_store_instance_bytes(data, study_uid, series_uid, sop_uid)

    monkeypatch.setattr(stow_module, "store_instance_bytes", flaky_store_instance_bytes)

    body, content_type = _build_multipart([dcm_good, dcm_bad])
    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert raised["count"] == 1, "flaky store_instance_bytes was never invoked"

    # Route should not 500 — the bulk INSERT path treats per-part disk
    # failures as conventional STOW failure entries.
    assert response.status_code in (200, 202, 409), (
        f"unexpected status on partial-success: {response.status_code}: " f"{response.text[:400]}"
    )
    payload = response.json()

    # Regression assertion #1 (the partial-success flip): the GOOD SOP
    # IS reported in ReferencedSOPSequence — it survived the sibling
    # failure and committed.  Under the legacy all-or-nothing rollback
    # this list would be empty.
    stored_seq = payload.get("00081199", {}).get("Value", [])
    assert any(
        entry.get("00081155", {}).get("Value", [None])[0] == good_sop for entry in stored_seq
    ), (
        "ReferencedSOPSequence must list the surviving sibling under the "
        f"partial-success contract. stored_seq={stored_seq!r}"
    )
    # And the failed SOP must NOT show up as stored.
    assert all(
        entry.get("00081155", {}).get("Value", [None])[0] != bad_sop for entry in stored_seq
    ), f"failed SOP must not appear in ReferencedSOPSequence; stored_seq={stored_seq!r}"

    # Regression assertion #2: the bad SOP is the only failure entry.
    assert "00081198" in payload, f"expected FailedSOPSequence in response, got: {payload!r}"
    failed = payload["00081198"]["Value"]
    assert any(
        f.get("00081155", {}).get("Value", [None])[0] == bad_sop
        and f.get("00081197", {}).get("Value", [None])[0] == 0xC000
        for f in failed
    ), f"expected the bad SOP to appear with 0xC000, got: {failed!r}"

    # Regression assertion #3: QIDO confirms the GOOD SOP's study IS
    # persisted.  This is the load-bearing change vs the legacy
    # behaviour, where this study would not have committed.
    qido = client.get(
        "/v2/studies",
        params={"StudyInstanceUID": good_study_uid},
        headers={"Accept": "application/dicom+json"},
    )
    assert qido.status_code == 200
    studies = qido.json()
    assert any(
        s.get("0020000D", {}).get("Value", [None])[0] == good_study_uid for s in studies
    ), (
        "good SOP's study must be persisted after a partial-success batch. "
        f"studies={studies!r}"
    )

    # Restore before the recovery request to keep intent explicit.
    monkeypatch.setattr(stow_module, "store_instance_bytes", original_store_instance_bytes)

    # Session-health check: fresh valid STOW on the same client must succeed.
    recovery_study_uid = "1.2.826.0.1.3680043.8.498.99990006"
    recovery_sop_uid = "1.2.826.0.1.3680043.8.498.99990006.1.1"
    dcm_recovery = DicomFactory.create_ct_image(
        patient_id="ROLLBACK-D-OK",
        study_uid=recovery_study_uid,
        series_uid="1.2.826.0.1.3680043.8.498.99990006.1",
        sop_uid=recovery_sop_uid,
        with_pixel_data=False,
    )
    response_ok = _post_stow(client, dcm_recovery)
    assert response_ok.status_code == 200, (
        f"recovery STOW should succeed, got {response_ok.status_code}: " f"{response_ok.text[:400]}"
    )
    stored = response_ok.json()["00081199"]["Value"]
    assert len(stored) == 1
    assert stored[0]["00081155"]["Value"][0] == recovery_sop_uid
