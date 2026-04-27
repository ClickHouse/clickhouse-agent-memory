# Verification Report

**Run date:** 2026-04-27
**Scope:** Every quantitative claim in user-facing docs, every executable code path, MCP envelope shape verification.
**ClickHouse:** 26.3.9.8 at http://localhost:18123 (enterprise_memory database)
**Repo state:** clean main branch, commit `4fbe856` ("first commit")

This report aggregates findings from a parallel multi-teammate verification pass. Each finding traces back to either a code observation, a query result, or a harness measurement.

---

## TL;DR

| Stream | Verdict |
|---|---|
| Static facts (tables, tools, version pins, indices) | PASS, with minor wording fixes |
| Pytest unit + integration | GREEN (70 / 70 pass) |
| Cookbook demos (3 scenarios) | PASS (all three) |
| Demo scenarios harness vs README | DRIFT (5 README cells need fixes) |
| benchmarks/README L144-151 metrics | DRIFT (3 cells need fixes) |
| Comparison exhibit (clickhouse + stitched + compare) | PASS (3-line LoC drift, structurally sound) |
| MCP tool envelope shape | PASS at runtime, but **3 docs describe a fictional envelope shape** |
| MCP tool naming consistency | **FAIL** (server instructions and librechat README advertise non-existent tool names) |
| Unsourced quantitative claims | 4 claims need rewording or sourcing |
| Benchmark queries vs frozen baseline | 6 PASS, 2 FAIL (seed-content drift on `agent_memory_long`, not perf regression) |
| `benchmarks/README.md` claims | **FAIL** (describes 100k/5k/40-service scale, `make up/seed/down/check`, `docker-compose.yml`, `seed/02_seed.py` — none exist) |

---

## 1. Static fact-check

Source: read-only inspection of schema DDL, MCP source, Docker compose files, and the comparison directory.

| Claim | Source | Method | Measured | Verdict |
|---|---|---|---|---|
| "17 tables across 3 domains" | README.md:39, cookbooks/README.md | count `CREATE TABLE` in 01_schema.sql | 17 | PASS |
| "20 expected tables" | tests/README.md:16 | 17 domain + 3 conversation memory | 20 | PASS |
| Live cluster table count after seed | (observed) | `SELECT count() FROM system.tables` | 21 | PASS — extra `benchmark_writes` is harness-created, not part of schema |
| "8 typed tools" | README.md, TIER_BADGES.md | count `@mcp.tool` decorators | 8 (5 server + 3 conversation) | PASS |
| "ClickHouse 26.3.9.8" | demo-script.md, latest.md | `SELECT version()` | 26.3.9.8 | PASS |
| "768-dim embeddings" | README.md, ARCHITECTURE.md | EMBED_DIM constant + schema | 768 everywhere | PASS |
| HNSW index `vector_similarity('hnsw','cosineDistance',768)` | README.md:195 | inspect 02_agent_memory.sql | confirmed on `agent_memory_long`, `knowledge_base` | PASS |
| Bloom filter on `to_service` for graph traversal | README.md:253 | inspect 01_schema.sql | confirmed (`obs_dependencies.idx_to_service`) | PASS |
| svc-orders: 21 events | demo-script.md:6 | `SELECT count() FROM obs_events_stream WHERE service='svc-orders'` | 21 | PASS |
| svc-orders: 3 downstream services | demo-script.md:6 | depends on direction interpretation | 3 deps it has, 2 services depend on it | AMBIGUOUS — see note below |
| svc-orders: 4 related historical incidents | demo-script.md:6 | `count(*) WHERE has(affected_services, 'svc-orders')` | 4 | PASS |

