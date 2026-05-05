---
name: stow performance round 2
overview: "A second STOW-RS performance iteration that finishes the work deferred from the first round: bulk dedup + bulk INSERT (eliminating the N+1 SELECTs), true per-part partial success on POST, multi-worker uvicorn tuning, SQLAlchemy pool sizing, BulkDataURI for binary VRs, the same batching for bulk_update_studies, bounded-gather inside AzureStorageQueueProvider, a pytest-benchmark regression suite, and a lightweight outbox/replay using the existing change_feed table."
todos:
  - id: bulk-insert-post
    content: Replace per-part dedup SELECT in stow_rs (POST) with bulk pg_insert ... ON CONFLICT DO NOTHING RETURNING; emit 45070 entries for losers (app/routers/stow.py)
    status: completed
  - id: bulk-upsert-put
    content: Refactor app/services/upsert.py into upsert_instances_bulk using ON CONFLICT DO UPDATE RETURNING xmax-trick to distinguish inserts vs updates; drop the post-replace re-SELECT in stow.py
    status: completed
  - id: partial-success-semantics
    content: Drop all-or-nothing rollback in _process_stow_records; per-part disk-write failures become 0xC000 entries that don't abort the batch; rewrite test_stow_rollback Scenarios B/D to assert siblings survive
    status: completed
  - id: pool-sizing
    content: Add pool_size / max_overflow / pool_timeout / pool_recycle knobs to app/database.py and surface them in app/config.py
    status: completed
  - id: uvicorn-tuning
    content: Switch Dockerfile CMD to multi-worker uvicorn with --timeout-keep-alive / --timeout-graceful-shutdown / --limit-concurrency knobs; surface in docker-compose.yml
    status: completed
  - id: bulkdata-uri
    content: Replace base64 InlineBinary in dataset_to_dicom_json with BulkDataURI; thread bulkdata_base through callers; add GET /bulkdata/{tag} endpoint in app/routers/wado.py
    status: completed
  - id: bulk-update-batching
    content: Refactor bulk_update_studies to single Core executemany UPDATE; add _apply_bulk_update_to_json helper that respects BulkDataURI
    status: completed
  - id: outbox-column
    content: Add event_published_at column + partial index to ChangeFeedEntry; add outbox_session_factory to app/database.py
    status: completed
  - id: outbox-publisher-stamp
    content: After successful publish_batch in _publish_stow_events, UPDATE change_feed SET event_published_at = now() WHERE sequence IN (...) using a fresh outbox session
    status: completed
  - id: outbox-sweeper
    content: Add startup _sweep_unpublished_events asyncio task in main.lifespan() that pages through unpublished change_feed rows and replays via publish_batch
    status: completed
  - id: azure-queue-bounded-gather
    content: Refactor AzureStorageQueueProvider.publish_batch to use bounded asyncio.gather (Semaphore + asyncio.gather) instead of a serial loop; add EVENT_AZURE_QUEUE_BATCH_CONCURRENCY env var (default 16) in app/config.py
    status: completed
  - id: stow-benchmark
    content: Add tests/performance/test_stow_benchmark.py using pytest-benchmark fixture with 50 / 500 / 5000-instance scenarios; document run instructions in tests/README.md and CLAUDE.md
    status: completed
isProject: false
---

# STOW-RS Performance Review — Round 2

This iteration closes out every "Out of scope" follow-up from `[c__Users_jmoreau_.cursor_plans_stow_performance_review_3fbc495b.plan-L1-L478-0.md](C:\Users\jmoreau\.cursor\projects\d-code-resonait-azure-dicom-service-emulator/uploads/c__Users_jmoreau_.cursor_plans_stow_performance_review_3fbc495b.plan-L1-L478-0.md)` except the consumer-side visibility delay (which stays in the .NET webhook receiver).

