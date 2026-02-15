# Comprehensive Testing Strategy Design

**Date:** 2026-02-15
**Author:** Testing & QA Initiative
**Status:** Approved

## Executive Summary

This document outlines a comprehensive testing strategy for the Azure DICOM Service Emulator to achieve production-grade quality with 90%+ code coverage, 98%+ coverage on critical paths, zero medium+ security issues, and performance benchmarks (p95 < 500ms).

## Goals & Success Criteria

### Coverage Targets (Aggressive Thresholds)
- **Overall coverage:** 90%+
- **Critical paths (STOW/WADO/QIDO):** 98%+
- **Security:** Zero medium+ security issues
- **Performance:** Response time p95 < 500ms, load testing 100+ concurrent users

### Testing Layers
- ✅ Unit Tests (200-300 tests)
- ✅ Integration Tests (50-75 tests)
- ✅ E2E Tests (10-15 tests)
- ✅ Performance Tests (benchmarks + load testing)
- ✅ Security Tests (scanning + penetration testing)

## Testing Architecture

### Testing Pyramid (Bottom-Up Execution)
```
        ┌─────────────────┐
        │   E2E Tests     │  ← Full workflows, smoke tests
        │  (10-15 tests)  │
        └─────────────────┘
       ┌──────────────────────┐
       │ Integration Tests    │  ← API endpoints, database
       │   (50-75 tests)      │
       └──────────────────────┘
    ┌───────────────────────────────┐
    │      Unit Tests               │  ← Services, models, utilities
    │     (200-300 tests)           │
    └───────────────────────────────┘
```

### Phasing Approach
**Bottom-Up (Recommended):**
1. Unit tests (services, utilities, models) → build foundation
2. Integration tests (API endpoints) → validate interactions
3. E2E tests → ensure workflows function
4. Performance/Security tests → production readiness

## Test Organization & Structure

### Directory Structure
```
tests/
├── unit/
│   ├── models/
│   │   ├── test_dicom_model.py
│   │   └── test_events_model.py
│   ├── services/
│   │   ├── test_dicom_engine.py
│   │   ├── test_multipart.py
│   │   ├── test_frame_extraction.py
│   │   ├── test_frame_cache.py
│   │   ├── test_image_rendering.py
│   │   ├── test_expiry.py
│   │   ├── test_upsert.py
│   │   ├── test_search_utils.py
│   │   └── test_ups_state_machine.py
│   └── utils/
│       └── test_validators.py
├── integration/
│   ├── test_stow_rs.py
│   ├── test_wado_rs.py
│   ├── test_qido_rs.py
│   ├── test_changefeed_api.py
│   ├── test_extended_query_tags_api.py
│   ├── test_operations_api.py
│   └── test_ups_api.py
├── e2e/
│   ├── test_dicom_workflow.py
│   ├── test_changefeed_workflow.py
│   └── test_ups_workflow.py
├── performance/
│   ├── test_stow_benchmark.py
│   ├── test_qido_benchmark.py
│   └── load_tests/
│       └── locustfile.py
├── security/
│   └── test_security_scan.py
├── fixtures/
│   ├── dicom/
│   │   ├── valid/
│   │   │   ├── ct_minimal.dcm
│   │   │   ├── ct_with_pixels.dcm
│   │   │   ├── mri_t1.dcm
│   │   │   ├── mri_t2.dcm
│   │   │   └── multiframe_ct.dcm
│   │   ├── edge_cases/
│   │   │   ├── empty_tags.dcm
│   │   │   ├── special_chars.dcm
│   │   │   └── compressed_transfer_syntax.dcm
│   │   └── invalid/
│   │       ├── missing_sop_uid.dcm
│   │       ├── corrupted_header.dcm
│   │       └── invalid_vr.dcm
│   ├── factories.py
│   └── sample_data.py
├── conftest.py
└── pytest.ini
```

### Migration Plan
- Audit existing 30+ tests
- Keep passing tests, move to appropriate directories (unit/ or integration/)
- Refactor tests that need enhancement
- Delete redundant or obsolete tests

