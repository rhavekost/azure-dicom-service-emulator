# Test Suite Documentation

## Overview

Comprehensive test suite for Azure DICOM Service Emulator with 90%+ code coverage target. The test suite is organized into multiple layers (unit, integration, e2e, performance, security) to ensure thorough testing of all components.

## Test Organization

```
tests/
├── unit/              # Unit tests (isolated, no DB/API)
│   ├── models/        # Database model tests
│   ├── services/      # Service layer tests (DICOM engine, events, etc.)
│   ├── routers/       # Router logic tests
│   └── utils/         # Utility function tests
├── integration/       # API integration tests
│   ├── test_stow_rs.py              # STOW-RS endpoint tests
│   ├── test_wado_rs_*.py            # WADO-RS endpoint tests
│   ├── test_qido_*.py               # QIDO-RS endpoint tests
│   └── test_ups_*.py                # UPS-RS endpoint tests
├── e2e/               # End-to-end workflow tests
├── performance/       # Benchmarks and load tests
│   └── load_tests/    # Load testing scenarios
├── security/          # Security scans
├── fixtures/          # Test data and DICOM files
│   ├── factories.py   # DICOM file factories
│   ├── sample_data.py # Fixture file generator
│   └── dicom/         # Generated DICOM files
│       ├── valid/     # Valid DICOM files
│       ├── edge_cases/# Edge case DICOM files
│       └── invalid/   # Invalid DICOM files
└── conftest.py        # Shared fixtures
```

## Running Tests

### All Tests
```bash
pytest
```

### By Layer
```bash
pytest tests/unit/ -m unit
pytest tests/integration/ -m integration
pytest tests/e2e/ -m e2e
```

### With Coverage
```bash
pytest --cov=app --cov-report=html
open htmlcov/index.html
```

### Parallel Execution
```bash
pytest -n auto  # Uses all CPU cores
```

### Specific Test
```bash
pytest tests/unit/services/test_dicom_engine.py::TestDicomEngine::test_parse_valid_dicom -v
```

## Test Markers

- `@pytest.mark.unit` - Unit tests
- `@pytest.mark.integration` - Integration tests
- `@pytest.mark.e2e` - End-to-end tests
- `@pytest.mark.slow` - Tests >1 second
- `@pytest.mark.security` - Security tests

Filter by marker:
```bash
pytest -m "unit and not slow"
pytest -m integration
```

## Test Fixtures

### DICOM Fixtures

Available in `conftest.py`:

- `sample_ct_dicom` - CT image bytes (minimal, no pixels)
- `sample_ct_with_pixels` - CT image bytes with pixel data
- `sample_mri_dicom` - MRI image bytes
- `sample_multiframe_dicom` - Multiframe CT (10 frames)
- `invalid_dicom_missing_sop` - Invalid DICOM (missing SOPInstanceUID)
- `invalid_dicom_missing_study` - Invalid DICOM (missing StudyInstanceUID)
- `valid_dicom_files` - List of valid DICOM file paths
- `edge_case_dicom_files` - Edge case DICOM files
- `invalid_dicom_files` - Invalid DICOM files

### Database Fixtures

- `client` - FastAPI TestClient with test database (SQLite)
- `db` - Async database session

### Fixture Directory

- `fixtures_dir` - Path to fixtures/dicom directory

### Example Usage
```python
def test_store_ct_image(client, sample_ct_dicom):
    response = client.post(
        "/v2/studies",
        files={"file": ("test.dcm", sample_ct_dicom, "application/dicom")}
    )
    assert response.status_code == 200
```

## Writing Tests

### Unit Test Example
```python
import pytest
from app.services.dicom_engine import DicomEngine

@pytest.mark.unit
class TestDicomEngine:
    def test_extract_metadata(self, sample_ct_dicom):
        """Should extract metadata from valid DICOM."""
        metadata = DicomEngine.extract_metadata(sample_ct_dicom)
        assert metadata["StudyInstanceUID"]
        assert metadata["PatientID"] == "FIXTURE-CT-001"
```

