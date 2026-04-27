# Demo scenarios — measured

- **Ran at:** 2026-04-22T07:34:59Z
- **ClickHouse:** `26.3.9.8` at `http://localhost:18123`
- **Iterations per scenario:** 25 (warmup 3)
- **Canonical anchor:** `svc-orders` · user `u-maruthi` · incident `04e3c725-1ff3-4b1c-b633-33dc3dc4532e`
- **SQL source of truth:** `cookbooks/mcp_server/queries.py` — the **live** MCP templates.

## Demo 1: HOT alone — what just happened?

**User:** "What is happening on svc-orders right now?"

**SQL origin:** `cookbooks/mcp_server/queries.py :: HOT_SCAN_SQL['observability']`

| Tool | Tier | Rows (p50) | Bytes (p50) | Dur p50 | Dur p95 | Result rows |
|---|---|---:|---:|---:|---:|---:|
| `search_events` | HOT | 200 | 27.5 KB | 1.0 ms | 2.0 ms | 0 |

**Feature exercised:** Memory engine · WHERE + ORDER BY ts DESC · time window + severity filter  
**What this proves:** Single-digit ms signals from a SQL surface. Same engine as warm + graph.  
**Honest caveat:** Redis is faster at this in isolation (sub-ms). CH wins only when the hot tier needs to compose with the others.

## Demo 2: WARM alone — have we seen this before?

**User:** "Find past incidents that look like a database connection timeout on svc-orders."

**SQL origin:** `cookbooks/mcp_server/queries.py :: WARM_VECTOR_SQL['observability']`

| Tool | Tier | Rows (p50) | Bytes (p50) | Dur p50 | Dur p95 | Result rows |
|---|---|---:|---:|---:|---:|---:|
| `semantic_search` | WARM | 14 | 25.7 KB | 3.0 ms | 5.0 ms | 5 |

**Feature exercised:** MergeTree + HNSW vector_similarity('hnsw','cosineDistance',768) inside the table  
**What this proves:** Filter-first retrieval. HNSW rank on the surviving set.  
**Honest caveat:** pgvector / Qdrant do this too. CH wins when filter volumes + data are heavy.

## Demo 3: GRAPH alone — what breaks if this fails?

**User:** "What services depend on svc-orders?"

**SQL origin:** `cookbooks/mcp_server/queries.py :: GRAPH_TRAVERSE_SQL['observability']`

| Tool | Tier | Rows (p50) | Bytes (p50) | Dur p50 | Dur p95 | Result rows |
|---|---|---:|---:|---:|---:|---:|
| `find_related_entities` | GRAPH | 53 | 1.3 KB | 3.0 ms | 5.0 ms | 2 |

**Feature exercised:** Two-hop upstream dependency walk · SQL JOIN + UNION ALL  
**What this proves:** Graph walks don't need a separate graph DB for 2-hop blast radius.  
**Honest caveat:** Neo4j / Memgraph still win at hard graph algorithms.

## Demo 4: MIXED — walk me through it

**User:** "svc-orders is failing, walk me through it."

**SQL origin:** `HOT_SCAN_SQL['observability']`

| Step | Tool | Intent | Rows (p50) | Bytes (p50) | Dur p50 |
|---|---|---|---:|---:|---:|
| 1/4 | `search_events` | live errors on the failing service | 200 | 27.5 KB | 1.0 ms |
| 2/4 | `semantic_search` | find similar past incident | 13 | 25.5 KB | 3.0 ms |
| 3/4 | `get_record` | hydrate top match (runbook + resolution) | 1 | 283 B | 1.0 ms |
| 4/4 | `find_related_entities` | 2-hop blast radius downstream | 53 | 1.3 KB | 3.0 ms |

**Session totals:** **267 rows** · **54.5 KB** · **8.0 ms** sum of p50 latencies across 4 tool calls.

