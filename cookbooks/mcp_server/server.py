"""
mcp_server/server.py
--------------------
Enterprise Agent Memory -- MCP server for LibreChat.

Exposes the domain-tier tools. Tool names use AI-engineer vocabulary so
a reader recognises the retrieval pattern immediately:

  search_events     -- HOT tier, live event / state tail
  create_case   -- HOT tier, per-case scratchpad workspace
  semantic_search      -- WARM tier, vector similarity over historical data
  get_record         -- WARM tier, runbook by id OR threat-intel semantic lookup
  find_related_entities       -- GRAPH tier, multi-hop relationship traversal

Conversation-memory tools live in mcp_server.conversation.
"""

from __future__ import annotations

import os
import sys
from typing import Any

# Reuse the cookbook client for ClickHouse + embeddings.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.client import get_ch_client, embed, query_to_dicts, query_with_stats

from mcp_server.app import mcp, check_embedding_dim_consistency
from mcp_server.tiers import DOMAINS, envelope, timed
from mcp_server.queries import (
    HOT_SCAN_SQL,
    HOT_WORKSPACE_LOAD_SQL,
    HOT_WORKSPACE_SUMMARY_SQL,
    WARM_VECTOR_SQL,
    WARM_LOOKUP_SQL,
    GRAPH_TRAVERSE_SQL,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_domain(domain: str) -> str:
    domain = (domain or "").lower()
    if domain not in DOMAINS:
        raise ValueError(
            f"Unknown domain '{domain}'. Must be one of: {', '.join(DOMAINS)}"
        )
    return domain


def _embed_literal(text: str) -> str:
    vec = embed(text)
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


# ---------------------------------------------------------------------------
# HOT tier -- search_events
# ---------------------------------------------------------------------------

@mcp.tool()
def search_events(
    domain: str,
    filter: str = "",
    minutes: int = 15,
    limit: int = 20,
) -> dict[str, Any]:
    """HOT tier: tail the live event / state stream.

    Uses ClickHouse Memory engine tables for sub-5ms, volatile retrieval
    of recent high-severity signals. filter narrows by service (obs),
    element/region (telco), or user/asset/ip (cybersecurity).

    Args:
        domain: observability | telco | cybersecurity
        filter: optional entity to narrow by (empty = no filter)
        minutes: lookback window in minutes (telco ignores it)
        limit: maximum rows to return
    """
    domain = _assert_domain(domain)
    client = get_ch_client()
    sql = HOT_SCAN_SQL[domain]
    params = {"service": filter or "", "minutes": int(minutes), "limit": int(limit)}
    (rows, stats), elapsed = timed(query_with_stats, client, sql, params)

    precision = {
        "filters_applied": [
            f"filter={filter}" if filter else "filter=(open)",
            "level IN ('ERROR','CRITICAL')" if domain != "telco" else "status IN ('degraded','down') OR error_rate > 1%",
            f"ts >= now() - INTERVAL {minutes} MINUTE" if domain != "telco" else "last_seen implied",
            f"LIMIT {limit}",
        ],
        "index_hint": "Memory engine (in-RAM, no secondary index, full table scan is cheap)",
        "embedding_dim": None,
    }
    insights: dict[str, Any] = {"filter_applied": filter or "(none)", "window_minutes": minutes}
    if rows:
        if domain == "observability":
            top = rows[0]
            insights["top_service"] = top.get("service")
            insights["top_error"] = top.get("error_code") or (top.get("message") or "")[:80]
        elif domain == "telco":
            top = rows[0]
            insights["worst_element"] = top.get("element_id")
            insights["status"] = top.get("status")
        elif domain == "cybersecurity":
            top = rows[0]
            insights["top_event_type"] = top.get("event_type")
            insights["top_user"] = top.get("user_id")

    return envelope(
        tier="HOT",
        domain=domain,
        operation="search_events",
        sql=sql,
        latency_ms=elapsed,
        rows=rows,
        insights=insights,
        precision=precision,
        scan_stats=stats,
    )


# ---------------------------------------------------------------------------
# HOT tier -- create_case
# ---------------------------------------------------------------------------

@mcp.tool()
def create_case(
    domain: str,
    case_id: str,
    trace_id: str = "",
) -> dict[str, Any]:
    """HOT tier: open a per-case investigation workspace + grouped summary.

    Copies correlated events from the live stream into the domain's
    workspace Memory table, then aggregates by the natural grouping key
    (service / element / event_type) so the agent sees an at-a-glance
    fault picture.

    Args:
        domain: observability | telco | cybersecurity
        case_id: identifier for this investigation (e.g. INC-1234)
        trace_id: observability-only, correlate by a specific trace id
    """
    domain = _assert_domain(domain)
    client = get_ch_client()

    workspace_table = {
        "observability": "obs_incident_workspace",
        "telco": "telco_fault_workspace",
        "cybersecurity": "sec_case_workspace",
    }[domain]

    client.command(f"TRUNCATE TABLE IF EXISTS enterprise_memory.{workspace_table}")

    load_sql = HOT_WORKSPACE_LOAD_SQL[domain]
    client.command(load_sql, parameters={"case_id": case_id, "trace_id": trace_id})

    summary_sql = HOT_WORKSPACE_SUMMARY_SQL[domain]
    (rows, stats), elapsed = timed(query_with_stats, client, summary_sql, {"case_id": case_id})

    total_events = sum(
        r.get("errors", 0) + r.get("criticals", 0) + r.get("events", 0)
        for r in rows
    ) if rows else 0
    insights = {
        "case_id": case_id,
        "workspace_table": workspace_table,
        "groups_summarised": len(rows),
        "events_loaded": total_events,
    }

    precision = {
        "filters_applied": [
            f"case_id={case_id}",
            "group by service / element / event_type",
        ],
        "index_hint": "Memory engine (INSERT ... SELECT + GROUP BY, single-pass)",
        "embedding_dim": None,
    }

    return envelope(
        tier="HOT",
        domain=domain,
        operation="create_case",
        sql=load_sql + "\n\n-- Group by the natural key so we see the fault shape:\n" + summary_sql,
        latency_ms=elapsed,
        rows=rows,
        insights=insights,
        precision=precision,
        scan_stats=stats,
    )


# ---------------------------------------------------------------------------
# WARM tier -- semantic_search
# ---------------------------------------------------------------------------

@mcp.tool()
def semantic_search(
    domain: str,
    query: str,
    k: int = 3,
) -> dict[str, Any]:
    """WARM tier: semantic search over historical records via cosineDistance.

    Embeds `query` and ranks the persistent MergeTree table for the
    chosen domain by vector similarity. Use this when the user asks
    "have we seen this pattern before" or "find past incidents like X".

    Args:
        domain: observability | telco | cybersecurity
        query: free-text description of the current situation
        k: number of neighbours to return (default 3)
    """
    domain = _assert_domain(domain)
    client = get_ch_client()

    emb_literal = _embed_literal(query)
    sql_template = WARM_VECTOR_SQL[domain]
    sql = sql_template.replace("{emb}", emb_literal)
    # 180-day recall window prunes whole monthly partitions before HNSW runs.
    # Wide enough for realistic "have we seen this before" recall, narrow
    # enough to keep the ranker out of ancient history.
    days = int(os.environ.get("SEMANTIC_SEARCH_DAYS", "180"))
    (rows, stats), elapsed = timed(
        query_with_stats, client, sql, {"k": int(k), "days": days}
    )

    insights: dict[str, Any] = {"query_text": query[:160], "k": k}
    if rows:
        top = rows[0]
        insights["top_match"] = top.get("title") or top.get("description", "")[:120]
        insights["similarity_distance"] = top.get("similarity_distance")
        insights["top_root_cause"] = (top.get("root_cause") or "")[:200]

    sql_for_display = sql_template.replace("{emb}", "[<query embedding vector>]")
    precision = {
        "filters_applied": [
            f"ts >= now() - INTERVAL {days} DAY  -- partition pruning",
            f"ORDER BY cosineDistance(embedding, query_vec) ASC",
            f"LIMIT {k}",
        ],
        "index_hint": "HNSW vector_similarity('hnsw', 'cosineDistance', 768) on `embedding` column; monthly partition pruning via PARTITION BY toYYYYMM(ts)",
        "embedding_dim": 768,
    }
    return envelope(
        tier="WARM",
        domain=domain,
        operation="semantic_search",
        sql=sql_for_display,
        latency_ms=elapsed,
        rows=rows,
        insights=insights,
        precision=precision,
        scan_stats=stats,
    )


# ---------------------------------------------------------------------------
# WARM tier -- get_record
# ---------------------------------------------------------------------------

@mcp.tool()
def get_record(
    domain: str,
    kind: str,
    identifier: str = "",
    query: str = "",
    k: int = 5,
) -> dict[str, Any]:
    """WARM tier: fetch a specific record.

    Two modes:
      kind="runbook"      -- deterministic lookup by incident/event id
      kind="threat_intel" -- semantic lookup over threat intel (cybersecurity)

    Args:
        domain: observability | telco | cybersecurity
        kind: runbook | threat_intel
        identifier: record id for kind=runbook
        query: free-text for kind=threat_intel
        k: neighbours for threat_intel lookup
    """
    domain = _assert_domain(domain)
    kind = (kind or "").lower()
    key = (domain, kind)
    if key not in WARM_LOOKUP_SQL:
        valid = [f"{d}:{k}" for (d, k) in WARM_LOOKUP_SQL.keys()]
        raise ValueError(f"Unsupported lookup {domain}:{kind}. Valid: {valid}")

    client = get_ch_client()
    sql_template = WARM_LOOKUP_SQL[key]
    params: dict[str, Any] = {}

    if kind == "threat_intel":
        if not query:
            raise ValueError("query is required for kind=threat_intel")
        emb_literal = _embed_literal(query)
        sql = sql_template.replace("{emb}", emb_literal)
        params["k"] = int(k)
        sql_for_display = sql_template.replace("{emb}", "[<query embedding vector>]")
        operation = "threat_intel_lookup"
    else:
        if not identifier:
            raise ValueError("identifier is required for kind=runbook")
        sql = sql_template
        params["identifier"] = identifier
        sql_for_display = sql
        operation = "fetch_runbook"

    (rows, stats), elapsed = timed(query_with_stats, client, sql, params)
    insights = {
        "kind": kind,
        "identifier": identifier or None,
        "query": query or None,
        "matches": len(rows),
    }
    if rows and kind == "runbook":
        insights["resolution"] = (rows[0].get("resolution") or rows[0].get("response") or "")[:240]

    if kind == "threat_intel":
        precision = {
            "filters_applied": [
                f"ORDER BY cosineDistance(embedding, query_vec) ASC",
                f"LIMIT {k}",
            ],
            "index_hint": "HNSW vector_similarity on `embedding` (threat intel)",
            "embedding_dim": 768,
        }
    else:
        precision = {
            "filters_applied": [f"WHERE incident_id = '{identifier}'"],
            "index_hint": "Primary-key lookup on incident_id (MergeTree ORDER BY prefix)",
            "embedding_dim": None,
        }

    return envelope(
        tier="WARM",
        domain=domain,
        operation=operation,
        sql=sql_for_display,
        latency_ms=elapsed,
        rows=rows,
        insights=insights,
        precision=precision,
        scan_stats=stats,
    )


# ---------------------------------------------------------------------------
# GRAPH tier -- find_related_entities
# ---------------------------------------------------------------------------

@mcp.tool()
def find_related_entities(
    domain: str,
    entity: str,
    max_hops: int = 2,
) -> dict[str, Any]:
    """GRAPH tier: multi-hop traversal starting from `entity`.

    Observability: who depends on <service>? (blast radius)
    Telco: what is downstream of <element>? (topology impact)
    Cybersecurity: what assets can <user> reach and who else can reach them? (lateral movement)

    Args:
        domain: observability | telco | cybersecurity
        entity: service_id / element_id / user_id to start from
        max_hops: 1 or 2 (default 2)
    """
    domain = _assert_domain(domain)
    if not entity:
        raise ValueError("entity is required")

    client = get_ch_client()
    sql_for_envelope = GRAPH_TRAVERSE_SQL[domain]
    params = {"entity": entity, "max_hops": int(max_hops)}
    (rows, stats), elapsed = timed(query_with_stats, client, sql_for_envelope, params)
    index_hint = "MergeTree PK JOINs + UNION ALL over 1-hop and 2-hop branches"

    direct = [r for r in rows if r.get("hops") == 1]
    indirect = [r for r in rows if r.get("hops") == 2]
    insights: dict[str, Any] = {
        "start_entity": entity,
        "direct_neighbours": len(direct),
        "indirect_neighbours": len(indirect),
    }
    if domain == "observability":
        insights["critical_services_affected"] = len(
            [r for r in rows if r.get("criticality") in ("critical", "high")]
        )
    elif domain == "cybersecurity":
        insights["critical_assets_reachable"] = len(
            [r for r in rows if r.get("criticality") == "critical"]
        )

    precision = {
        "filters_applied": [
            f"start entity: {entity}",
            f"max_hops={max_hops}",
            "Cypher MATCH with variable-length path" if domain == "observability"
            else "JOIN on service_id / element_id / asset_id (indexed PKs)",
        ],
        "index_hint": index_hint,
        "embedding_dim": None,
    }
    return envelope(
        tier="GRAPH",
        domain=domain,
        operation="find_related_entities",
        sql=sql_for_envelope,
        latency_ms=elapsed,
        rows=rows,
        insights=insights,
        precision=precision,
        scan_stats=stats,
    )


# Import the conversation-memory tools so their @mcp.tool() decorators
# register them against the same FastMCP instance. Both modules pull
# `mcp` from `mcp_server.app`, so there is no circular import.
from mcp_server import conversation  # noqa: E402,F401


if __name__ == "__main__":
    check_embedding_dim_consistency()
    mcp.run(transport="streamable-http")
