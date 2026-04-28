# Stitched stack vs ClickHouse — perf bench

- Iterations per agent: **30** (after 5 warmup runs)
- Stitched wall time: 16.09 s for 30 runs (536.2 ms / run including subprocess startup)
- ClickHouse wall time: 6.19 s for 30 runs (206.3 ms / run including subprocess startup)
- Stitched backends: Redis (real :6380) + Pinecone (in-mem fallback) + Neo4j (real :7688) + Postgres (real :5433)
- ClickHouse backend: single cluster on :18123 (cookbook seed)

Per-iteration is run in a fresh Python subprocess to give each iteration a clean cold start; subprocess startup cost (~0.3 s on macOS) is therefore included in `total_ms`. The per-step rows isolate the actual work and are the fair comparison.

## Per-step latency (ms, in-process timing)

| Step | Stitched p50 | Stitched p95 | ClickHouse p50 | ClickHouse p95 | Stitched / CH (p50) |
|------|-------------:|-------------:|---------------:|---------------:|--------------------:|
| 1 · scan live events | 4.57 | 5.08 | 6.29 | 13.31 | 0.7x |
| 2 · open workspace | 1.11 | 1.89 | 5.17 | 16.74 | 0.2x |
| 3 · vector search history | 3.44 | 4.18 | 8.28 | 17.88 | 0.4x |
| 4 · graph blast radius | 9.12 | 19.70 | 6.26 | 15.06 | 1.5x |
| 5 · fetch runbook | 2.03 | 5.42 | 3.43 | 11.42 | 0.6x |
| 6 · synthesise brief | 7.52 | 8.32 | 7.86 | 8.50 | 1.0x |

## Summary metrics

Two metrics, two stories:

| Metric | Stitched (p50) | ClickHouse (p50) | Stitched / CH |
|---|---:|---:|---:|
| Sum of per-step work | 27.79 ms | 37.29 ms | 0.75x |
| Total per iteration (incl. connection setup + Python orch) | 337.97 ms | 68.97 ms | 4.90x |

## What the numbers say

**Per-step work**: at this scale stitched is slightly faster (27.8 ms vs 37.3 ms summed). Two reasons:

- The Pinecone WARM-tier path is the in-memory double (3 rows, no network). A real Pinecone or Weaviate query adds 5-50 ms on the wire.
- ClickHouse pays HTTP + SQL parse overhead (~5 ms) per call even on tiny tables; Redis XREVRANGE on a few entries is sub-millisecond. At this seed size, the CH HNSW machinery is overkill.

**Per-iteration total**: ClickHouse wins by **4.9x** (338 ms vs 69 ms). Stitched pays for four separate client connection setups (Redis, Pinecone, Neo4j, Postgres) on every cold start; ClickHouse pays for one. In a long-lived agent process the connection setup cost is amortised, but in serverless / per-request agents (the typical deployment) it dominates.

**Where this comparison flips at production scale**: with millions of incidents in the WARM vector store, ClickHouse HNSW returns a top-K in single-digit ms while a real Pinecone call adds round-trip latency. With real Neo4j + a 100k-edge graph, Cypher traversal is fine but still on the wire. At scale the per-step metric also favors ClickHouse because the queries become large enough that HTTP+parse overhead is amortised.

## Reproduce

```bash
cd /path/to/clickhouse-agent-memory
# 1. cookbook stack
make cli-up && make cli-seed
# 2. stitched services
docker compose -f comparison/stitched/docker-compose.yml up -d
python comparison/seed_stitched.py
# 3. perf bench
python comparison/bench_runner.py --iterations 20
```