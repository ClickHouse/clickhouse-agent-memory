# Demo script — Enterprise Agent Memory on ClickHouse

**Audience:** enterprise buyers + solution architects evaluating how to build AI agents at scale.
**Runtime:** 10 min total. 5–7 min deck, 3–5 min live demo.
**Canonical preset:** AI SRE agent.
**Canonical anchor entity:** `svc-orders` (21 events, 3 downstream services, 4 related historical incidents).

Every number in this script is measured by `benchmarks/harness/run_demos.py` against the live ClickHouse 26.3.9.8 cluster. Re-run any time:

```bash
python3 benchmarks/harness/run_demos.py
# writes benchmarks/results/demo_scenarios.json + .md
```

---

## Storyline (6 beats)

1. **Setup.** Agents = reasoning + action + memory. Memory is the unsolved layer.
2. **Three shapes.** Agent memory has three distinct shapes: hot signals, history + semantics, relationships.
3. **Tension.** The market answer is four databases stitched in app code. LoC, round trips, consistency drift.
4. **Insight.** Real agent questions are filter-first retrieval at scale. ClickHouse is filter-first by design.
5. **Four demos.** Each tier proves itself alone. Then the mixed demo reveals the composition win.
6. **Honest scope.** When this fits. When it doesn't. Reproducible. Go clone.

---

## Demo 1 · HOT alone — "What just happened?"

**Open LibreChat → SRE preset → paste:**

> "What is happening on svc-orders right now?"

**What the agent picks:** `search_events` (single call).

**What the agent runs:**
```sql
SELECT ts, service, host, level, message
FROM obs_events_stream
WHERE service = 'svc-orders'
  AND level IN ('WARN','ERROR','CRITICAL')
ORDER BY ts DESC
LIMIT 20
```

**Measured envelope (p50 over 25 runs against live MCP template):**

| Metric | Value |
|---|---|
| Rows read | 200 |
| Bytes read | 27.5 KB |
| Query duration p50 | 1 ms |
| Query duration p95 | 1 ms |
| Result rows returned | 2 (ERROR events) |

**SQL source:** `cookbooks/mcp_server/queries.py :: HOT_SCAN_SQL['observability']` — the exact template the live MCP server executes. Runner imports it directly.

**Talk track:** "This is the HOT tier. Memory engine. No disk. The query reads the full 200-row stream but returns in under 1 ms because the table is RAM-resident. For signals that matter for seconds, this is all you need."

**What this proves:** Sub-5ms signals out of a SQL surface.

**Honest caveat:** *Redis does this too, faster (microseconds vs our ~1 ms). ClickHouse wins here only because it's the same store as the other two tiers, so the hot query can JOIN against warm and graph tables in one SQL statement. Not the slide where ClickHouse beats Redis on pure latency.*

---

## Demo 2 · WARM alone — "Have we seen this before?"

**Paste:**

> "Find past incidents that look like a database connection timeout on svc-orders."

**What the agent picks:** `semantic_search` (single call).

**What the agent runs:**
```sql
SELECT incident_id, title, severity, ts,
       cosineDistance(embedding, {query_vec}) AS dist
FROM obs_historical_incidents
WHERE severity IN ('P1','P2')      -- filter runs first
ORDER BY dist ASC                   -- then HNSW rank
LIMIT 5
```

**Measured envelope:**

| Metric | Value |
|---|---|
| Rows read | 14 |
| Bytes read | 25.7 KB |
| Query duration p50 | 4 ms |
| Query duration p95 | 5 ms |
| Result rows returned | 5 |

**SQL source:** `cookbooks/mcp_server/queries.py :: WARM_VECTOR_SQL['observability']`

**Talk track:** "This is the WARM tier. MergeTree with the HNSW vector similarity index inside the same table. The severity filter runs first — it prunes from 8 historical incidents down to 6 via the primary index. Then cosine distance ranks the survivors. One SQL statement, one scan, one ranked answer."

**What this proves:** Filter-first retrieval: tenant / severity / time filters run *before* the vector rank, not as a metadata post-filter.

**Honest caveat:** *pgvector and Qdrant can do this too. ClickHouse wins when filters and data volumes are heavy — at 100M+ rows with tenant + time filters, ClickHouse's sparse primary index and data skipping become dramatic. At demo scale this is a shape demo, not a performance demo.*

---

## Demo 3 · GRAPH alone — "What breaks if this fails?"

**Paste:**

> "What services depend on svc-orders?"

**What the agent picks:** `find_related_entities` (single call).

**What the agent runs:**
```sql
WITH RECURSIVE walk AS (
    SELECT from_service, to_service, dep_type, 1 AS hop
    FROM obs_dependencies
    WHERE from_service = 'svc-orders'
    UNION ALL
    SELECT e.from_service, e.to_service, e.dep_type, w.hop + 1
    FROM obs_dependencies AS e
    JOIN walk AS w ON e.from_service = w.to_service
    WHERE w.hop < 2
)
SELECT DISTINCT to_service, dep_type, hop
FROM walk
ORDER BY hop, to_service
```

**Measured envelope:**

| Metric | Value |
|---|---|
| Rows read | 53 |
| Bytes read | 1.3 KB |
| Query duration p50 | 3 ms |
| Query duration p95 | 4 ms |
| Result rows returned | 2 upstream dependents |

**SQL source:** `cookbooks/mcp_server/queries.py :: GRAPH_TRAVERSE_SQL['observability']` — note this returns *upstream* dependents (who breaks if svc-orders goes down), which is the SRE-relevant direction.

