"""
tests/integration/test_domain_tools.py
--------------------------------------
End-to-end coverage of every domain MCP tool for every domain it supports.
The tools are imported directly (they are decorated with @mcp.tool() but
remain plain Python callables), and we assert on the envelope they return.

Tools exercised:
  - search_events         (all three domains)
  - create_case    (all three domains)
  - semantic_search      (all three domains)
  - get_record      (runbook + threat_intel)
  - find_related_entities   (all three domains)
"""

from __future__ import annotations

import pytest

# Importing server also registers conversation tools at the bottom of that
# module. We rely on the session-scoped ch_client/seeded fixtures from
# integration/conftest.py to verify the cluster is live before any of this
# runs. Unit tests do not import this module.
from mcp_server.server import (
    search_events,
    create_case,
    semantic_search,
    get_record,
    find_related_entities,
)


pytestmark = pytest.mark.integration


DOMAINS = ("observability", "telco", "cybersecurity")


# ---------------------------------------------------------------------------
# HOT: search_events
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain", DOMAINS)
def test_memory_hot_scan_returns_hot_envelope(seeded, domain):
    """Scan the live stream for each domain and assert the envelope shape."""
    env = search_events(domain=domain, limit=5)
    assert env["tier"] == "HOT"
    assert env["domain"] == domain
    assert env["operation"] == "search_events"
    assert env["row_count"] >= 0
    # The banner is load-bearing: the LibreChat agent echoes it verbatim.
    assert env["tier"] == "HOT"
    # Insights expose the filter + window applied.
    assert "filter_applied" in env["insights"]
    assert "window_minutes" in env["insights"]


@pytest.mark.parametrize("domain", DOMAINS)
def test_memory_hot_scan_with_filter_does_not_raise(seeded, domain):
    """A filter value should not crash the tool even if it matches no rows."""
    env = search_events(domain=domain, filter="no-such-entity-zzz", limit=5)
    assert env["tier"] == "HOT"
    assert env["row_count"] >= 0


def test_memory_hot_scan_rejects_unknown_domain(seeded):
    with pytest.raises(ValueError):
        search_events(domain="not-a-domain")


# ---------------------------------------------------------------------------
# HOT: create_case
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain", DOMAINS)
def test_memory_hot_workspace_builds_grouped_summary(seeded, domain):
    """Materialise a case workspace and assert the envelope + insights."""
    case_id = f"TEST-123-{domain}"
    env = create_case(domain=domain, case_id=case_id)
    assert env["tier"] == "HOT"
    assert env["domain"] == domain
    # insights.case_id is always echoed back so the caller can correlate.
    assert env["insights"]["case_id"] == case_id
    # Summary table varies by domain: obs=obs_incident_workspace, etc.
    assert env["insights"]["workspace_table"].endswith("_workspace")


# ---------------------------------------------------------------------------
# WARM: semantic_search
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain", DOMAINS)
def test_memory_warm_search_returns_similarity_ranked_rows(seeded, domain):
    """Top-k vector search returns at most k rows, each with a distance score."""
    env = semantic_search(domain=domain, query="connection pool", k=3)
    assert env["tier"] == "WARM"
    assert env["domain"] == domain
    assert env["operation"] == "semantic_search"
    assert env["row_count"] <= 3
    # Every returned row exposes similarity_distance.
    for row in env["rows_preview"]:
        assert "similarity_distance" in row


def test_memory_warm_search_rejects_unknown_domain(seeded):
    with pytest.raises(ValueError):
        semantic_search(domain="alien-domain", query="anything", k=3)


# ---------------------------------------------------------------------------
# WARM: get_record
# ---------------------------------------------------------------------------

def test_memory_warm_lookup_observability_runbook(seeded, ch_client):
    """Pull a real seeded incident_id first, then ensure runbook lookup finds it."""
    result = ch_client.query(
        "SELECT incident_id FROM enterprise_memory.obs_historical_incidents LIMIT 1"
    )
    assert result.result_rows, "obs_historical_incidents has no rows"
    incident_id = str(result.result_rows[0][0])

    env = get_record(
        domain="observability",
        kind="runbook",
        identifier=incident_id,
    )
    assert env["tier"] == "WARM"
    assert env["row_count"] == 1
    # Insights surface the resolution snippet for agent display.
    assert "resolution" in env["insights"]


def test_memory_warm_lookup_cybersecurity_threat_intel(seeded):
    """Threat-intel search over FIN6 should return at least one match."""
    env = get_record(
        domain="cybersecurity",
        kind="threat_intel",
        query="FIN6",
        k=5,
    )
    assert env["tier"] == "WARM"
    assert env["operation"] == "threat_intel_lookup"
    assert env["row_count"] >= 1


def test_memory_warm_lookup_rejects_unsupported_pair(seeded):
    # observability does not support threat_intel lookup.
    with pytest.raises(ValueError):
        get_record(
            domain="observability", kind="threat_intel", query="anything", k=3
        )


def test_memory_warm_lookup_runbook_requires_identifier(seeded):
    with pytest.raises(ValueError):
        get_record(domain="observability", kind="runbook")


def test_memory_warm_lookup_threat_intel_requires_query(seeded):
    with pytest.raises(ValueError):
        get_record(
            domain="cybersecurity", kind="threat_intel", query=""
        )


# ---------------------------------------------------------------------------
# GRAPH: find_related_entities
# ---------------------------------------------------------------------------

# (domain, entity) pairs whose seed data is guaranteed to populate the graph.
GRAPH_CASES = [
    ("observability", "svc-payments"),
    ("telco", "core-router-01"),
    ("cybersecurity", "user-006"),
]


@pytest.mark.parametrize("domain,entity", GRAPH_CASES)
def test_memory_graph_traverse_returns_neighbours(seeded, domain, entity):
    """Each seeded entity should have at least one reachable neighbour."""
    env = find_related_entities(domain=domain, entity=entity, max_hops=2)
    assert env["tier"] == "GRAPH"
    assert env["domain"] == domain
    assert env["operation"] == "find_related_entities"
    assert env["row_count"] > 0, (
        f"{domain}.find_related_entities({entity}) returned no rows; "
        "is the graph seeded?"
    )
    # Insights summarise direct vs indirect neighbours.
    assert "direct_neighbours" in env["insights"]
    assert "indirect_neighbours" in env["insights"]


def test_memory_graph_traverse_requires_entity(seeded):
    with pytest.raises(ValueError):
        find_related_entities(domain="observability", entity="", max_hops=2)


def test_memory_graph_traverse_rejects_unknown_domain(seeded):
    with pytest.raises(ValueError):
        find_related_entities(domain="martian", entity="anything", max_hops=1)
