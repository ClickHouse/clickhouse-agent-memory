# Demo scenarios — measured

- **Ran at:** 2026-04-29T06:18:09Z
- **ClickHouse:** `26.3.9.8` at `http://localhost:18123`
- **Iterations per scenario:** 25 (warmup 3)
- **Canonical anchor:** `svc-orders` · user `u-maruthi` · incident `d03c4742-5489-4015-8271-7d55d36d2ffb`
- **SQL source of truth:** `cookbooks/mcp_server/queries.py` — the **live** MCP templates.

## Demo 1: HOT alone — what just happened?

**User:** "What is happening on svc-orders right now?"

**SQL origin:** `cookbooks/mcp_server/queries.py :: HOT_SCAN_SQL['observability']`

| Tool | Tier | Rows (p50) | Bytes (p50) | Dur p50 | Dur p95 | Result rows |
|---|---|---:|---:|---:|---:|---:|
| `search_events` | HOT | 200 | 27.5 KB | 1.0 ms | 1.0 ms | 2 |

**Feature exercised:** Memory engine · WHERE + ORDER BY ts DESC · time window + severity filter  
**What this proves:** Single-digit ms signals from a SQL surface. Same engine as warm + graph.  
**Honest caveat:** Redis is faster at this in isolation (sub-ms). CH wins only when the hot tier needs to compose with the others.

## Demo 2: WARM alone — have we seen this before?

**User:** "Find past incidents that look like a database connection timeout on svc-orders."

**SQL origin:** `cookbooks/mcp_server/queries.py :: WARM_VECTOR_SQL['observability']`

| Tool | Tier | Rows (p50) | Bytes (p50) | Dur p50 | Dur p95 | Result rows |
|---|---|---:|---:|---:|---:|---:|
| `semantic_search` | WARM | 14 | 25.8 KB | 4.0 ms | 6.0 ms | 5 |

**Feature exercised:** MergeTree + HNSW vector_similarity('hnsw','cosineDistance',768) inside the table; WHERE ts >= now() - 180 DAY prunes whole monthly partitions before HNSW runs  
**What this proves:** Filter-first retrieval. HNSW rank on the surviving set.  
**Honest caveat:** pgvector / Qdrant do this too. CH wins when filter volumes + data are heavy.

## Demo 3: GRAPH alone — what breaks if this fails?

**User:** "What services depend on svc-orders?"

**SQL origin:** `cookbooks/mcp_server/queries.py :: GRAPH_TRAVERSE_SQL['observability']`

| Tool | Tier | Rows (p50) | Bytes (p50) | Dur p50 | Dur p95 | Result rows |
|---|---|---:|---:|---:|---:|---:|
| `find_related_entities` | GRAPH | 32 | 922 B | 3.0 ms | 6.0 ms | 2 |

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
| 3/4 | `get_record` | hydrate top match (runbook + resolution) | 2 | 561 B | 1.0 ms |
| 4/4 | `find_related_entities` | 2-hop blast radius downstream | 32 | 922 B | 3.0 ms |

**Session totals:** **247 rows** · **54.5 KB** · **8.0 ms** sum of p50 latencies across 4 tool calls.