The bounded-`gather` change inside `AzureStorageQueueProvider.publish_batch` is being pulled into this round even though it's still off the response path: in combination with the new outbox stamp it directly shrinks the "lost events on hard kill" window between `db.commit()` and `event_published_at = now()`.

The biggest semantic change is item 3 — a single bad part no longer torches the whole batch — and it is enabled by item 1 (bulk INSERT with `ON CONFLICT DO NOTHING`). All other items are independent and can land in any order.

## Confirmed design decisions

- **Partial success strategy:** bulk `INSERT ... ON CONFLICT (sop_instance_uid) DO NOTHING RETURNING sop_instance_uid` for the POST path. Dedup losers come back atomically as 45070 warnings (Azure v2). Other constraint violations are rare and remain whole-batch failures — that matches "indicates real corruption."
- **Outbox approach:** reuse the existing `change_feed` table; add `event_published_at TIMESTAMPTZ NULL`. No new table, no new endpoints. The background publisher stamps the column on `publish_batch` success; a startup sweep replays any unstamped rows.

## Architecture delta

```mermaid
flowchart LR
    Stow[stow_rs handler]
    PreInsert[pre-insert validation<br/>"+ written_files tracking"]
    BulkIns["INSERT ... ON CONFLICT<br/>DO NOTHING RETURNING"]
    BulkFeed["INSERT ... change_feed<br/>"+ event_published_at=NULL""]
    PG[(Postgres)]
    OutboxBG["fire-and-forget publisher<br/>(per-batch task)"]
    Sweep["startup outbox sweeper<br/>(replays unpublished)"]

    Stow --> PreInsert
    PreInsert --> BulkIns
    BulkIns --> PG
    BulkIns --> BulkFeed
    BulkFeed --> PG
    BulkFeed --> OutboxBG
    OutboxBG -->|"UPDATE event_published_at=now()"| PG
    Sweep -->|"SELECT WHERE event_published_at IS NULL"| PG
    Sweep --> OutboxBG
```

## 1. Bulk dedup + bulk Core inserts (POST + PUT)

`[app/routers/stow.py](app/routers/stow.py)` lines **472-486** (POST dedup `select`) and **467-470** (PUT post-replace re-`select`), plus `[app/services/upsert.py](app/services/upsert.py)` lines **72-74** (PUT existence `select`).

**POST path (`upsert=False`).** Replace the per-part `select(DicomInstance).where(DicomInstance.sop_instance_uid == sop_uid)` with a single `pg_insert` (`from sqlalchemy.dialects.postgresql import insert as pg_insert`):

```python
stmt = (
    pg_insert(DicomInstance.__table__)
    .values(records_to_insert)              # list[dict] built from ParsedPartRecord
    .on_conflict_do_nothing(index_elements=[DicomInstance.sop_instance_uid])
    .returning(DicomInstance.__table__.c.sop_instance_uid)
)
inserted_sops: set[str] = {r[0] for r in (await db.execute(stmt)).all()}
```

Then iterate the parsed records once in Python:
- `sop_uid in inserted_sops` -> success entry in `result.stored`, push `(instance, feed_entry)` to `instances_to_publish`.
- `sop_uid not in inserted_sops` -> 45070 (`FAILURE_REASON_INSTANCE_ALREADY_EXISTS`) warning entry; **do not** unlink the file (its existing twin owns the path; this is a duplicate of identical content).

`change_feed` rows for the winners are inserted in a second Core `INSERT ... VALUES (...)` in the same transaction. `sequence` is autoincrement so the DB assigns it; we then `RETURNING change_feed.sequence` to feed the event payload.

**PUT path (`upsert=True`).** Refactor `[app/services/upsert.py](app/services/upsert.py)` `upsert_instance` into a batch helper `upsert_instances_bulk(db, records, storage_dir) -> list[UpsertOutcome]`:

