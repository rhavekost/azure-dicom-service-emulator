# Contributing

Thanks for your interest in contributing to the Azure DICOM Service Emulator.

## Prerequisites

- Python 3.11+
- Docker Desktop (for integration/e2e tests)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Local Setup

```bash
git clone https://github.com/rhavekost/azure-dicom-service-emulator.git
cd azure-dicom-service-emulator

# Install dependencies (including dev/test extras)
uv sync --all-extras

# Or with pip
pip install -e ".[dev]"
```

## Running Tests

The test suite has three layers. For day-to-day development, the unit and integration tests are sufficient — they run against an in-memory SQLite backend and require no external services.

```bash
# Unit + integration only (fast, no Docker required)
pytest -m "not e2e and not performance" --cov=app

# Unit tests only
pytest tests/unit/ -m unit

# Integration tests only
pytest tests/integration/ -m integration

# E2E tests (requires a running stack: docker compose up -d)
pytest tests/e2e/ -m e2e

# Performance benchmarks (requires a running stack)
pytest tests/performance/ -m performance

# Full run in parallel
pytest -n auto
```

### Coverage

The CI enforces these thresholds:

| Python version | Threshold |
|----------------|-----------|
| 3.14 | 85% |
| 3.12 / 3.13 | 72% |

Run locally with:

```bash
pytest --cov=app --cov-report=html
open htmlcov/index.html
```

## Code Style

- Follow PEP 8 and use type hints on all function signatures.
- Keep functions under 50 lines; files under 800 lines.
- Use `async`/`await` for all database and I/O operations.
- Handle errors explicitly — no silent `except: pass`.
- Never mutate objects in place; return new copies instead.

Pre-commit hooks run `ruff`, `mypy`, and `bandit` automatically:

```bash
pre-commit install
pre-commit run --all-files   # manual check
```

## Adding an Endpoint

1. Add the route to the appropriate router in `app/routers/`.
2. Add or update DB models in `app/models/dicom.py` if needed.
3. Put complex business logic in `app/services/`.
4. Write tests first (RED → GREEN → REFACTOR).
5. Update `README.md` API table and `CHANGELOG.md` `[Unreleased]` section.

## PR Process

1. Fork the repo and create a feature branch from `main`.
2. Make your changes with tests (`pytest -m "not e2e"` must pass).
3. Run `pre-commit run --all-files` and fix any issues.
4. Open a PR against `main` with a clear description of what changed and why.
5. Reference any related issues in the PR body.

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add bulk delete endpoint
fix: correct ETag header on 304 responses
test: add integration tests for change feed
docs: update README with new endpoints
```

## Reporting Bugs

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md). Include the steps to reproduce, expected behavior, actual behavior, and your environment details.

## License

By contributing you agree that your changes will be licensed under the [MIT License](LICENSE).