### Integration Test Example
```python
import pytest

@pytest.mark.integration
class TestStowRS:
    def test_store_instance(self, client, sample_ct_dicom):
        """Should store DICOM instance successfully."""
        response = client.post(
            "/v2/studies",
            files={"file": ("test.dcm", sample_ct_dicom, "application/dicom")}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ReferencedSOPSequence"]
```

## Coverage Goals

| Component | Target Coverage |
|-----------|----------------|
| Services  | 90%+ |
| Models    | 90%+ |
| Routers   | 98%+ (critical paths) |
| Overall   | 90%+ |

Current configuration (from `pyproject.toml`):
- Coverage tracking enabled by default
- Report format: terminal with missing lines
- Target directory: `app/`

## Performance Benchmarks

Two flavors of perf tests live under `tests/performance/`:

* `test_stow_perf.py` / `test_qido_perf.py` — wall-clock baselines
  against a **live** emulator container (skipped if the service at
  `localhost:8080` isn't reachable).
* `test_stow_benchmark.py` — `pytest-benchmark` micro-benchmarks driven
  by an in-process `TestClient`.  No live container needed.  Captures a
  statistical distribution per scenario for trend-tracking across PRs.

### STOW micro-benchmark

```bash
pytest tests/performance/test_stow_benchmark.py --benchmark-only \
  --benchmark-columns=mean,median,min,max,stddev,rounds \
  --benchmark-autosave
```

Default scenarios are **50** and **500 instances** per request.  The
heavy **5 000-instance** scenario is opt-in:

```bash
pytest tests/performance/test_stow_benchmark.py --benchmark-only --run-stow-5k
```

> **Windows note:** the benchmarks write `.dcm` files to a temp
> directory whose path includes pydicom-generated UIDs (~64 chars
> each).  On Windows the default pytest `tmp_path` rooted under
> `C:\Users\<user>\AppData\Local\Temp\pytest-of-<user>\` exceeds the
> legacy 260-char `MAX_PATH` limit.  Use a short basetemp:
> `pytest ... --basetemp=C:\t\b`.

### Comparing runs

```bash
pytest tests/performance/ --benchmark-compare
```

Saved JSON output lives under `tests/performance/.benchmarks/`.

## Security Testing

Run security scans:
```bash
bandit -r app/ -ll
safety check
```

Note: Security dependencies are included in the `dev` optional dependencies.

## CI/CD Integration

Tests run automatically on:
- Every push to main
- Every pull request
- Nightly (full suite + security scans)

Quality gates:
- ✅ All tests pass
- ✅ Coverage ≥90%
- ✅ No linting errors
- ✅ No security issues (medium+)
- ✅ Performance benchmarks pass

## Troubleshooting

### Tests failing locally but passing in CI
- Check Python version (tests run on 3.12, 3.13)
- Check database state (use fresh test DB)
- Run with `-v` for verbose output

### Coverage not increasing
- Check if new code is in `app/` directory
- Check if tests are actually executing (`pytest --collect-only`)
- Look for excluded patterns in `pyproject.toml`

### Slow test suite
- Use `pytest -n auto` for parallel execution
- Mark slow tests with `@pytest.mark.slow`
- Run fast tests first: `pytest -m "not slow"`

### Database fixture issues
- The `client` fixture uses SQLite (not PostgreSQL)
- Each test gets a fresh database in a temporary directory
- Database schema is created automatically via `Base.metadata.create_all()`

### Async test issues
- Tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- No need to mark tests with `@pytest.mark.asyncio`
- Fixture loop scope is set to `"function"`

## Test Data Generation

Generate DICOM fixture files:
```bash
python -m tests.fixtures.sample_data
```

This creates:
- Valid DICOM files (CT, MRI, multiframe, US, XA)
- Edge case files (empty names, special characters, implicit VR)
- Invalid files (missing required tags)

## Additional Resources

- [pytest Documentation](https://docs.pytest.org/)
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [pytest-cov Coverage](https://pytest-cov.readthedocs.io/)
- [DICOM Standard](https://www.dicomstandard.org/)
- [Azure DICOM Service Docs](https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/)
