"""
tests/integration/test_schema.py
--------------------------------
Verifies the applied schema against what 01_schema.sql + 02_agent_memory.sql
declare. Runs against the live enterprise_memory database.

Covers:
  - The expected 20 tables (17 domain + 3 conversation memory) exist.
  - agent_memory_long has an HNSW index on content_embedding.
  - agent_memory_hot uses the Memory engine.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


# All tables we expect to see under the enterprise_memory database.
# 17 domain tables (observability=5, telco=4, cybersecurity=8) plus 3 new
# conversation / knowledge tables from 02_agent_memory.sql.
EXPECTED_TABLES = {
    # Observability (5)
    "obs_events_stream",
    "obs_incident_workspace",
    "obs_historical_incidents",
    "obs_services",
    "obs_dependencies",
    # Telco (4)
    "telco_network_state",
    "telco_fault_workspace",
    "telco_network_events",
    "telco_elements",
    "telco_connections",  # note: this makes 5 telco; keep the count accurate below
    # Cybersecurity (8)
    "sec_events_stream",
    "sec_case_workspace",
    "sec_threat_intel",
    "sec_historical_incidents",
    "sec_assets",
    "sec_users",
    "sec_access",
    # Conversation / knowledge (3)
    "agent_memory_hot",
    "agent_memory_long",
    "knowledge_base",
}


def test_all_expected_tables_exist(ch_client):
    """Every table 01_schema.sql + 02_agent_memory.sql declares must be present."""
    result = ch_client.query(
        "SELECT name FROM system.tables WHERE database = 'enterprise_memory'"
    )
    actual = {row[0] for row in result.result_rows}
    missing = EXPECTED_TABLES - actual
    assert not missing, f"Missing expected tables: {missing}"


def test_table_count_is_at_least_twenty(ch_client):
    """Sanity check: at least 20 tables landed (17 domain + 3 memory)."""
    result = ch_client.query(
        "SELECT count() FROM system.tables WHERE database = 'enterprise_memory'"
    )
    count = result.result_rows[0][0]
    assert count >= 20, f"Expected at least 20 tables in enterprise_memory, got {count}"


def test_agent_memory_hot_uses_memory_engine(ch_client):
    """HOT conversation scratchpad must be the volatile Memory engine."""
    result = ch_client.query(
        "SELECT engine FROM system.tables "
        "WHERE database = 'enterprise_memory' AND name = 'agent_memory_hot'"
    )
    assert result.result_rows, "agent_memory_hot not found in system.tables"
    engine = result.result_rows[0][0]
    assert engine == "Memory", f"agent_memory_hot engine is {engine}, expected Memory"


def test_agent_memory_long_has_vector_similarity_index(ch_client):
    """WARM long-term memory must expose a vector_similarity (HNSW) index.

    The seed applies this with allow_experimental_vector_similarity_index=1.
    Syntax on CH 26.3:
      INDEX idx content_embedding TYPE
        vector_similarity('hnsw', 'cosineDistance', 768) GRANULARITY 1000
    """
    result = ch_client.query(
        "SELECT name, type_full, expr FROM system.data_skipping_indices "
        "WHERE database = 'enterprise_memory' AND table = 'agent_memory_long'"
    )
    rows = result.result_rows
    assert rows, "No data_skipping_indices rows for agent_memory_long"
    matches = [
        r for r in rows
        if (
            ("vector_similarity" in (r[1] or "").lower()
             or "hnsw" in (r[1] or "").lower())
            and "content_embedding" in (r[2] or "")
        )
    ]
    assert matches, (
        "Did not find a vector_similarity/HNSW index on content_embedding; "
        f"indices present: {rows}"
    )


def test_hot_tables_are_memory_engine(ch_client):
    """All three domain workspaces + event streams should use Memory engine."""
    memory_engine_tables = [
        "obs_events_stream",
        "obs_incident_workspace",
        "telco_network_state",
        "telco_fault_workspace",
        "sec_events_stream",
        "sec_case_workspace",
    ]
    placeholders = ",".join([f"'{t}'" for t in memory_engine_tables])
    result = ch_client.query(
        "SELECT name, engine FROM system.tables "
        f"WHERE database = 'enterprise_memory' AND name IN ({placeholders})"
    )
    found = {row[0]: row[1] for row in result.result_rows}
    for table in memory_engine_tables:
        assert found.get(table) == "Memory", (
            f"{table} engine should be Memory, got {found.get(table)!r}"
        )
