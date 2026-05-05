"""STOW-RS micro-benchmarks driven by ``pytest-benchmark``.

These benchmarks exercise the full POST ``/v2/studies`` path against the
in-process FastAPI application (no live emulator container needed).
They live alongside the live-service ``test_stow_perf.py`` baseline,
but unlike that one they collect a statistical distribution per scenario
so we can track regressions over time via ``--benchmark-autosave``.

Three scenarios are wired up to bracket the realistic STOW load shapes:

* **50 instances** — small batch, dominated by per-request overhead.
* **500 instances** — typical mid-size study; stresses the parse pool
  dispatch path and the bulk INSERT round-trip.
* **5 000 instances** — high-fanout STOW, the workload that motivates
  the round-2 changes (bulk INSERT, partial success, BulkDataURI,
  bounded-gather event publish).

Run with::

    pytest tests/performance/test_stow_benchmark.py \\
        --benchmark-only \\
        --benchmark-columns=mean,median,min,max,stddev,rounds \\
        --benchmark-autosave

The 5 000-instance scenario is opt-in via ``--run-stow-5k`` because it
takes a noticeable amount of wall time even on fast machines; CI runs
should typically stick to the 50/500 sizes.

Requires ``pytest-benchmark`` (already in the dev extras).
"""

from __future__ import annotations

import io
import os
import uuid
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db

pytestmark = pytest.mark.performance


# The ``--run-stow-5k`` opt-in flag is registered in tests/performance/conftest.py.

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DICOM byte-string factory
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _build_dicom_bytes(
    *,
    study_uid: str,
    series_uid: str,
    sop_uid: str,
    patient_id: str = "BENCH-001",
    instance_number: int = 1,
) -> bytes:
    """Build a minimal valid CT DICOM byte-string.

    Kept extremely small (no pixel data) so the benchmark measures the
    server's request-handling cost rather than DICOM parser throughput.
    """
    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.1"

    ds = FileDataset(
        filename_or_obj=io.BytesIO(),
        dataset=Dataset(),
        file_meta=file_meta,
        preamble=b"\x00" * 128,
    )
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.PatientID = patient_id
    ds.PatientName = f"Bench^{patient_id}"
    ds.StudyDate = "20260214"
    ds.StudyTime = "120000"
    ds.Modality = "CT"
    ds.AccessionNumber = f"ACC-{uuid.uuid4().hex[:8]}"
    ds.StudyDescription = "Benchmark Study"
    ds.SeriesDescription = "Benchmark Series"
    ds.SeriesNumber = 1
    ds.InstanceNumber = instance_number

    buf = io.BytesIO()
    ds.save_as(buf)
    return buf.getvalue()


def _build_multipart_body(parts: list[bytes], boundary: str = "stow-bench") -> tuple[bytes, str]:
    """Serialize a list of DICOM byte-strings into a multipart/related body.

    Returns ``(body, content_type_header)``.
    """
    chunks: list[bytes] = []
    for part in parts:
        chunks.append(f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode())
        chunks.append(part)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    content_type = f"multipart/related; type=application/dicom; boundary={boundary}"
    return body, content_type


