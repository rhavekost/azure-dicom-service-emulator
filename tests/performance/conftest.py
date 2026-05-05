"""Pytest configuration for the performance benchmark suite.

Houses CLI flags that gate heavyweight scenarios (e.g. the 5 000-instance
STOW benchmark).  ``pytest_addoption`` only fires when registered at the
``conftest`` level, so the option lives here instead of the test file.
"""

from __future__ import annotations


def pytest_addoption(parser):
    """Register opt-in flags used by the performance benchmark scenarios."""
    group = parser.getgroup("stow-benchmark")
    group.addoption(
        "--run-stow-5k",
        action="store_true",
        default=False,
        help="Run the heavyweight 5 000-instance STOW benchmark (slow).",
    )
