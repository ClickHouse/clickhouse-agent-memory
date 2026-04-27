# Benchmarks — reproducible agent-memory numbers

Every performance number in the deck, blog, and report is generated from this
directory. If a number appears in marketing and does not trace back to a query
file + a harness run here, it does not belong in marketing.

## What this measures

Eight MCP tools, each backed by a single ClickHouse query. Per tool we capture:

- `read_rows` — rows the query touched (deterministic across runs)
- `read_bytes` — bytes read from storage (deterministic)
- `query_duration_ms` — wall time from `system.query_log` (varies ±)
- `result_rows` — rows returned to the caller

We do NOT measure the LLM picking tools. That varies per model and per prompt.
This harness isolates the storage path so the numbers are reproducible
regardless of which agent is orchestrating on top.

## Quick start

```bash
cd benchmarks
make up            # Start a dedicated CH 26.3 container on port 18124
make seed          # Create schema, load 100k events + 5k incidents + graph edges
make bench         # Run each of 8 tool queries × 50 iterations, write results/
make report        # Pretty-print results/ vs baseline/results.reference.json
make check         # Diff rows/bytes against baseline — fails on drift
make down          # Tear down the container
```

On a fresh Apple Silicon laptop with 16GB RAM and Docker Desktop, expect:

- `make up` — 30s (image pull first time, 5s after)
- `make seed` — 2–4 min (Gemini embeddings are cached in `seed/.embedding_cache/` after the first run, so reruns take 20s)
- `make bench` — 15–25s (50 iterations × 8 queries)
- `make report` — instant

Total: under 5 minutes first time, under 1 minute on reruns.

## Reproducibility guarantees

| Metric | Deterministic? | Why |
|---|---|---|
| `read_rows` | **Yes, exact** | Comes from ClickHouse's query planner. No randomness. |
| `read_bytes` | **Yes, exact** | Same source as rows. Compression is deterministic per data version. |
| `query_duration_ms` | No, but bounded | Varies with CPU, thermals, disk cache. We report p50 and p95 over N runs. |
| Embedding vectors | **Yes, for the same input** | Gemini is deterministic per (model, input). We pin model and cache to disk. |
| Seed data | **Yes** | Fixed random seed (`SEED=42`), generated via `generateRandom()` + fixed prompts. |

The meaningful claim is on the deterministic metrics. When the deck says "448
rows scanned," that number comes from `read_rows` and should match on every
laptop. The 47ms latency will not match exactly — but the *rows scanned* proves
the agent is not brute-forcing.

## What you need

- Docker Desktop with 4GB+ allocated
- Python 3.11+
- A Google Gemini API key set as `GOOGLE_API_KEY` (for embeddings). Free tier is
  plenty for a single seed run (~105k embed calls, batched).
- 500MB free disk for the CH volume + embedding cache.

The embedding cache (`seed/.embedding_cache/*.json`) is gitignored but persists
across `make seed` runs, so you pay the Gemini cost once.

## Directory layout

```
benchmarks/
├── README.md              (this file)
├── Makefile               (reproducibility entry point)
├── docker-compose.yml     (pinned CH 26.3 image, port 18124)
├── seed/
│   ├── 01_schema.sql      (HOT Memory, WARM MergeTree+HNSW, GRAPH edges)
│   ├── 02_seed.py         (deterministic data gen + Gemini embeddings w/ cache)
│   └── .embedding_cache/  (gitignored; sha256(input)->vector JSON files)
├── queries/
│   ├── T1_scan_live_stream.sql
│   ├── T2_open_investigation.sql
│   ├── T3_recall_memory.sql
│   ├── T4_semantic_search.sql
│   ├── T5_fetch_record.sql
│   ├── T6_replay_session.sql
│   ├── T7_save_memory.sql
│   └── T8_graph_traverse.sql
├── harness/
│   ├── run_bench.py       (runs each query, captures system.query_log)
│   └── render_report.py   (results.json + results.md)
├── baseline/
│   └── results.reference.json  (frozen expected values; drift check source)
└── results/
    └── <timestamp>.json   (gitignored; your runs)
```

## How the harness works

1. `make up` boots `clickhouse/clickhouse-server:26.3.x` on port 18124. Writes to
   a named Docker volume. We use a dedicated container so the benchmark never
   touches your dev cluster state.
2. `make seed` runs `seed/01_schema.sql` (idempotent DDL) then `seed/02_seed.py`,
   which:
   - Generates 100,000 events deterministically (random service, severity, time
     window, seeded).
   - Generates 5,000 incidents with human-readable bodies from a fixed template.
   - Embeds incident bodies with `gemini-embedding-001` (768-d). Cache hit on
     reruns. Batches of 100.
   - Loads 40 services + ~120 edges into `service_edges`.
3. `make bench` runs each of 8 query files 50 times, after a 5-iteration warmup.
   For every run, it pulls `read_rows`, `read_bytes`, `query_duration_ms`, and
   `result_rows` from `system.query_log` using the `query_id` it sets per run.
4. `make report` diffs today's run against `baseline/results.reference.json`.
   Any drift in `read_rows` or `read_bytes` is flagged.

## How `make check` catches regressions

```bash
make bench && make check
```

`check` loads the most recent `results/<timestamp>.json`, reads
`baseline/results.reference.json`, and asserts:

- `read_rows` matches exactly (0% tolerance)
- `read_bytes` matches within 5% (small variance from CH internal metadata)
- p50 `query_duration_ms` within 3× the baseline (laptops vary; 3× covers it)

It exits 1 on drift, printing which tool regressed and by how much. Wire this
into CI if you want.

## Updating the baseline

If a schema change legitimately moves the numbers:

```bash
make bench
cp results/<timestamp>.json baseline/results.reference.json
git add baseline/results.reference.json
git commit -m "bench: update baseline after <reason>"
```

Keep the reason explicit in the commit. Baselines drift silently otherwise.

## The one number the deck cites

From the live SRE session report at `docs/report/example-sre-report.html`:

- 448 rows scanned across 5 tool calls
- 68 KB read
- 47 ms end-to-end
- 0.003% selectivity vs a brute-force scan of `memory_long`

The 448 rows figure comes from one specific session with a specific embedding
and a specific set of primary-key values. The numbers you get from `make bench`
will be slightly different because the harness runs every tool query 50 times
and reports p50. What's identical: the *shape* of the numbers. Filter-first
retrieval touches thousands of rows at worst, never millions.

## Comparison bench (optional)

`benchmarks/comparison/` runs the same agent session against a Qdrant + Redis +
Postgres + Neo4j stack for apples-to-apples LoC and latency. See
`comparison/README.md` in that directory. We substitute Qdrant for Pinecone
because Pinecone has no local-dev mode; the substitution is called out honestly
in the results.