## Testing Tools & Dependencies

### Standard QA Toolkit
```toml
[project.optional-dependencies]
dev = [
    # Existing
    "ruff>=0.8.0",
    "mypy>=1.13.0",
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",

    # Coverage & Reporting
    "pytest-cov>=4.1.0",
    "coverage[toml]>=7.4.0",

    # Performance Testing
    "pytest-benchmark>=4.0.0",
    "locust>=2.20.0",

    # Security
    "bandit>=1.7.6",
    "safety>=3.0.0",

    # Test Execution
    "pytest-xdist>=3.5.0",  # Parallel execution
    "pytest-html>=4.1.0",   # HTML reports

    # Test Data
    "faker>=22.0.0",
    "factory-boy>=3.3.0",
]
```

### Configuration Files
- `.coveragerc` - Coverage settings (90% threshold, exclude patterns)
- `pytest.ini` - Pytest configuration (test discovery, markers)
- `.github/workflows/tests.yml` - CI/CD workflow
- `.pre-commit-config.yaml` - Pre-commit hooks
- `bandit.yaml` - Security scan configuration

### Test Markers
```ini
[pytest]
markers =
    unit: Unit tests
    integration: Integration tests
    e2e: End-to-end tests
    slow: Slow running tests
    security: Security tests
```

## Test Fixtures & Data Management

### DICOM Fixture Library Strategy
- **Primary:** Fixture library with curated test DICOM files
- **Secondary:** External test datasets (NEMA, dicom.offis.de) if needed

### Test Factories
```python
# tests/fixtures/factories.py
class DicomFactory:
    """Factory for creating test DICOM files."""

    @staticmethod
    def create_ct_image(patient_id="TEST-001", ...):
        """Create a minimal CT DICOM instance."""

    @staticmethod
    def create_mri_image(...):
        """Create MRI instance."""

    @staticmethod
    def create_multiframe(...):
        """Create multiframe instance."""

    @staticmethod
    def create_invalid_dicom(...):
        """Create DICOM with missing required tags."""
```

### Shared Fixtures
```python
# tests/conftest.py
@pytest.fixture
def sample_ct_dicom():
    """Returns CT DICOM bytes."""

@pytest.fixture
def test_study():
    """Creates a complete study with multiple series."""

@pytest.fixture
async def db_with_test_data(db):
    """Database populated with test studies."""
```

## Unit Testing Strategy

### Target: 90%+ Coverage on Services/Models/Utilities

### Critical Unit Test Areas
1. **DicomEngine** - Parsing, validation, metadata extraction, tag encoding
2. **Frame extraction** - Extract frames, handle compressed transfer syntaxes
3. **Search utilities** - Wildcard matching, fuzzy search, UID lists
4. **Expiry logic** - Date calculations, cleanup rules
5. **UPS state machine** - State transitions, validation
6. **Event providers** - All provider types (in-memory, file, webhook, Azure Queue)
7. **Multipart handling** - Boundary parsing, content-type detection

### Example Unit Test
```python
# tests/unit/services/test_dicom_engine.py
class TestDicomEngine:
    def test_parse_valid_dicom(self, sample_ct_dicom):
        """Should extract metadata from valid DICOM."""
        metadata = DicomEngine.extract_metadata(sample_ct_dicom)
        assert metadata["StudyInstanceUID"]
        assert metadata["PatientID"] == "TEST-001"

    def test_parse_missing_required_tag(self):
        """Should raise error for missing SOPInstanceUID."""
        invalid_dcm = DicomFactory.create_invalid_dicom(
            missing_tags=["SOPInstanceUID"]
        )
        with pytest.raises(ValueError, match="Missing required tag"):
            DicomEngine.extract_metadata(invalid_dcm)

    @pytest.mark.parametrize("modality", ["CT", "MRI", "US", "XA"])
    def test_modality_specific_validation(self, modality):
        """Should validate modality-specific attributes."""
```

## Integration Testing Strategy

