"""
tests/conftest.py
-----------------
Top-level pytest configuration for the Enterprise Agent Memory suite.

Responsibilities:
  - Put the cookbooks/ and project root on sys.path so unit + integration
    tests can import mcp_server, shared, and comparison modules directly.
  - Register the unit/integration markers (also declared in pytest.ini) so
    running a single file with -m still filters cleanly.
"""

from __future__ import annotations

import os
import sys


# Absolute project root (the directory that contains cookbooks/, comparison/,
# librechat/, tests/). Resolved once so fixtures do not have to reason about
# the cwd pytest happens to be invoked from.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
COOKBOOKS_DIR = os.path.join(PROJECT_ROOT, "cookbooks")

# Cookbooks first so `from mcp_server.tiers import ...` and
# `from shared.client import ...` resolve without installing the package.
for path in (COOKBOOKS_DIR, PROJECT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)


def pytest_configure(config):
    """Register markers so pytest does not complain when files are run solo."""
    config.addinivalue_line(
        "markers",
        "unit: pure-function tests that require no external services",
    )
    config.addinivalue_line(
        "markers",
        "integration: tests that require a running ClickHouse with seeded data",
    )
