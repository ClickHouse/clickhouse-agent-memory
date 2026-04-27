# Benchmarks — reproducible storage-path numbers

Every performance number cited in the README, ARCHITECTURE doc, blog post, or
slide deck is generated from this directory. If a number appears in marketing
and does not trace back to a query file plus a harness run here, it does not
belong in marketing.

The harness measures the same ClickHouse cluster the cookbooks demo runs
against — it is not a separate bench-scale rig. That keeps the numbers honest:
what the agent actually queries is what we actually measure.

## What this measures

Eight MCP tool queries (one SQL file per tool) replayed against a live
ClickHouse 26.3 cluster. Per query we capture:

- `read_rows` — rows the query actually touched (deterministic across runs)
- `read_bytes` — bytes read from storage (deterministic per data version)
- `query_duration_ms` — wall time from `system.query_log` (varies with CPU)
- `result_rows` — rows returned to the caller

We do NOT measure the LLM picking tools. That varies per model and prompt.
This harness isolates the storage path so the numbers are reproducible
regardless of which agent is orchestrating on top.

## Prerequisites

The harness reuses the cookbook stack. Before running anything here, bring
up and seed that stack from the project root:

```bash
make cli-up        # docker compose up clickhouse + demo-app
make cli-seed      # populate 17 domain + 3 conversation memory tables
```

`benchmarks/Makefile` reads `CH_HTTP` (default `http://localhost:18123`),
`CH_USER`, `CH_PASS`, `CH_DB` (default `enterprise_memory`). Override per-run
or export from `cookbooks/.env`.

## Quick start

```bash
cd benchmarks
set -a; . ../cookbooks/.env; set +a   # picks up CLICKHOUSE_USER / _PASSWORD

make check-cluster   # confirm the cluster is reachable + has rows
make bench           # run each of 8 queries N times, capture system.query_log
make report          # render results/latest.json into results/latest.md
make diff            # compare latest run against baseline/results.reference.json
```

`ITERATIONS` defaults to 50 with 5 warmup iterations. Override:

```bash
make bench ITERATIONS=200 WARMUP=20
```

A full run on a warm Apple Silicon laptop: about 20 seconds end to end for
default settings.

## Reproducibility guarantees

| Metric | Deterministic? | Why |
|---|---|---|
| `read_rows` | yes, exact | Comes from ClickHouse's query planner. No randomness. |
| `read_bytes` | yes, exact | Same source. Compression deterministic per data version. |
| `query_duration_ms` | no, but bounded | Varies with CPU, thermals, disk cache. We report p50 and p95 over N runs. |
| Embedding vectors in seed | yes, per (model, input) | Gemini embeddings are deterministic; cookbook seeder caches by content hash. |
| Seed data | yes, fixed seed | `cookbooks/shared/seeders/seed_all.py` uses `SEED=42` for every random draw. |

The deterministic metrics are the meaningful claim. When the deck or README
cites a row count or byte count, it must match `make bench` exactly. Latencies
will vary on different hardware — we report p50 and p95 over the iteration set
so callers can compare order of magnitude.

## Directory layout

```
benchmarks/
├── README.md            this file
├── Makefile             reproducibility entry point
├── queries/             one .sql file per tool query (T1-T8)
│   ├── T1_scan_live_stream.sql
│   ├── T2_open_investigation.sql
│   ├── T3_recall_memory.sql
│   ├── T4_semantic_search.sql
│   ├── T5_fetch_record.sql
│   ├── T6_replay_session.sql
│   ├── T7_save_memory.sql
│   └── T8_graph_traverse.sql
├── harness/
│   ├── run_bench.py             runs each query, captures system.query_log
│   ├── render_report.py         results/latest.json → results/latest.md
│   ├── run_demos.py             4-scenario demo run, writes demo_scenarios.{json,md}
│   ├── run_execution_report.py  end-to-end agent session simulator
│   └── screenshot_deck.py       Playwright screenshots of slide deck
├── baseline/
│   └── results.reference.json   frozen expected values; drift check source
└── results/                     gitignored; per-run outputs land here
    ├── latest.json
    ├── latest.md
    ├── diff.md
    ├── demo_scenarios.json
    └── demo_scenarios.md
```

## How `make bench` works