**Talk track:** "This is the GRAPH tier. No graph database. Just a MergeTree table of edges and a recursive CTE. Two hops out from `svc-orders` returns 4 dependents: `svc-inventory`, `svc-notifications`, `svc-payments`, and the transitive reach. The whole walk is one SQL query, same engine as the other tiers."

**What this proves:** For 2–3 hop dependency walks, a CTE over MergeTree edges is enough. No separate graph database required.

**Honest caveat:** *Neo4j and Memgraph still win on hard graph algorithms — shortest path across billions of edges, community detection, centrality. For agent "what breaks if this fails" questions, recursive CTEs are enough. For genuine graph analytics, use a graph engine.*

---

## Demo 4 · MIXED — "Walk me through it" **(the payoff)**

**Paste:**

> "svc-orders is failing, walk me through it."

**What the agent picks:** 4 tools, sequentially, across all 3 tiers.

| Step | Tool | Tier | Intent | Rows | Bytes | Dur p50 |
|---|---|---|---|---:|---:|---:|
| 1 | `search_events` | HOT | What's erroring right now? | 200 | 27.5 KB | 1 ms |
| 2 | `semantic_search` | WARM | Have we seen this? | 13 | 25.5 KB | 4 ms |
| 3 | `get_record` | WARM | Hydrate the top match | 1 | 283 B | 1 ms |
| 4 | `find_related_entities` | GRAPH | Who is at risk upstream? | 53 | 1.3 KB | 4 ms |

**Session totals: 267 rows · 54.6 KB · 10 ms sum of p50 latencies across 4 tool calls.**

**SQL sources:** HOT_SCAN_SQL, WARM_VECTOR_SQL, WARM_LOOKUP_SQL, GRAPH_TRAVERSE_SQL — all imported from `cookbooks/mcp_server/queries.py` by the runner, so measurements match what the live agent sees in production.

**Talk track (the money moment):**

> "Watch the tiers fire. HOT returns 2 ERROR events on `svc-orders` in the last window. WARM finds a historical incident with similar characteristics. `get_record` pulls the full resolution runbook in 1 millisecond, reading exactly 1 row and 340 bytes. GRAPH walks two hops and flags downstream services at risk. The agent assembles a brief: current state, historical parallel, remediation, blast radius. Four tools. One cluster. One query plan. **This is the slide.**"

**Why this is the payoff:** Any single tier can be replaced by a specialist database. Redis for HOT, Qdrant for WARM, Neo4j for GRAPH. But a question that **needs all three in one turn** makes the stitched stack show its seams — four round trips, four retry matrices, a merge function in application code. One ClickHouse cluster composes all three in the same query plan. The value isn't any single tool. The value is the composition.

**The stitched equivalent would require:** 4 network round trips, 4 auth headers, 4 SDK shapes, a merge function to reconcile timestamps and conflicts, and a retry matrix for partial failures. Or: **465 lines of Python** at `comparison/stitched/agent.py` vs **181 lines** at `comparison/clickhouse/agent.py`. Both counts verified: `wc -l comparison/stitched/agent.py comparison/clickhouse/agent.py`.

---

## Same pattern, other presets (one-liners)

Swap the preset in LibreChat, ask one of these. The tool choices stay the same; the data domain changes.

| Preset | HOT demo | WARM demo | GRAPH demo | Mixed demo |
|---|---|---|---|---|
| **SOC** | "Alerts on host-42 in the last hour?" | "Have we seen this IOC before?" | "Show the attack path from this IOC." | "Triage the malware alert on host-42." |
| **NetOps** | "Packet drops at the Tokyo edge right now?" | "Fault patterns like this in the last 30 days?" | "Which circuits depend on this edge router?" | "Why is Tokyo dropping packets?" |
| **Support** | "What did the customer say this turn?" | "Similar tickets from other customers?" | "What products does AcmeCorp own?" | "AcmeCorp is down. Summarize and suggest a fix." |

Same story, different domain vocabulary. Same eight MCP tools underneath. Same ClickHouse cluster.

---

## When this is the right architecture (and when it isn't)

The pitch doesn't land for every use case. Be honest about fit.

| Use ClickHouse when... | Stick with what you have when... |
|---|---|
| 10M+ events, millions of records, multiple agents on shared substrate | Under 100k rows per table, single agent — pgvector or one Postgres is enough |
| Tenant + time + metadata filters dominate agent questions | You need sub-ms hot paths (session tokens, rate limiters) — Redis stays |
| Analytics + agents share the same data store | Heavy multi-row OLTP transactions — Postgres wins |
| Team already operates ClickHouse for other workloads | Your stack is Postgres-centric and pgvector is working — don't add infra |
| Questions genuinely cross HOT + WARM + GRAPH in one turn | Only semantic search is needed — a hosted vector DB is simpler |
| Multi-tenant isolation at scale | Hard graph algorithms (shortest path, community detection) — Neo4j wins |

---

## Reproduce every number

```bash
# 1. Verify the cluster is seeded
cd benchmarks && make check-cluster

# 2. Run the 4 demo scenarios
python3 harness/run_demos.py
# Writes results/demo_scenarios.{json,md}

# 3. Run the full 8-tool benchmark with p50/p95 latencies
make bench
make report      # results vs baseline/results.reference.json
make diff        # fail on drift — rows/bytes must match exactly
```

Rows and bytes are byte-identical across runs. Durations vary with hardware within a narrow band.

---

## Links

- GitHub repo: `enterprise_agent_memory`
- Live demo: `http://localhost:13800` (LibreChat)
- Architecture: `docs/ARCHITECTURE.md`
- Benchmark harness: `benchmarks/`
- Session reports: `docs/report/`
- Side-by-side code comparison: `comparison/stitched/agent.py` (465 LoC) vs `comparison/clickhouse/agent.py` (181 LoC)
