# Tier metadata

## Where tier identity lives

Each MCP tool envelope carries a `tier` field (`HOT`, `WARM`, `GRAPH`, or
`RESULT`). The shape of the envelope is defined in `tiers.py:envelope()`:

```python
{
    "tier":         "HOT" | "WARM" | "GRAPH" | "RESULT",
    "domain":       "observability" | "telco" | "cybersecurity" | "conversation",
    "operation":    short snake_case name of what the tool did,
    "latency_ms":   float, ClickHouse query time only,
    "row_count":    int, rows returned,
    "insights":     dict, the structured takeaway,
    "precision":    dict with rows_read, bytes_read, selectivity, index_hint,
    "rows_preview": list[dict], up to 3 rows,
    "sql":          str, dedented SQL ready to fence,
}
```

The full per-tier metadata (display label, engine name, latency profile)
lives in `TIER_META` in `tiers.py`:

```python
TIER_META = {
    "HOT":   {"label": "HOT MEMORY",          "engine": "ClickHouse Memory Engine",          "latency_profile": "sub-5ms | volatile | in-memory"},
    "WARM":  {"label": "WARM MEMORY",         "engine": "ClickHouse MergeTree + Vector Search", "latency_profile": "50-500ms | persistent | cosineDistance"},
    "GRAPH": {"label": "GRAPH MEMORY",        "engine": "ClickHouse SQL JOINs",              "latency_profile": "10-100ms | relationships | multi-hop"},
    "RESULT":{"label": "SYNTHESISED CONTEXT", "engine": "Agent Context Assembly",            "latency_profile": "aggregated across tiers"},
}
```

## How LibreChat renders the tier

The LibreChat preset prompt tells the agent to mention the tier (HOT /
WARM / GRAPH) in the natural-language reply so the user sees which memory
layer answered. The raw envelope is shown in the collapsible tool-call
panel; the tier is a structured field the agent can read off `tier`.

If you want a literal one-line banner the agent echoes verbatim, build it
client-side from `tier`, `domain`, `operation`, and `latency_ms`. The
server intentionally keeps the envelope minimal — extra fields cost screen
real estate and the agent can format the banner from primitives.

## Self-check

Run `python cookbooks/mcp_server/tiers.py` to print a sample envelope for
each of the four tiers.
