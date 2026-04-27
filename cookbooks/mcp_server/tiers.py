"""
mcp_server/tiers.py
-------------------
Tier metadata + the thin response envelope every MCP tool returns.

The envelope is intentionally minimal: LibreChat renders tool responses
as raw JSON in a collapsible card, so every extra field costs screen
real estate and makes the conversation harder to read. Only fields the
agent actually needs to write a good narrative go here.

Shape:
  {
    "tier":         "HOT" | "WARM" | "GRAPH" | "RESULT"
    "domain":       "observability" | "telco" | "cybersecurity" | "conversation"
    "operation":    short snake_case name of what the tool did
    "latency_ms":   float, ClickHouse query time only (no network overhead)
    "row_count":    int, total matching rows the query found
    "insights":     dict, the structured takeaway the agent should narrate
    "rows_preview": list[dict], up to 3 rows, for visual sanity check
    "sql":          str, dedented, with real newlines, ready for ```sql fence
  }
"""

from __future__ import annotations

import textwrap
import time
from typing import Any


TIER_META = {
    "HOT": {
        "label": "HOT MEMORY",
        "engine": "ClickHouse Memory Engine",
        "latency_profile": "sub-5ms | volatile | in-memory",
    },
    "WARM": {
        "label": "WARM MEMORY",
        "engine": "ClickHouse MergeTree + Vector Search",
        "latency_profile": "50-500ms | persistent | cosineDistance",
    },
    "GRAPH": {
        "label": "GRAPH MEMORY",
        "engine": "ClickHouse SQL JOINs",
        "latency_profile": "10-100ms | relationships | multi-hop",
    },
    "RESULT": {
        "label": "SYNTHESISED CONTEXT",
        "engine": "Agent Context Assembly",
        "latency_profile": "aggregated across tiers",
    },
}


DOMAINS = ("observability", "telco", "cybersecurity")


def envelope(
    tier: str,
    domain: str,
    operation: str,
    sql: str,
    latency_ms: float,
    rows: list[dict],
    insights: dict[str, Any] | None = None,
    precision: dict[str, Any] | None = None,
    scan_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Standard thin response shape returned by every memory MCP tool.

    precision carries the agent-provided narrative of WHY this query is
    precise: filters_applied (list[str]), index_hint (str),
    embedding_dim (int|None). scan_stats carries the physical numbers
    ClickHouse reported: read_rows, read_bytes. The envelope merges
    both into a single `precision` block so a downstream report can say
    "we scanned N rows out of M, using index X" without guessing.
    """
    precision = dict(precision or {})
    stats = scan_stats or {}
    read_rows = int(stats.get("read_rows", 0) or 0)
    read_bytes = int(stats.get("read_bytes", 0) or 0)
    rows_returned = len(rows)

    # Selectivity: returned / read. Low returned + high read = full scan.
    if read_rows > 0:
        selectivity_pct = round(100.0 * rows_returned / read_rows, 2)
        selectivity = f"{selectivity_pct}%"
    elif rows_returned > 0:
        # Write or non-Query path: fall back to row_count as the scan proxy.
        selectivity = "n/a (write or non-SELECT)"
    else:
        selectivity = "no rows scanned"

    precision_block = {
        "filters_applied": precision.get("filters_applied", []),
        "index_hint": precision.get("index_hint", "unknown"),
        "embedding_dim": precision.get("embedding_dim"),
        "rows_read": read_rows or None,
        "bytes_read": read_bytes or None,
        "rows_returned": rows_returned,
        "selectivity": selectivity,
        "written_rows": int(stats.get("written_rows", 0) or 0) or None,
    }

    return {
        "tier": tier,
        "domain": domain,
        "operation": operation,
        "latency_ms": round(latency_ms, 2),
        "row_count": rows_returned,
        "insights": insights or {},
        "precision": precision_block,
        "rows_preview": _serialise_rows(rows[:3]),
        "sql": textwrap.dedent(sql).strip(),
    }


def _serialise_rows(rows: list[dict]) -> list[dict]:
    """Make values JSON-safe. Datetimes -> iso, embeddings hidden, etc."""
    out = []
    for row in rows:
        clean: dict[str, Any] = {}
        for k, v in row.items():
            if k in ("embedding", "content_embedding"):
                clean[k] = f"<vector[{len(v)}]>" if isinstance(v, (list, tuple)) else str(v)
            elif hasattr(v, "isoformat"):
                clean[k] = v.isoformat()
            elif isinstance(v, (list, tuple)):
                clean[k] = list(v)
            elif isinstance(v, (int, float, bool, type(None), str)):
                clean[k] = v
            else:
                clean[k] = str(v)
        out.append(clean)
    return out


def timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = (time.perf_counter() - t0) * 1000
    return result, elapsed


if __name__ == "__main__":
    # Quick self-check: print a representative envelope for each tier.
    import json
    samples = [
        ("HOT", "observability", "search_events", "SELECT 1", 0.82),
        ("WARM", "telco", "vector_similarity_search", "SELECT 1", 134.7),
        ("GRAPH", "cybersecurity", "graph_multi_hop_traversal", "SELECT 1", 42.5),
        ("RESULT", "observability", "context_assembly", "SELECT 1", 178.0),
    ]
    for tier, domain, op, ms in [(s[0], s[1], s[2], s[4]) for s in samples]:
        env = envelope(tier, domain, op, sql="SELECT 1", latency_ms=ms, rows=[])
        print(json.dumps(env, indent=2))