### Target: 98%+ Coverage on Critical Paths (STOW/WADO/QIDO)

### Critical Integration Test Coverage
- **STOW-RS**: Success, duplicates, warnings, multipart, various transfer syntaxes
- **WADO-RS**: Retrieve study/series/instance, metadata, frames, rendered images, accept headers
- **QIDO-RS**: Search studies/series/instances, wildcards, fuzzy matching, UID lists, pagination
- **Change Feed**: Entries created on create/update/delete, time filtering, pagination
- **Extended Query Tags**: CRUD operations, reindexing, query status
- **UPS-RS**: Create/retrieve/update/cancel workitems, state transitions, subscriptions

### Example Integration Test
```python
# tests/integration/test_stow_rs.py
class TestStowRS:
    def test_store_single_instance_success(self, client, sample_ct_dicom):
        """Should store valid DICOM and return 200."""
        response = client.post(
            "/v2/studies",
            files={"file": ("test.dcm", sample_ct_dicom, "application/dicom")},
            headers={"Content-Type": "multipart/related; ..."}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ReferencedSOPSequence"]
        # Verify database entry
        # Verify file written to disk
        # Verify change feed entry created
        # Verify event published

    def test_store_duplicate_instance_returns_409(self, client, sample_ct_dicom):
        """Should reject duplicate with 409 Conflict."""
        client.post("/v2/studies", ...)  # Store once
        response = client.post("/v2/studies", ...)  # Store again
        assert response.status_code == 409
```

## E2E, Performance & Security Testing

### E2E Tests (End-to-End Workflows)
```python
# tests/e2e/test_dicom_workflow.py
class TestCompleteDICOMWorkflow:
    async def test_store_search_retrieve_delete_workflow(self, client):
        """Complete workflow: STOW → QIDO → WADO → DELETE → verify change feed."""
        # 1. Store a study
        # 2. Search for the study
        # 3. Retrieve metadata
        # 4. Retrieve instances
        # 5. Delete study
        # 6. Verify change feed shows delete
```

### Performance Tests
```python
# tests/performance/test_stow_benchmark.py
def test_stow_single_instance_benchmark(benchmark, client, sample_ct_dicom):
    """Benchmark STOW-RS performance (target: <500ms p95)."""
    result = benchmark(lambda: client.post("/v2/studies", ...))
    assert result.stats["mean"] < 0.5  # 500ms

# tests/performance/load_tests/locustfile.py
class DicomUser(HttpUser):
    """Load test with 100+ concurrent users."""
    wait_time = between(1, 3)

    @task(3)
    def store_instance(self):
        self.client.post("/v2/studies", ...)

    @task(5)
    def search_studies(self):
        self.client.get("/v2/studies?PatientID=TEST-*")
```

### Security Tests
```python
# tests/security/test_security_scan.py
def test_bandit_security_scan():
    """Run bandit security scanner (zero medium+ issues)."""

def test_sql_injection_protection(client):
    """Test QIDO-RS against SQL injection."""
    response = client.get("/v2/studies?PatientID='; DROP TABLE--")
    assert response.status_code in [200, 400]

def test_path_traversal_protection(client):
    """Test WADO-RS against path traversal."""
    response = client.get("/v2/studies/../../etc/passwd")
    assert response.status_code == 404
```

## CI/CD Integration & Automation

### GitHub Actions Workflow
```yaml
# .github/workflows/tests.yml
name: Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
      - name: Install dependencies
        run: pip install -e ".[dev]"
      - name: Run linting
        run: ruff check .
      - name: Run security scan
        run: bandit -r app/ -ll && safety check
      - name: Run tests with coverage
        run: |
          pytest tests/unit/ -v --cov=app --cov-report=xml
          pytest tests/integration/ -v --cov-append --cov-report=xml
          pytest tests/e2e/ -v
      - name: Check coverage threshold
        run: coverage report --fail-under=90
      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v4
```

### Pre-commit Hooks
```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/PyCQA/bandit
    rev: 1.7.6
    hooks:
      - id: bandit
        args: [-ll, -r, app/]
```