def _build_batch(count: int) -> tuple[bytes, str]:
    """Pre-build a multipart body for ``count`` instances in a fresh study.

    All instances share the same study/series so the bulk INSERT path
    sees the realistic "many instances per study" shape.
    """
    study_uid = generate_uid()
    series_uid = generate_uid()
    parts = [
        _build_dicom_bytes(
            study_uid=study_uid,
            series_uid=series_uid,
            sop_uid=generate_uid(),
            patient_id=f"BENCH-{count:05d}",
            instance_number=i + 1,
        )
        for i in range(count)
    ]
    return _build_multipart_body(parts)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  In-process benchmark client
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.fixture(scope="module")
def bench_client(tmp_path_factory):
    """Build a :class:`TestClient` wired to a SQLite test DB + tmp storage.

    The client is module-scoped so the DB schema cost is paid once and
    each benchmark scenario sees the same warmed-up app.  The tests are
    isolated by study UID, so cross-scenario contamination is benign.
    """
    tmp_path = tmp_path_factory.mktemp("stow_bench")

    storage_dir = tmp_path / "dicom_storage"
    storage_dir.mkdir(exist_ok=True)
    os.environ["DICOM_STORAGE_DIR"] = str(storage_dir)

    import app.services.dicom_engine as dicom_engine

    dicom_engine.STORAGE_DIR = str(storage_dir)

    db_path = tmp_path / "stow_bench.db"
    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    test_session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_db():
        async with test_session_factory() as session:
            try:
                yield session
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # Mirror the production lifespan's app-state contract.  No parse
        # pool — the spawn-based pool isn't worth the cold-start cost
        # for these in-process benchmarks; the dispatcher falls back to
        # an inline parse when ``parse_pool`` is None.
        app.state.pending_event_tasks = set()
        app.state.parse_pool = None
        yield
        pending = app.state.pending_event_tasks
        if pending:
            import asyncio as _asyncio

            await _asyncio.gather(*pending, return_exceptions=True)
        await test_engine.dispose()

    test_app = FastAPI(lifespan=_lifespan)
    from app.routers import (
        changefeed,
        debug,
        delete as delete_router,
        dicomweb,
        extended_query_tags,
        operations,
        ups,
    )

    test_app.include_router(dicomweb.router, prefix="/v2", tags=["DICOMweb"])
    test_app.include_router(changefeed.router, prefix="/v2", tags=["Change Feed"])
    test_app.include_router(extended_query_tags.router, prefix="/v2", tags=["Extended Query Tags"])
    test_app.include_router(operations.router, prefix="/v2", tags=["Operations"])
    test_app.include_router(ups.router, prefix="/v2", tags=["UPS-RS"])
    test_app.include_router(delete_router.router, prefix="/v2", tags=["DELETE"])
    test_app.include_router(debug.router, tags=["Debug"])

    test_app.dependency_overrides[get_db] = override_get_db

    with TestClient(test_app) as client:
        yield client


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Pre-built bodies — caching avoids paying pydicom serialization
#  inside the benchmark inner loop.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.fixture(scope="module")
def stow_body_50() -> tuple[bytes, str]:
    return _build_batch(50)


@pytest.fixture(scope="module")
def stow_body_500() -> tuple[bytes, str]:
    return _build_batch(500)


@pytest.fixture(scope="module")
def stow_body_5000() -> tuple[bytes, str]:
    return _build_batch(5000)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Benchmark scenarios
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _post_stow(client: TestClient, body: bytes, content_type: str) -> int:
    """Post a pre-built multipart body and return the status code.

    A new study UID is **not** generated per call — each scenario reuses
    the same body so successive calls inside a single benchmark round
    will hit the duplicate-detection (45070) path on the second+ call.
    That's intentional: the benchmark deliberately exercises the bulk
    ``ON CONFLICT DO NOTHING`` path, which is the round-2 hot loop.
    """
    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert response.status_code in (200, 202), f"STOW failed: {response.status_code}"
    return response.status_code


@pytest.mark.benchmark(group="stow")
def test_stow_benchmark_50_instances(benchmark, bench_client, stow_body_50):
    """50-instance batch — small-study STOW dominated by per-request overhead."""
    body, content_type = stow_body_50
    benchmark.pedantic(
        lambda: _post_stow(bench_client, body, content_type),
        rounds=5,
        iterations=1,
        warmup_rounds=1,
    )


@pytest.mark.benchmark(group="stow")
@pytest.mark.slow
def test_stow_benchmark_500_instances(benchmark, bench_client, stow_body_500):
    """500-instance batch — typical mid-size study, hits the parse-pool dispatch path."""
    body, content_type = stow_body_500
    benchmark.pedantic(
        lambda: _post_stow(bench_client, body, content_type),
        rounds=3,
        iterations=1,
        warmup_rounds=1,
    )


@pytest.mark.benchmark(group="stow")
@pytest.mark.slow
def test_stow_benchmark_5000_instances(benchmark, bench_client, stow_body_5000, request):
    """5 000-instance batch — round-2 high-fanout target.

    Opt-in via ``--run-stow-5k``.  Excluded from default runs because a
    single round can take tens of seconds on a fast machine.
    """
    if not request.config.getoption("--run-stow-5k"):
        pytest.skip("Skipped by default; run with --run-stow-5k to include.")
    body, content_type = stow_body_5000
    benchmark.pedantic(
        lambda: _post_stow(bench_client, body, content_type),
        rounds=2,
        iterations=1,
        warmup_rounds=0,
    )