1. `SELECT sop_instance_uid, file_path FROM dicom_instances WHERE sop_instance_uid IN (:sops)` — single round-trip, populates a "replaces" map.
2. Bulk `INSERT ... ON CONFLICT (sop_instance_uid) DO UPDATE SET ... RETURNING sop_instance_uid, (xmax = 0) AS inserted` — Postgres trick: `xmax=0` distinguishes inserts from updates atomically.
3. For replaced rows, schedule old-file unlink via the existing `_try_unlink` / `_try_rmdir` helpers, but only AFTER `db.commit()` succeeds (today's per-row "delete-then-insert" can leak old files on rollback; the bulk path keeps that invariant the same way).
4. Drop the post-replace `select` in `_persist_record` — `RETURNING` already gives us back the columns we need to build the event.

This eliminates two round-trips per instance on PUT and one on POST. Existing rollback/regression tests in `tests/test_stow_rollback.py` keep working: per-part Python-side validation failures still pre-empt the bulk insert and land in `result.failures` as before.

## 2. True per-part partial success on POST

`[app/routers/stow.py](app/routers/stow.py)` `_process_stow_records` lines **557-588** — the all-or-nothing `db.rollback()` + `_unlink_files(written_files)` branch.

With item 1 in place, the only failure modes inside the batch are:

- **Worker-side parse / validation failure** -> `record.ok is False` -> already pre-empted; never reaches the DB.
- **Unique-constraint dedup** -> handled by `ON CONFLICT DO NOTHING` -> no exception.
- **Other constraint failure** (e.g. malformed JSON, FK violation) -> the bulk INSERT raises; we keep the existing whole-batch rollback for this case (it indicates real corruption).
- **Disk write failure for one part** -> we **do not** abort the batch; that one part becomes a `0xC000` failure entry with `WarningReason` set, the file we *attempted* to write is unlinked, and we keep going. Other parts in the batch still commit.

Concretely, restructure `_process_stow_records` so the per-part exception handler:

- Appends a single `0xC000` failure entry **for that part only**.
- Calls `await asyncio.to_thread(os.remove, attempted_path)` for that part.
- **Does not** clear `result.stored` / `result.instances_to_publish` / `result.written_files`.
- **Does not** call `db.rollback()` (no DB writes happen until the bulk INSERT, which is one transaction-scoped operation at the end of the loop).

The existing rollback regression tests in `[tests/test_stow_rollback.py](tests/test_stow_rollback.py)` Scenarios B and D currently assert that prior-part successes are *erased* on a per-part failure. Those assertions need to flip to assert that prior parts **survive** (which is what the new, correct semantics demand). I will rewrite both scenarios to inject a flaky `store_instance_bytes` for one specific SOP and assert:
- response `ReferencedSOPSequence` includes the surviving SOPs;
- response `FailedSOPSequence` contains exactly the bad SOP with `0xC000`;
- the surviving SOPs are queryable via QIDO after the request returns.

## 3. SQLAlchemy pool sizing

`[app/database.py](app/database.py)` line **17**.

Add `pool_size`, `max_overflow`, and `pool_timeout` knobs, defaulting to values that comfortably cover one uvicorn worker's concurrent request slots:

```python
return create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "20")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
    pool_timeout=float(os.getenv("DB_POOL_TIMEOUT_SECONDS", "30")),
    pool_recycle=int(os.getenv("DB_POOL_RECYCLE_SECONDS", "1800")),
)
```

Add the four env vars to `[app/config.py](app/config.py)` so they're discoverable. The async-engine pool defaults to `pool_size=5, max_overflow=10`, which is the bottleneck the moment a STOW request and a parallel QIDO collide.

## 4. Multi-worker uvicorn + timeout / limit tuning

`[Dockerfile](Dockerfile)` line **64** — replace the bare `uvicorn` invocation:

```dockerfile
CMD ["sh", "-c", "/app/.venv/bin/uvicorn main:app \
  --host 0.0.0.0 --port 8080 \
  --workers ${UVICORN_WORKERS:-4} \
  --timeout-keep-alive ${UVICORN_KEEPALIVE:-75} \
  --timeout-graceful-shutdown ${UVICORN_GRACEFUL_SHUTDOWN:-60} \
  --limit-concurrency ${UVICORN_LIMIT_CONCURRENCY:-200} \
  --h11-max-incomplete-event-size ${UVICORN_H11_MAX:-16384}"]
```

Note: with `--workers > 1` each worker gets its **own** `ProcessPoolExecutor` (the parse pool is created per-worker in `lifespan()`). The default of 4 workers x `os.cpu_count()` parse workers can over-subscribe a small box; document this in `[CLAUDE.md](CLAUDE.md)` and add a `DICOM_PARSE_WORKERS` recommendation table.

`[docker-compose.yml](docker-compose.yml)` `emulator` service: surface the new env vars so they can be tuned without rebuilding the image. Bump the healthcheck `start_period` to `30s` to cover spawn-pool warm-up across N workers.

## 5. BulkDataURI for binary VRs

`[app/services/dicom_engine.py](app/services/dicom_engine.py)` lines **134-155** — replace the `base64.b64encode(...)` -> `entry["InlineBinary"]` branch with a BulkDataURI string:

```python
elif elem.VR in ("OB", "OD", "OF", "OL", "OW", "UN"):
    if elem.value is not None:
        tag = f"{elem.tag.group:04X}{elem.tag.element:04X}"
        entry["BulkDataURI"] = f"{bulkdata_base}/bulkdata/{tag}"
```

`bulkdata_base` is the per-instance URL prefix the route layer already knows (`/v2/studies/{study}/series/{series}/instances/{sop}`). Threading it down requires a small signature change on `dataset_to_dicom_json(ds, *, bulkdata_base: str | None = None)` so existing callers (tests, `bulk_update_studies`) that don't have a base just get `None` and skip the BulkDataURI emission entirely (their JSON loses the `InlineBinary` field rather than gaining a broken URI).

Backing endpoint: add a thin `GET /v2/studies/{s}/series/{ser}/instances/{inst}/bulkdata/{tag}` handler in `[app/routers/wado.py](app/routers/wado.py)` that re-reads the `.dcm`, extracts the requested tag's bytes via pydicom, and streams them with `Content-Type: application/octet-stream`. This is single-instance work (no batching) so a simple `await asyncio.to_thread(...)` is fine.

Net win: `dicom_json` rows shrink dramatically for modalities with private/overlay binary fields, the JSONB column stops doing write amplification, and the parse pool stops spending CPU on base64 encoding inside `dataset_to_dicom_json`.

## 6. Apply batching to bulk_update_studies

`[app/routers/stow.py](app/routers/stow.py)` `bulk_update_studies` lines **143-257**.

Today's loop loads each study's instances via ORM `select`, mutates `inst.dicom_json` in Python, and relies on the unit-of-work. For 5k-instance studies this round-trips on every flush. Refactor to:

- A single `select(DicomInstance.id, DicomInstance.dicom_json)` per study (already there).
- A single bulk `update(DicomInstance.__table__).where(DicomInstance.id.in_(:ids)).values(...)` Core statement using `executemany` semantics — `db.execute(stmt, [{"id": ..., "dicom_json": ...}, ...])`.
- One commit at the end (already true).

Plus: after item 5 lands, the JSON mutation in `bulk_update_studies` needs to honor BulkDataURI shape (don't re-base64 binary VRs that are now URIs). Add a small `_apply_bulk_update_to_json(json_blob, attribute_updates)` helper that touches only the listed attributes and leaves BulkDataURI entries alone.

## 7. Outbox / replay using change_feed.event_published_at

`[app/models/dicom.py](app/models/dicom.py)` `ChangeFeedEntry` (109-127) — add a column:

```python
event_published_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True), nullable=True, index=True
)
```

Migration shape: `ALTER TABLE change_feed ADD COLUMN event_published_at TIMESTAMPTZ NULL; CREATE INDEX ix_changefeed_unpublished ON change_feed (sequence) WHERE event_published_at IS NULL;` (partial index keeps the sweeper query cheap as the table grows).

`[app/routers/stow.py](app/routers/stow.py)` `_publish_stow_events` (623-662) — on `event_manager.publish_batch` success, issue:

```python
await db_for_outbox.execute(
    update(ChangeFeedEntry)
    .where(ChangeFeedEntry.sequence.in_(published_sequences))
    .values(event_published_at=func.now())
)
await db_for_outbox.commit()
```

The background task gets its **own** session (not the request's session, which is closed by the time the task runs). Add a small `outbox_session_factory` helper in `[app/database.py](app/database.py)` that returns a fresh `AsyncSession` for tasks that outlive their originating request.

`[main.py](main.py)` `lifespan()` — on startup, after the parse pool boots, schedule a one-shot **outbox sweep**:

```python
app.state.outbox_sweeper = asyncio.create_task(_sweep_unpublished_events(event_manager))
```

`_sweep_unpublished_events` does `SELECT * FROM change_feed WHERE event_published_at IS NULL ORDER BY sequence LIMIT N`, builds `DicomEvent`s, calls `publish_batch`, and stamps `event_published_at`. Loops in pages until the partial index is empty, then exits. Bounded by `OUTBOX_SWEEP_PAGE_SIZE` (default 500) and `OUTBOX_SWEEP_MAX_AGE_SECONDS` (default 86400 — older rows are considered consumer-handled via change-feed backfill).

Trade-off documented in plan: a hard kill between commit and `event_published_at = now()` still loses the in-flight publish, but the next startup picks it up. This is the change that closes the "process kill loses unfired events" gap noted in the previous round.

## 8. Bounded-`gather` inside `AzureStorageQueueProvider.publish_batch`

`[app/services/events/providers.py](app/services/events/providers.py)` lines **174-178** — the current implementation is a naive serial loop:

```174:178:app/services/events/providers.py
async def publish_batch(self, events: list[DicomEvent]) -> None:
    """Send batch of events to Azure Storage Queue."""
    for event in events:
        message = json.dumps(event.to_dict())
        await asyncio.to_thread(self.queue_client.send_message, message)
```

For a 5,000-event batch at ~5–20 ms per `send_message` (Azurite RTT + thread hop), that's 25–100 seconds of strictly sequential work. The webhook provider got the same treatment in round 1 — this closes the parity gap.

Refactor to a bounded `asyncio.gather`:

```python
async def publish_batch(self, events: list[DicomEvent]) -> None:
    if not events:
        return
    sem = asyncio.Semaphore(self._batch_concurrency)

    async def _send(event: DicomEvent) -> None:
        message = json.dumps(event.to_dict())
        async with sem:
            await asyncio.to_thread(self.queue_client.send_message, message)

    await asyncio.gather(*(_send(e) for e in events))
```

`self._batch_concurrency` is read from `EVENT_AZURE_QUEUE_BATCH_CONCURRENCY` (default `16`) added to `[app/config.py](app/config.py)`. The semaphore is critical: without it, a 5k-event batch would submit 5k tasks into the global default thread pool, starving every other `asyncio.to_thread` caller (`aiofiles`, parse-pool dispatch results, etc.). 16 is conservative — Azurite + the SDK client are both naturally bounded around that range, and it leaves headroom for the rest of the app.

**Synergy with item 7.** The outbox window — between `db.commit()` and `UPDATE change_feed SET event_published_at = now()` — shrinks proportionally with publish-batch wall-time. Faster publish = fewer rows the startup sweeper has to replay after a hard kill. So in this round it stops being a pure throughput optimization and reinforces the outbox guarantees.

Tests: extend `[tests/unit/services/test_event_providers.py](tests/unit/services/test_event_providers.py)` (or whichever file exercises `AzureStorageQueueProvider`) with two cases:

- **Concurrency cap respected.** Patch `self.queue_client.send_message` to record concurrent in-flight count; assert the maximum is `<= EVENT_AZURE_QUEUE_BATCH_CONCURRENCY`.
- **Per-event failure isolation.** Make one event's `send_message` raise; assert `asyncio.gather` is called with `return_exceptions=True` (or that the exception is logged and others still complete) — failing one event must not poison the rest of the batch.

## 9. pytest-benchmark STOW regression test

New file `tests/performance/test_stow_benchmark.py` using the **`benchmark`** fixture from `pytest-benchmark` (already in dev deps):

```python
@pytest.mark.benchmark(group="stow")
def test_stow_500_instances(benchmark, perf_client, dicom_factory_500):
    body, headers = _build_multipart(dicom_factory_500)
    benchmark.pedantic(
        lambda: perf_client.post("/v2/studies", content=body, headers=headers),
        rounds=5,
        iterations=1,
    )
```

Three test sizes (50, 500, 5000 instances) so the benchmark catches both throughput regressions and tail-latency regressions. Saved JSON output via `--benchmark-autosave` lets us track trends across PRs (committed under `tests/performance/.benchmarks/` like the existing repo convention).

Update `[tests/README.md](tests/README.md)` and `[CLAUDE.md](CLAUDE.md)` with the run instructions: `pytest tests/performance --benchmark-only --benchmark-columns=mean,median,min,max,stddev,rounds`.

## File-by-file change inventory

- `[app/routers/stow.py](app/routers/stow.py)` — `_persist_record`, `_process_stow_records`, `bulk_update_studies` (items 1, 2, 6).
- `[app/services/upsert.py](app/services/upsert.py)` — new `upsert_instances_bulk`; deprecate per-call `upsert_instance` (item 1).
- `[app/services/dicom_engine.py](app/services/dicom_engine.py)` — `dataset_to_dicom_json` BulkDataURI branch (item 5).
- `[app/routers/wado.py](app/routers/wado.py)` — new `/bulkdata/{tag}` endpoint (item 5).
- `[app/database.py](app/database.py)` — pool sizing knobs + `outbox_session_factory` (items 4, 7).
- `[app/config.py](app/config.py)` — new env vars (items 4, 7).
- `[app/models/dicom.py](app/models/dicom.py)` — `event_published_at` column on `ChangeFeedEntry` + partial index (item 7).
- `[main.py](main.py)` — outbox sweeper task (item 7).
- `[app/services/events/providers.py](app/services/events/providers.py)` — bounded-`gather` in `AzureStorageQueueProvider.publish_batch` (item 8).
- `[Dockerfile](Dockerfile)`, `[docker-compose.yml](docker-compose.yml)` — workers + timeouts (item 2).
- `[tests/test_stow_rollback.py](tests/test_stow_rollback.py)` — flip Scenarios B/D assertions to "siblings survive" (item 3).
- `tests/performance/test_stow_benchmark.py` — new benchmark suite (item 8).
- `tests/integration/routers/test_stow_partial_success.py` — new integration tests for item 3 semantics.
- `tests/unit/services/test_outbox_sweeper.py` — new unit tests for the sweeper (item 7).
- `tests/unit/services/test_event_providers.py` — extend with concurrency-cap and per-event-failure-isolation cases for `AzureStorageQueueProvider` (item 8).
- `tests/integration/routers/test_bulkdata_uri.py` — new tests for items 5 (URI shape + GET endpoint round-trip).

## Out of scope (intentionally deferred)

- Consumer-side visibility delay (stays in the .NET webhook receiver).
- Switching the change_feed table to a separate `event_outbox` table (the lightweight column add does the job; revisit only if change_feed grows large enough that partial-index scans become a problem).
- Per-tag BulkDataURI for **every** binary VR variant including private creators — the new endpoint only resolves standard public tags in this iteration; private-creator round-trip is a follow-up.