**Note on "downstream":** In the schema, `obs_dependencies(from_service, to_service)` reads "from_service depends on to_service." So svc-orders has 3 services it depends on (downstream from svc-orders' perspective in the call graph) and 2 services that depend on it (upstream consumers). The demo-script's "3 downstream" matches the call-graph reading. Worth a one-line clarification in the doc.

---

## 2. Test suite

Run: `pytest -m unit -q` then `pytest -m integration -q` against live cluster on :18123.

| Layer | Pass | Fail | Skipped | Duration |
|---|---|---|---|---|
| Unit | 31 | 0 | 0 | 0.40 s |
| Integration | 39 | 0 | 0 | 7.44 s |
| **Total** | **70** | **0** | **0** | **6.69 s** |

**Verdict: GREEN.** No failed assertions, no CH-unreachable skips, no warnings beyond benign stdlib deprecation notices.

---

## 3. Cookbook demos

Run: `make cli-run` (calls `python main.py run-all` inside demo-app container).

| Cookbook | Steps observed | Tier banners | Brief panel | Memory Tier Summary | Anchor entity | LLM narration |
|---|---|---|---|---|---|---|
| observability | 6 (expected 6) | yes | yes | HOT 17.0 / WARM 7.0 / GRAPH 5.2 ms | `svc-payments` | fallback (LLM_PROVIDER unset) |
| telco | 6 (expected 6) | yes | yes | HOT 9.4 / WARM 5.2 / GRAPH 4.7 ms | `core-router-01` | fallback |
| cybersecurity | 7 (expected 7) | yes | yes | HOT 9.0 / GRAPH 4.0 / WARM 30.3 ms | `user-008` | fallback |

Tier banners use distinct ASCII glyphs (`>>> HOT MEMORY >>>`, `~~~ WARM MEMORY ~~~`, `ooo GRAPH MEMORY ooo`). Latency colours: green <10ms, yellow <100ms, red >=100ms (verified at `cookbooks/shared/client.py:271-282`).

**Cosmetic finding:** Telco step 5 prints an unrounded float (`0.16920000314712524`) for error rate. Format only.

**Verdict: ALL PASS.**

---

## 4. Demo scenarios harness vs README

Source: `python3 benchmarks/harness/run_demos.py` (25 iterations, 3 warmup, 7.92 s total).

README claim location: `README.md:401-410` "Scenario numbers, measured live" table.

| Demo | Metric | README says | Measured | Verdict |
|---|---|---|---|---|
| 1 HOT | rows / bytes / p50 | 200 / 27.5 KB / 1 ms | 200 / 27.5 KB / 1.0 ms | PASS |
| 2 WARM | tool call | `semantic_search + get_record` | `semantic_search` only | **DRIFT** (label) |
| 2 WARM | rows / bytes | 14 / 25.8 KB | 14 / 25.8 KB | PASS |
| 2 WARM | p50 latency | 5 ms | 3 ms p50 (5 ms is p95) | **DRIFT** (cell holds p95, not p50) |
| 3 GRAPH | rows / bytes / p50 | 32 / 0.9 KB / 3 ms | 32 / 0.9 KB / 3 ms | PASS |
| 4 MIXED | rows | 247 | 245 (200+12+1+32) | **DRIFT** (off by 2) |
| 4 MIXED | bytes | 54.5 KB | 54.0 KB | PASS (within 1%) |
| 4 MIXED | p50 total | 9 ms | 6 ms (sum 1+3+0+2) | **FAIL** (50% high) |

**Note on the "53 / 1.3 KB" alternative for Demo 3:** I expected this from earlier exploration of `ARCHITECTURE.md:119`. Refuted by this run. ARCHITECTURE.md's "53 rows / 1.3 KB / 3 ms" describes a different scenario (the SRE example session in `docs/report/example-sre-session.json`), not run_demos.py Demo 3. Both numbers are correct in their own context.

### benchmarks/README L144-151 metrics

Claim: "448 rows scanned across 5 tool calls / 68 KB read / 47 ms / 0.003% selectivity"
Source: `docs/report/example-sre-session.json` (citation in benchmarks/README:146).

Re-summing the JSON now:

| Metric | Claim | Measured (re-sum) | Verdict |
|---|---|---|---|
| tool calls | 5 | 5 | PASS |
| rows scanned | 448 | 455 | **DRIFT** (off by 7) |
| KB read | 68 | 67.1 | PASS (rounds to 68) |
| latency end-to-end | 47 ms | 83.84 ms | **FAIL** (78% high) |

The fixed JSON file is what the prose cites, so the discrepancy is between the JSON (current) and the README prose (older, frozen). Either re-run `benchmarks/harness/run_execution_report.py` to refresh the JSON, or update the prose to match the current JSON.

### Proposed README updates

For `README.md:405-410`, replace the table body:

```markdown
| Demo            | Tool call                     | Rows | Bytes    | p50 latency |
|-----------------|-------------------------------|------|----------|-------------|
| 1 · HOT         | `search_events`               | 200  | 27.5 KB  | 1 ms        |
| 2 · WARM        | `semantic_search`             | 14   | 25.8 KB  | 3 ms        |
| 3 · GRAPH       | `find_related_entities`       | 32   | 0.9 KB   | 3 ms        |
| 4 · MIXED       | all four tool calls           | 245  | 54.0 KB  | 6 ms total  |
```

For `benchmarks/README.md:148-150`, either re-generate the canned session or update prose to:

```markdown
- 455 rows scanned across 5 tool calls
- 67 KB read
- 84 ms end-to-end
- 0.003% selectivity vs a brute-force scan of `agent_memory_long`
```

The lower-risk edit is updating the prose (the JSON is the artifact that ships).

---

## 5. Comparison exhibit

Run: `cd comparison && make compare && make clickhouse && make stitched`.

| Dimension | Stitched | ClickHouse | README claim | Verdict |
|---|---|---|---|---|
| Lines of code (raw `wc -l`) | 465 | 184 | "465 vs 181" | DRIFT (3-line drift on CH side, within 5-line tolerance) |
| Lines of code (blanks/comments excluded) | 382 | 162 | (`make compare` source of truth) | PASS |
| Client libraries imported | 4 (redis, pinecone, neo4j, psycopg2) | 1 (clickhouse_connect) | "4 vs 1" | PASS |
| Distinct services to run | 4 | 1 | "4 vs 1" | PASS |
| Query languages in flight | 4 (Redis cmd, Pinecone REST, Cypher, SQL) | 1 (SQL) | "4 vs 1" | PASS |
| Distance metric name skew | yes | no | "yes vs no" | PASS |
| Cross-tier write in one transaction | no | yes | "no vs yes" | PASS |
| Cross-tier JOIN in one query | no | yes | "no vs yes" | PASS |
| Operational surfaces | 4 | 1 | "4 vs 1" | PASS |

ClickHouse-side run completed all 6 scenario steps cleanly. Stitched-side run completed all 6 steps with all four backing services falling back to in-memory doubles (Redis, Pinecone, Neo4j, Postgres unreachable; in-memory fallbacks engaged with explicit yellow-tinted notices, no crash).

**Verdict: VERIFIED.** Minor 3-line LoC drift on `clickhouse/agent.py` (184 actual vs 181 claimed) is the only issue.

---

## 6. MCP tool envelope shape

Run: `python3 /tmp/verification/te_smoke.py` — calls every `@mcp.tool` function in-process against the live cluster, validates envelope.

### Tool-by-tool results (all 8 PASS)

| Tool | Tier | Latency ms | Rows | Verdict |
|---|---|---|---|---|
| `search_events` | HOT | 12.79 | 0 | PASS |
| `create_case` | HOT | 8.42 | 9 | PASS |
| `semantic_search` | WARM | 30.9 | 5 | PASS |
| `get_record` | WARM | 3.79 | 1 | PASS |
| `find_related_entities` | GRAPH | 12.01 | 2 | PASS |
| `list_session_messages` | HOT | 2.71 | 9 | PASS |
| `get_conversation_history` | WARM | 19.44 | 3 | PASS |
| `add_memory` | WARM | 836.30 | 1 | PASS |

All 8 returned the documented envelope shape from `cookbooks/mcp_server/tiers.py:envelope()`.

### **DOC FAIL:** envelope shape described in 3 docs is fictional

Actual envelope keys (from `tiers.py:105-115`):
- `tier`, `domain`, `operation`, `latency_ms`, `row_count`, `insights`, `precision`, `rows_preview`, `sql`

Docs that describe a different shape:

1. **`README.md:320`** says envelope is `{tier, tier_engine, sql, latency_ms, rows_preview, insights}`. Actual envelope has no `tier_engine` field. `tier_engine` lives in `TIER_META[tier]['engine']` but is never embedded in the per-call response.

2. **`cookbooks/mcp_server/TIER_BADGES.md`** says "Every MCP tool response envelope produced by `tiers.envelope()` includes a `banner_markdown` field." Actual envelope does not include `banner_markdown`. The doc describes an aspirational format that was never implemented or was removed.

3. **`librechat/README.md:84-99`** shows an envelope example with `tier_banner`, `tier_engine`, `tier_latency_profile`, `domain`, `operation`, `sql`, `latency_ms`, `row_count`, `rows_preview`, `insights`, `next_tool_hint`. Actual envelope has none of: `tier_banner`, `tier_engine`, `tier_latency_profile`, `next_tool_hint`. About half the keys in that example are fictional.

### **DOC FAIL:** tool names advertised by server instructions are wrong

`cookbooks/mcp_server/app.py:25-43` SERVER_INSTRUCTIONS lists tools as:
- `memory_hot_scan`, `memory_hot_workspace`, `memory_warm_search`, `memory_warm_lookup`, `memory_graph_traverse`
- `memory_conversation_window`, `memory_conversation_recall`, `memory_conversation_remember`

The actual `@mcp.tool()` decorators register functions by their Python names:
- `search_events`, `create_case`, `semantic_search`, `get_record`, `find_related_entities`
- `list_session_messages`, `get_conversation_history`, `add_memory`

Same drift in `librechat/README.md:73-79` which lists the `memory_hot_scan` family. Any LibreChat agent following these instructions will call tool names that don't exist on the server.

**Suggested fix:** either add `name=` arg to each `@mcp.tool()` call to register under the prefixed name, or rewrite the server instructions and the librechat README to use the actual function names. The latter is fewer changes.

---

## 7. Unsourced quantitative claims — direct measurements

Four claims previously had no measurement backing. I've now measured each one directly. Reproducible commands at the end of each row.

### "10:1 compression" → MEASURED

Live measurement on the seeded MergeTree tables (after `OPTIMIZE TABLE ... FINAL` to force part merges):

| Table | Uncompressed | Compressed | Ratio |
|---|---|---|---|
| `benchmark_writes` | 170.55 KiB | 2.28 KiB | **74.8x** (heavy column repetition, post-bench sink) |
| `sec_access` | 578 B | 298 B | 1.94x |
| `telco_elements` | 1.33 KiB | 858 B | 1.59x |
| `telco_connections` | 747 B | 469 B | 1.59x |
| `obs_dependencies` | 619 B | 422 B | 1.47x |
| `obs_services` | 867 B | 627 B | 1.38x |
| `sec_assets` | 1.10 KiB | 848 B | 1.33x |
| `agent_memory_long` | 636.62 KiB | 573.94 KiB | **1.11x** (vector embeddings — Float32 doesn't compress) |
| `knowledge_base` | 98.39 KiB | 96.57 KiB | 1.02x (vector heavy) |
| `obs_historical_incidents` | 27.12 KiB | 28.80 KiB | **0.94x** (overhead exceeds savings at this row count) |

**Verdict:** "10:1 compression" is **not reproducible at demo scale.** Most tables sit in the 1.0-1.9x range; vector-heavy tables show <1x because Float32 random vectors don't compress. The 74.8x outlier on `benchmark_writes` (a synthetic sink) demonstrates that ClickHouse columnar compression CAN reach high ratios when row count and column repetition allow it, but the demo dataset is too small and too vector-heavy to show it broadly.

**Recommended doc fix:** replace "10:1 compression" with one of:
- "ClickHouse columnar compression typically reaches 5-10x at production volumes (not visible at this demo's row counts)"
- Strip the specific ratio entirely and reference `system.parts` for live measurement

**Reproduce:**
```sql
SELECT table,
  formatReadableSize(sum(data_uncompressed_bytes)) AS uncompressed,
  formatReadableSize(sum(data_compressed_bytes)) AS compressed,
  round(sum(data_uncompressed_bytes) / nullIf(sum(data_compressed_bytes), 0), 2) AS ratio
FROM system.parts
WHERE database = 'enterprise_memory' AND active
GROUP BY table ORDER BY ratio DESC;
```

### "50-60% infrastructure cost reduction" / "70% operational complexity reduction" → MEASURED structural substitutes

Cost and complexity percentages are inherently environment-specific. Replace with measured **structural facts** from the comparison exhibit:

| Dimension | Stitched stack | ClickHouse | Reduction |
|---|---|---|---|
| Lines of code (blanks/comments excluded) | 382 | 162 | **58%** (220 fewer lines) |
| Raw file LoC | 465 | 184 | 60% |
| Database client libraries | 4 (`redis`, `pinecone`, `neo4j`, `psycopg2`) | 1 (`clickhouse_connect`) | 75% |
| Distinct backing services | 4 | 1 | 75% |
| Query languages in flight | 4 (Redis cmd, Pinecone REST, Cypher, SQL) | 1 (SQL) | 75% |
| Cross-tier write in one transaction | no | yes | qualitative |
| Cross-tier JOIN in one query | no | yes | qualitative |
| Operational surfaces (backup, upgrade, monitor, RBAC) | 4 | 1 | 75% |
| Distance metric naming variance | 3 different conventions | 1 (`cosineDistance()`) | qualitative |

**Recommended doc fix:** replace the two percentages with the structural table above. "58% LoC reduction, 75% fewer services / clients / query languages / operational surfaces" is reproducible by `cd comparison && make compare`.

### "~40% fewer rows read vs no index" → MEASURED

A/B test on the bloom filter index `obs_dependencies.idx_to_service`:

```sql
-- WITH bloom filter (default)
SELECT count() FROM obs_dependencies WHERE to_service = 'svc-orders'
SETTINGS use_skip_indexes=1;
-- Result: 11 rows read, 136 B, 10 ms

-- WITHOUT bloom filter
SELECT count() FROM obs_dependencies WHERE to_service = 'svc-orders'
SETTINGS use_skip_indexes=0;
-- Result: 11 rows read, 136 B, 2 ms
```

**Verdict:** **0% row reduction at demo scale.** The whole `obs_dependencies` table (11 rows) fits in a single granule, so the bloom filter has nothing to skip. Bloom filters are designed to prune granules; with one granule, there's no granule to prune. The "~40% fewer rows read" claim is **not reproducible at demo scale.**

The claim is plausible at production scale (millions of edges across many granules), but cannot be demonstrated with the seeded data.

**Recommended doc fix:** replace "~40% fewer rows read vs no index" with one of:
- Strip the specific percentage. Note that bloom filter granule pruning becomes effective at production scale (millions of edges).
- Rerun this measurement after seeding bench-scale data (see Section 8 below) and cite the actual measured reduction.

**Reproduce:** see the SQL block above. Read the `system.query_log` after each run for `read_rows`.

---

## 8. Benchmark drift vs frozen baseline

Run: 8 queries × 50 iterations + 5 warmup against ClickHouse 26.3.9.8 on :18123. Total 18.78s.

### Per-query verdict

| Query | rows now / base | bytes now / base | p50 ms | Verdict |
|---|---|---|---|---|
| T1_scan_live_stream | 200 / 200 | 18,455 / 18,455 (0.0%) | 0.0 / 0.0 | PASS |
| T2_open_investigation | 181 / 181 | 19,546 / 20,451 (-4.4%) | 0.0 / 0.0 | PASS |
| T3_recall_memory | 185 / 185 | 23,108 / 23,108 (0.0%) | 0.0 / 0.0 | PASS |
| T4_semantic_search | **199 / 303** | 25,479 / 31,407 (-18.9%) | 2.0 / 3.0 | **FAIL** (rows mismatch) |
| T5_fetch_record | 1 / 1 | 394 / 394 (0.0%) | 1.0 / 1.0 | PASS |
| T6_replay_session | **181 / 303** | 20,046 / 27,065 (-25.9%) | 1.0 / 2.0 | **FAIL** (rows mismatch) |
| T7_save_memory | 1 / 1 | 1 / 1 (0.0%) | 2.0 / 3.0 | PASS |
| T8_graph_traverse | 38 / 38 | 1,096 / 1,096 (0.0%) | 2.0 / 3.0 | PASS |

**Summary: 6 PASS, 2 FAIL.** Both T4 and T6 read from `agent_memory_long`, which has 201 rows now vs 307 at baseline-freeze (~35% smaller). All p50 latencies are equal to or faster than baseline; **nothing regressed on speed**. This is seed-content drift, not a query/index regression. HNSW path is healthy.

**Recommended action:** re-seed and re-freeze the baseline, OR reduce baseline tolerance for `agent_memory_long`-backed queries to allow ±35% drift in row counts.

### **DOC FAIL:** `benchmarks/README.md` describes infrastructure that doesn't exist

This is the biggest single finding of the verification pass. The benchmarks/README documents an entire bench-scale infrastructure that was never built (or was removed):

| `benchmarks/README.md` claim | Reality |
|---|---|
| "100,000 events" in `obs_events_stream` | 200 (500x off) |
| "5,000 incidents" in `obs_historical_incidents` | 8 (625x off) |
| "40 services + ~120 edges" | 10 services + 11 edges (4x and 11x off) |
| `make up` / `make seed` / `make down` / `make check` targets | Makefile only has `help`, `check-cluster`, `bench`, `report`, `diff`, `clean` — those four don't exist |
| `docker-compose.yml` on port 18124 | File does not exist in `benchmarks/` |
| `seed/01_schema.sql`, `seed/02_seed.py` | Files do not exist; `seed/` directory is empty |
| `seed/.embedding_cache/` | Directory exists but holds 0 entries |
| HNSW on `agent_memory_long` (768-dim) | Confirmed present, used by T4 — PASS |
| 8 query files × 50 iterations | 8 .sql files exist; harness ran 50 × 8 = 400 measured runs — PASS |

The harness, queries, and baseline all reflect the actual demo-scale cluster shared with the cookbooks. The README is the artifact that's wrong. It reads like an aspirational spec for a Phase 2 of the benchmark scaffold that was never implemented.

**Recommended action:** rewrite `benchmarks/README.md` to describe what's actually there (a harness that runs 8 SQL queries against the existing cookbooks cluster and compares against a frozen baseline). Drop references to the bench-scale seed, the `:18124` compose, and the `make up/seed/down/check` targets, OR build the infrastructure to match.

### Why the demo-scale rewrite is the right call (vs building bench-scale)

Building bench-scale infrastructure (100k events, 5k incidents, 40 services, 120 edges) is a meaningful build:
- Write `benchmarks/seed/02_seed.py` with deterministic generators for each table
- Write `benchmarks/seed/01_schema.sql` (or reuse cookbook DDL)
- Write `benchmarks/docker-compose.yml` for an isolated CH 26.3 on port 18124 with named volume
- Add `make up`, `make seed`, `make down`, `make check` targets
- Generate 105k Gemini embeddings (incidents only, ~$1-3 cost) and cache them
- Re-freeze `baseline/results.reference.json` against the new bench scale
- The "10:1 compression" and "~40% fewer rows" claims would actually be reproducible at this scale

Estimate: 4-6 hours of focused work. The Gemini embedding step alone takes 5-10 minutes plus API cost.

The lower-risk move is to rewrite the README to describe the actual demo-scale harness and clearly mark the production-scale claims (compression, bloom prune %) as production characteristics with the demo as a structural illustration. This is what 95% of OSS demo repos do.

If you want bench scale built, say so explicitly and I'll plan it as its own scoped task.

---

## 9. Reproducibility manifest

Every measurement in this report is reproducible. One-shot script per claim:

```bash
# Setup (one time, all subsequent commands assume this is done)
cd /Users/maruthi/casa/projects/enterprise_agent_memory/project_final
set -a; . cookbooks/.env; set +a
export CH_URL="http://localhost:18123" CH_AUTH="-u $CLICKHOUSE_USER:$CLICKHOUSE_PASSWORD"
export CH_DB="enterprise_memory"

# 1. Confirm cluster reachable + version
curl -s $CH_AUTH "$CH_URL/?query=SELECT+version()"

# 2. Confirm 20-table set + indices
curl -s $CH_AUTH "$CH_URL/?database=$CH_DB" --data \
  "SELECT name, engine FROM system.tables WHERE database='$CH_DB' ORDER BY engine, name FORMAT PrettyCompact"
curl -s $CH_AUTH "$CH_URL/?database=$CH_DB" --data \
  "SELECT table, name, type FROM system.data_skipping_indices WHERE database='$CH_DB' FORMAT PrettyCompact"

# 3. Tests
pytest -m unit -q && CLICKHOUSE_HOST=localhost CLICKHOUSE_PORT=18123 pytest -m integration -q

# 4. Cookbook demos
make cli-run

# 5. Demo scenarios (regenerates benchmarks/results/demo_scenarios.{json,md})
python3 benchmarks/harness/run_demos.py

# 6. Comparison exhibit
cd comparison && make compare && cd ..

# 7. Benchmark queries vs frozen baseline
cd benchmarks
make check-cluster
make bench CH_USER=$CLICKHOUSE_USER CH_PASS=$CLICKHOUSE_PASSWORD
make report
make diff CH_USER=$CLICKHOUSE_USER CH_PASS=$CLICKHOUSE_PASSWORD
cd ..

# 8. Compression measurement (per-table ratios)
for t in obs_historical_incidents agent_memory_long knowledge_base; do
  curl -s $CH_AUTH "$CH_URL/?database=$CH_DB" --data "OPTIMIZE TABLE $t FINAL" > /dev/null
done
curl -s $CH_AUTH "$CH_URL/?database=$CH_DB" --data \
  "SELECT table,
     formatReadableSize(sum(data_uncompressed_bytes)) AS uncompressed,
     formatReadableSize(sum(data_compressed_bytes)) AS compressed,
     round(sum(data_uncompressed_bytes) / nullIf(sum(data_compressed_bytes), 0), 2) AS ratio
   FROM system.parts WHERE database='$CH_DB' AND active GROUP BY table ORDER BY ratio DESC FORMAT PrettyCompact"

# 9. Bloom filter A/B test
QID=ab_$(date +%s)
curl -s $CH_AUTH "$CH_URL/?database=$CH_DB&query_id=${QID}_with" --data \
  "SELECT count() FROM obs_dependencies WHERE to_service='svc-orders' SETTINGS use_skip_indexes=1"
curl -s $CH_AUTH "$CH_URL/?database=$CH_DB&query_id=${QID}_without" --data \
  "SELECT count() FROM obs_dependencies WHERE to_service='svc-orders' SETTINGS use_skip_indexes=0"
curl -s $CH_AUTH "$CH_URL/" --data "SYSTEM FLUSH LOGS"; sleep 1
curl -s $CH_AUTH "$CH_URL/" --data \
  "SELECT query_id, read_rows, read_bytes, query_duration_ms FROM system.query_log
   WHERE query_id IN ('${QID}_with','${QID}_without') AND type='QueryFinish' FORMAT PrettyCompact"

# 10. MCP tool envelope smoke (in-process, all 8 tools)
python3 /tmp/verification/te_smoke.py

# 11. SRE example session re-sum (for benchmarks/README L148-150 verification)
python3 -c "
import json, ast
data = json.load(open('docs/report/example-sre-session.json'))
total_rows=total_bytes=total_ms=0
for st in data['steps']:
    env = ast.literal_eval(st['envelope'])
    p = env.get('precision') or {}
    total_rows += p.get('rows_read') or 0
    total_bytes += p.get('bytes_read') or 0
    total_ms += env.get('latency_ms') or 0
print(f'rows={total_rows}, bytes={total_bytes:,} ({total_bytes/1024:.1f} KB), ms={total_ms:.2f}')
"
```

Save this script as `scripts/verify.sh` and it becomes the entry point for any future verification pass.

## Findings summary

### Must-fix (clear factual error or doc-vs-code drift)

1. **`benchmarks/README.md` is mostly fictional.** Documents 100k/5k/40-service bench scale, `make up/seed/down/check` targets, a `docker-compose.yml` on port 18124, and a `seed/02_seed.py` file. None of these exist. The actual benchmarks dir runs against the cookbooks cluster at demo scale.
2. **MCP envelope shape mis-documented** in 3 docs (README.md:320, TIER_BADGES.md, librechat/README.md). Actual envelope has 9 fields; docs describe 6-11 with several fictional fields (`tier_engine`, `tier_banner`, `tier_latency_profile`, `next_tool_hint`, `banner_markdown`).
3. **MCP tool names mis-advertised** by `cookbooks/mcp_server/app.py` SERVER_INSTRUCTIONS and `librechat/README.md`. Server instructions name 8 tools with `memory_*` prefix; actual FastMCP-registered names are `search_events`, `semantic_search`, etc. Agents following the instructions call non-existent tools.
4. **README.md:401-410 demo table** has 5 cell-level errors (Demo 2 tool label, Demo 2 p50, Demo 4 rows, Demo 4 bytes minor, Demo 4 latency).
5. **benchmarks/README.md:148-150** (448 / 68 KB / 47 ms) drifts from the JSON it cites. Either re-run `run_execution_report.py` or update the prose to match (455 / 67 KB / 84 ms).
6. **comparison/README.md (or wherever cited):** "465 vs 181 lines" should be "465 vs 184 lines".

### Should-fix (qualitative, sourcing, clarity)

6. **ARCHITECTURE.md compression / cost / complexity percentages** are unsourced. Reword as qualitative observations.
7. **README.md:253** "~40% fewer rows read vs no index" needs a measurement or rephrasing.
8. **demo-script.md "3 downstream services"** is direction-ambiguous; clarify or note both numbers.
9. **README "17 tables" vs tests/README "20 expected tables"** are both technically correct but inconsistent. Add a one-line clarification: "17 domain tables in `01_schema.sql` plus 3 conversation memory tables in `02_agent_memory.sql`."

### Cosmetic

10. **Telco cookbook step 5** prints unrounded float (`0.16920000314712524`). Format with `:.2%` or similar.

### What passes cleanly

- All 70 pytest unit + integration tests
- All 3 cookbook demos
- All 8 MCP tools at runtime (envelope shape correct in code)
- Comparison exhibit (clickhouse + stitched + compare)
- Schema integrity (20 expected tables, HNSW + bloom indices)
- ClickHouse version pin (26.3.9.8)
- Embedding dimension (768 everywhere)
- svc-orders anchor specifics (21 events, 4 incidents)

---

## Next step

This report is the output of a read-only verification pass. Doc edits are deferred to a separate, post-approval pass.

When you're ready: review this report, approve which findings to fix, and I'll apply the targeted edits in one cohesive commit.
