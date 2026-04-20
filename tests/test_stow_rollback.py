"""Regression tests for session rollback on STOW-RS failure.

Covers the fix for the session pool-poisoning bug where an unhandled
SQLAlchemyError on ``commit()`` would leave the session dirty, poisoning
subsequent pooled connections. See PR #15 for the design discussion.

What this suite proves:
    * Scenarios A and B spy on ``AsyncSession.rollback`` to assert that
      the production code's **explicit** ``rollback()`` call is invoked
      on the failure path (not merely the implicit rollback inside
      ``AsyncSession.__aexit__``'s ``close()``). This closes the main
      regression gap on SQLite: without the production fix, these
      assertions fail even though the end-to-end "recovery request
      succeeds" check still passes (because aiosqlite has no real pool
      and ``async with`` cleanup rolls back on its own).
    * Scenarios A and B also verify the follow-up request succeeds and
      the instance is persisted (QIDO round-trip).
    * Scenario C asserts the production ``engine`` is configured with
      ``pool_pre_ping=True``.

Layers exercised:
    * Scenario A — commit-time ``SQLAlchemyError`` inside ``stow_rs``:
      exercises the local try/except/rollback guard around the
      ``await db.commit()`` call in ``app/routers/stow.py`` AND the
      outer ``get_db`` rollback-on-exception wrapper.
    * Scenario B — in-loop exception inside ``_process_stow_instances``:
      exercises the in-loop ``await db.rollback()`` in the per-part
      exception handler.
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
#  Scenario B — in-loop failure inside _process_stow_instances
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_stow_recovers_after_in_loop_instance_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception inside the per-instance loop must not poison later requests.

    Exercises the ``await db.rollback()`` added to the ``except`` block
    in ``_process_stow_instances`` in ``app/routers/stow.py``.

    Injection point: ``app.routers.stow.store_instance`` — called from the
    POST path AFTER the deduplication check and metadata extraction, so
    the per-part ``try/except`` wraps it cleanly. Making it raise on a
    chosen SOP UID forces the in-loop rollback path without affecting any
    other test.

    Observed behavior (matches the plan's noted tradeoff):
        Because ``_process_stow_instances`` runs all parts in one
        transaction, the in-loop rollback discards previously-added
        in-memory adds for OTHER parts in the same batch. The response
        still completes (no 500) and reports at least one failure entry;
        sibling "good" parts in the same batch do NOT persist. The plan
        documents per-instance savepoints as a future enhancement. This
        test encodes the current behavior and focuses on the real goal:
        the NEXT request still works on the same client/session.
    """
    good_sop = "1.2.826.0.1.3680043.8.498.99990003.1.1"
    bad_sop = "1.2.826.0.1.3680043.8.498.99990003.2.1"

    dcm_good = DicomFactory.create_ct_image(
        patient_id="ROLLBACK-B-GOOD",
        study_uid="1.2.826.0.1.3680043.8.498.99990003",
        series_uid="1.2.826.0.1.3680043.8.498.99990003.1",
        sop_uid=good_sop,
        with_pixel_data=False,
    )
    dcm_bad = DicomFactory.create_ct_image(
        patient_id="ROLLBACK-B-BAD",
        study_uid="1.2.826.0.1.3680043.8.498.99990003",
        series_uid="1.2.826.0.1.3680043.8.498.99990003.2",
        sop_uid=bad_sop,
        with_pixel_data=False,
    )

    # Spy on AsyncSession.rollback to count EXPLICIT calls scoped to the
    # in-loop failure path. Without Task 2's ``await db.rollback()`` in
    # ``_process_stow_instances``, this list stays empty.
    rollback_calls: list[None] = []
    original_rollback = sqlalchemy.ext.asyncio.AsyncSession.rollback

    async def spy_rollback(self: AsyncSession) -> None:
        rollback_calls.append(None)
        await original_rollback(self)

    monkeypatch.setattr(sqlalchemy.ext.asyncio.AsyncSession, "rollback", spy_rollback)

    original_store_instance = stow_module.store_instance
    raised = {"count": 0}

    async def flaky_store_instance(file_data: bytes, ds) -> str:
        if str(ds.SOPInstanceUID) == bad_sop:
            raised["count"] += 1
            raise RuntimeError("simulated store_instance failure")
        return await original_store_instance(file_data, ds)

    monkeypatch.setattr(stow_module, "store_instance", flaky_store_instance)

    body, content_type = _build_multipart([dcm_good, dcm_bad])
    response_batch = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert raised["count"] == 1, "flaky store_instance was never invoked"

    # Regression assertion: the in-loop exception branch must have
    # explicitly rolled back the session. Without Task 2's
    # ``await db.rollback()`` in ``_process_stow_instances``, this list
    # is empty.
    assert rollback_calls, (
        "expected an explicit session.rollback() call from production code "
        "on the in-loop failure path"
    )

    # Reset so any rollbacks during the recovery request don't mask a
    # regression on the failing path.
    rollback_calls.clear()

    # The route must NOT 500 — _process_stow_instances caught, rolled
    # back, and recorded a failure entry. Status may be 200, 202, or 409
    # depending on how the route counts post-rollback successes.
    assert response_batch.status_code in (200, 202, 409), (
        f"unexpected status on in-loop failure: {response_batch.status_code}: "
        f"{response_batch.text[:400]}"
    )

    payload = response_batch.json()
    # A FailedSOPSequence with the in-loop (0xC000) failure entry is the
    # observable signature of the in-loop except branch.
    assert "00081198" in payload, f"expected FailedSOPSequence in response, got: {payload!r}"
    failed = payload["00081198"]["Value"]
    assert any(
        f.get("00081197", {}).get("Value", [None])[0] == 0xC000 for f in failed
    ), f"expected an in-loop (0xC000) failure entry, got: {failed!r}"

    # Explicit restore before the recovery request to make intent clear.
    monkeypatch.setattr(stow_module, "store_instance", original_store_instance)

    # A fresh, valid single-instance request on the same client. If the
    # session is poisoned, this would 500.
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
#  Scenario D — in-loop rollback must not report prior parts as stored
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_stow_response_excludes_rolled_back_siblings_on_in_loop_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-loop rollback must not leave stale entries in response/events.

    When a later part in a batch triggers the in-loop ``except`` branch
    (``await db.rollback()``), the session's pending ORM adds for earlier
    successful parts are discarded. The Python-side mirror lists
    (``result.stored`` and ``result.instances_to_publish``) must also be
    cleared, otherwise:

    * The STOW response's ``ReferencedSOPSequence`` (``00081199``) reports
      prior parts as stored when they were actually rolled back.
    * ``_publish_stow_events`` emits ``DicomImageCreated`` events for
      instances that never got persisted.

    Steps:
        1. Two-part multipart STOW: part 1 valid, part 2 monkeypatched
           to raise inside ``store_instance`` (same pattern as Scenario B).
        2. Send the batch.
        3. Assert ``ReferencedSOPSequence`` is absent OR empty — part 1
           must NOT be reported as stored.
        4. Assert ``FailedSOPSequence`` records the failure for part 2.
        5. Immediately QIDO for part 1's SOP UID and confirm it is not
           persisted (rollback wiped it from the DB).
        6. Send a fresh valid STOW on the same client to confirm the
           session is still healthy.
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

    original_store_instance = stow_module.store_instance
    raised = {"count": 0}

    async def flaky_store_instance(file_data: bytes, ds) -> str:
        if str(ds.SOPInstanceUID) == bad_sop:
            raised["count"] += 1
            raise RuntimeError("simulated store_instance failure")
        return await original_store_instance(file_data, ds)

    monkeypatch.setattr(stow_module, "store_instance", flaky_store_instance)

    body, content_type = _build_multipart([dcm_good, dcm_bad])
    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert raised["count"] == 1, "flaky store_instance was never invoked"

    # Route should not 500 — in-loop rollback catches, records the failure,
    # and returns a conventional STOW-RS envelope.
    assert response.status_code in (200, 202, 409), (
        f"unexpected status on in-loop failure: {response.status_code}: " f"{response.text[:400]}"
    )
    payload = response.json()

    # Regression assertion #1: ReferencedSOPSequence must NOT claim part 1
    # as stored. Without the `result.stored.clear()` in the except branch,
    # part 1 would appear here even though the rollback wiped it from the
    # DB, causing the response to lie to the client.
    stored_seq = payload.get("00081199", {}).get("Value", [])
    assert stored_seq == [] or all(
        entry.get("00081155", {}).get("Value", [None])[0] != good_sop for entry in stored_seq
    ), (
        "ReferencedSOPSequence must not list part 1's SOP UID after the "
        "sibling rollback discarded it from the session. "
        f"stored_seq={stored_seq!r}"
    )

    # Regression assertion #2: FailedSOPSequence records the 0xC000 failure
    # from the in-loop except branch.
    assert "00081198" in payload, f"expected FailedSOPSequence in response, got: {payload!r}"
    failed = payload["00081198"]["Value"]
    assert any(
        f.get("00081197", {}).get("Value", [None])[0] == 0xC000 for f in failed
    ), f"expected an in-loop (0xC000) failure entry, got: {failed!r}"

    # Regression assertion #3: QIDO confirms part 1 is NOT persisted.
    # If instances_to_publish had leaked, the event publisher would have
    # fired, but the DB itself must remain clean.
    qido = client.get(
        "/v2/studies",
        params={"StudyInstanceUID": good_study_uid},
        headers={"Accept": "application/dicom+json"},
    )
    # QIDO returns 200 with empty list when nothing matches.
    assert qido.status_code in (200, 204)
    if qido.status_code == 200 and qido.text:
        studies = qido.json()
        assert studies == [] or all(
            s.get("0020000D", {}).get("Value", [None])[0] != good_study_uid for s in studies
        ), "part 1's study must not be persisted after in-loop rollback. " f"studies={studies!r}"

    # Restore before the recovery request to keep intent explicit.
    monkeypatch.setattr(stow_module, "store_instance", original_store_instance)

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