1. `make check-cluster` — `SELECT version()` plus a row-count summary of every
   seeded table in the demo database. Fails fast if the cluster is unreachable
   or unseeded.

2. `make bench` runs each of the 8 query files `ITERATIONS` times with
   `WARMUP` warmup iterations first. For every run, the harness sets a unique
   `query_id`, executes the query, then pulls `read_rows`, `read_bytes`,
   `query_duration_ms`, and `result_rows` from `system.query_log` keyed on
   that id. Results land in `results/latest.json` and a derived
   `results/latest.md`.

3. `make report` re-renders `latest.md` from `latest.json`. Useful after a
   manual edit or when reprocessing an older run.

4. `make diff` compares the latest run against `baseline/results.reference.json`
   and writes `results/diff.md`. Drift on `read_rows` or `read_bytes` is the
   signal worth attention.

## Updating the baseline

If a schema change legitimately moves the deterministic numbers:

```bash
make bench
cp results/latest.json baseline/results.reference.json
git add baseline/results.reference.json
git commit -m "bench: re-baseline after <reason>"
```

Keep the reason explicit in the commit message. Baselines drift silently
otherwise.

## Scale note — two modes

The harness ships with two scales:

**Demo scale** (default, against the cookbook cluster on :18123). Tiny
seed: 200 live events, 8 historical incidents, 10-12 graph nodes per
domain. That exercises every query path (HOT scan, WARM HNSW, GRAPH
self-JOIN, INSERT) but is too small to demonstrate compression ratios or
bloom filter granule pruning.

**Bench scale** (isolated cluster on :18124). Larger deterministic seed:
100,000 events, 50,000 incidents (with 768-d vectors + HNSW), 50,000
agent-memory rows, 40 services, 120 dependency edges. Big enough that
the table spans 25+ granules so bloom filter pruning is observable, and
the per-column compression ratios reach their natural shape (5-63x on
structured columns; 1.0x on random vectors).

Bench scale uses deterministic embeddings (sha256-seeded numpy unit
vectors) so seeding takes ~13 seconds with no API key. The vectors are
not semantically meaningful but they exercise HNSW indexing and the
same SQL query path as real embeddings.

```bash
# Bring up the isolated bench cluster on :18124
cd benchmarks
make up                      # docker compose up, schema applied via init script
make seed                    # populates 100k events / 50k incidents / 50k memories
make check                   # row counts + index status

# Run the harness against the bench cluster
make bench CH_HTTP=http://localhost:18124
make report
make diff CH_HTTP=http://localhost:18124   # vs baseline (re-baseline if needed)

# Tear down
make down                    # keeps the named volume
```

Override row counts via env vars:

```bash
N_EVENTS=1000000 N_INCIDENTS=200000 REBUILD=1 make seed
```

If you need production-scale numbers, run this harness against a
production ClickHouse cluster with your own data. The same query files
apply.

## Demo session report

`harness/run_execution_report.py` produces a full end-to-end agent session
walk-through under `docs/report/`, complete with the SQL each tool emitted,
the rows returned, and per-step latency. It is the artifact buyers and
solution architects walk through during a demo. Re-run it any time:

```bash
python3 benchmarks/harness/run_execution_report.py
# writes docs/report/execution-report.html and benchmarks/results/execution_report.json
```

The fixed example session at `docs/report/example-sre-session.json` is the
frozen artifact the deck and blog cite. Its current totals (re-summable any
time):

```bash
python3 -c "
import json, ast
data = json.load(open('docs/report/example-sre-session.json'))
total_rows = total_bytes = total_ms = 0
for st in data['steps']:
    env = ast.literal_eval(st['envelope'])
    p = env.get('precision') or {}
    total_rows  += p.get('rows_read')  or 0
    total_bytes += p.get('bytes_read') or 0
    total_ms    += env.get('latency_ms') or 0
print(f'rows={total_rows}, bytes={total_bytes:,} ({total_bytes/1024:.1f} KB), ms={total_ms:.2f}')
"
```

Current sums: 5 tool calls, 455 rows scanned, 67 KB read, 84 ms end to end.
Selectivity vs a brute-force scan of `agent_memory_long`: 0.003%.

If a reader cites different totals, they are quoting a stale snapshot. Refresh
with the script above.
