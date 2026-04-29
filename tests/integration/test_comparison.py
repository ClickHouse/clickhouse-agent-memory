"""
tests/integration/test_comparison.py
------------------------------------
Run both comparison agents end-to-end and assert they complete without
exception and return the expected synthesis dict shape.

The stitched agent expects Pinecone + Redis + Neo4j + Postgres. When those
are unreachable (which is the common case in CI), each client falls back
to a clearly-marked in-memory double so the scenario still completes.

The clickhouse agent expects only ClickHouse, which the ch_client fixture
already guarantees is reachable.

HOT-window widening: the clickhouse agent defaults to `INTERVAL 10 MINUTE`
for its "live incident" narrative. Tests widen that to 7 days via the
COMPARE_HOT_WINDOW_MINUTES env var so the assertion that at least one
trigger event exists is robust to seed age. The test is not measuring
freshness semantics; it is measuring that the end-to-end pipeline runs.
"""

from __future__ import annotations

import importlib
import os

import pytest


pytestmark = pytest.mark.integration

# Widen the "live" window so the test is robust to seed age. Demo runs keep
# the 10-minute default for the narrative; tests do not. 7 days covers most
# "seeded once, ran tests later in the week" workflows.
os.environ.setdefault("COMPARE_HOT_WINDOW_MINUTES", "10080")


# The keys the synthesis step returns from both agents.
EXPECTED_KEYS = {
    "incident_id",
    "trigger",
    "blast_radius",
    "similar",
    "playbook",
    "memory_sources",
    "backends_used",
}


def test_clickhouse_comparison_agent_runs_end_to_end(seeded):
    """comparison.clickhouse.agent.main returns a full context dict."""
    module = importlib.import_module("comparison.clickhouse.agent")
    ctx = module.main("svc-payments")
    assert isinstance(ctx, dict)
    missing = EXPECTED_KEYS - ctx.keys()
    assert not missing, f"clickhouse agent missing keys: {missing}"
    assert ctx["backends_used"] == ["ClickHouse"]


def test_stitched_comparison_agent_runs_end_to_end(seeded):
    """comparison.stitched.agent.main tolerates all four services being down.

    The seeded fixture only ensures ClickHouse is up. Redis, Pinecone, Neo4j
    and Postgres are expected to fail to connect in this environment; each
    client has an in-memory double that lets the scenario complete.
    """
    module = importlib.import_module("comparison.stitched.agent")
    ctx = module.main("svc-payments")
    assert isinstance(ctx, dict)
    missing = EXPECTED_KEYS - ctx.keys()
    assert not missing, f"stitched agent missing keys: {missing}"
    # The stitched agent advertises four backing services.
    assert set(ctx["backends_used"]) == {"Pinecone", "Redis", "Neo4j", "Postgres"}