### Quality Gates
- ✅ All tests pass
- ✅ Coverage ≥90% (98%+ on critical paths)
- ✅ No medium+ security issues
- ✅ Linting passes
- ✅ Performance benchmarks pass

## Implementation Roadmap

### Phase 1: Foundation (Week 1-2)
- [ ] Install and configure all testing tools
- [ ] Reorganize test directory structure
- [ ] Create DICOM fixture library (20+ samples)
- [ ] Set up test factories and shared fixtures
- [ ] Audit existing 30+ tests, categorize as keep/refactor/delete
- [ ] Migrate passing tests to new structure
- **Deliverable:** Test infrastructure ready, fixtures created

### Phase 2: Unit Tests (Week 3-4)
- [ ] Unit test all services (dicom_engine, frame_extraction, search_utils, etc.)
- [ ] Unit test all models (DICOM, events, workitems)
- [ ] Unit test event providers (all 4 types)
- [ ] Unit test UPS state machine
- [ ] Target: 90%+ coverage on app/services/ and app/models/
- **Deliverable:** 200-300 unit tests, 90%+ coverage

### Phase 3: Integration Tests (Week 5-6)
- [ ] Integration tests for STOW-RS (target 98%+)
- [ ] Integration tests for WADO-RS (target 98%+)
- [ ] Integration tests for QIDO-RS (target 98%+)
- [ ] Integration tests for Change Feed API
- [ ] Integration tests for Extended Query Tags API
- [ ] Integration tests for UPS-RS API
- [ ] Integration tests for Operations API
- **Deliverable:** 50-75 integration tests, 98%+ on critical paths

### Phase 4: E2E, Performance, Security (Week 7)
- [ ] E2E workflow tests (10-15 scenarios)
- [ ] Performance benchmarks for all critical endpoints
- [ ] Load testing with Locust (100+ concurrent users)
- [ ] Security scanning with Bandit (zero medium+ issues)
- [ ] SQL injection and path traversal tests
- **Deliverable:** Complete test coverage at all layers

### Phase 5: CI/CD & Documentation (Week 8)
- [ ] GitHub Actions workflow setup
- [ ] Pre-commit hooks configuration
- [ ] Codecov integration
- [ ] Test documentation (README, test plan doc)
- [ ] Performance baseline documentation
- [ ] Final validation - all quality gates pass
- **Deliverable:** Production-ready test suite with automation

## Success Metrics

### Quantitative Metrics
- **Code Coverage:** 90%+ overall, 98%+ on critical paths
- **Test Count:** 200-300 unit tests, 50-75 integration tests, 10-15 E2E tests
- **Security:** Zero medium+ issues from Bandit/Safety scans
- **Performance:** p95 < 500ms for all critical endpoints
- **Load:** Support 100+ concurrent users

### Qualitative Metrics
- All critical user workflows validated end-to-end
- Clear test documentation and maintainable test code
- Fast test execution (parallel execution enabled)
- Automated quality gates prevent regressions
- High confidence for production deployment

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Large DICOM files slow down tests | High | Use minimal test files, lazy loading, parallel execution |
| Flaky async tests | Medium | Proper async fixtures, deterministic test data |
| Coverage gaps in edge cases | Medium | Systematic audit, parametrized tests for variations |
| Performance tests unreliable in CI | Low | Establish baselines, allow variance, run nightly |
| Test maintenance burden | Medium | Good organization, DRY principles, clear documentation |

## Conclusion

This comprehensive testing strategy provides a clear path to production-grade quality for the Azure DICOM Service Emulator. By following the 8-week roadmap with bottom-up execution, we will achieve:

- **90%+ code coverage** with 98%+ on critical paths
- **Zero medium+ security issues**
- **Performance validated** at <500ms p95
- **Automated quality gates** via CI/CD
- **Maintainable test suite** with clear organization

The aggressive quality thresholds ensure confidence for production deployment while the phased approach makes the work manageable and trackable.
