# Stitched Stack vs ClickHouse -- Side-by-Side Exhibit

This exhibit takes one AI agent scenario and implements it two ways. The
goal is not a benchmark. The goal is to show, structurally, the cost of
federating four services versus using one.

## The scenario

`svc-payments` is failing. An AI SRE agent must, in order:

1. Scan the live event stream for recent errors on that service. (HOT)
2. Create an investigation workspace and load correlated events into it. (HOT)
3. Find similar past incidents via vector similarity. (WARM)
4. Walk the service dependency graph to compute blast radius. (GRAPH)
5. Pull the resolution runbook from the best-matching past incident. (WARM)
6. Synthesise an incident brief the agent can reason on.

Both implementations do the same six steps against the same seed data
shape. They differ only in where the data lives.

## The two implementations

- [`stitched/agent.py`](stitched/agent.py) -- Pinecone (WARM vector),
  Redis (HOT stream + workspace), Neo4j (GRAPH blast radius), Postgres
  (WARM runbook lookup). Four clients. Four protocols. Four failure modes.
- [`clickhouse/agent.py`](clickhouse/agent.py) -- one ClickHouse client,
  one database, reuses the existing cookbook tables (`obs_events_stream`,
  `obs_incident_workspace`, `obs_historical_incidents`, `obs_services`,
  `obs_dependencies`).

Both files are deliberately honest: the stitched side uses the real
libraries, real connection patterns, and real error handling. Where a
real service is not reachable, each client falls back to a clearly marked
in-memory double so the reader can still run the code end to end.

## Structural comparison

Numbers below are produced by `make compare` (see `compare.py`). They are
intentionally about structure, not latency.

| Dimension | Stitched | ClickHouse |
|---|---|---|
| Lines of code (blanks/comments excluded) | see `make compare` | see `make compare` |
| Client libraries | `redis`, `pinecone`, `neo4j`, `psycopg2` (4) | `clickhouse_connect` (1) |
| Distinct services to run | 4 (Pinecone, Redis, Neo4j, Postgres) | 1 (ClickHouse) |
| Query languages in flight | Redis protocol, Pinecone REST, Cypher, SQL | SQL |
| Distance-metric name skew | yes: Pinecone `score`, pgvector `<=>`, Redis `COSINE` | no: `cosineDistance()` everywhere |
| Cross-tier write in one transaction | no | yes (single `INSERT ... SELECT`) |
| Cross-tier `JOIN` in one query | no, stitched in Python | yes, native SQL |
| Operational surfaces (backup, upgrade, monitor, RBAC) | 4 | 1 |

### API skew you only feel once you build it

Even the simple "find similar past incidents" step carries four different
mental models across three databases:

- Pinecone returns `score` where higher is more similar.
- pgvector expects a `<=>` or `<->` operator and returns raw distance
  where lower is more similar.
- Redis (RediSearch) uses `VECTOR_RANGE` or a `KNN` clause with the
  `COSINE` distance keyword.
- ClickHouse exposes `cosineDistance(a, b)` as a plain function in SQL.

Translating between these in application code is where bugs live.

## What the stitched stack does buy you

Honest list, because sales decks should be honest:

- Pinecone's managed service is genuinely good at very large, very hot
  ANN workloads. If your agent memory is billions of vectors served at
  single-digit-millisecond p50 with no filter, Pinecone has been built
  for that shape for years.
- Redis at a few million ops/second on a single shard is still
  unreasonably fast for key/value and streams.
- Neo4j's Cypher is more expressive than SQL for deep traversals of
  five-hop-plus graphs with path-constrained matching.
- Postgres with pgvector is a fine choice if you already run Postgres
  and vectors are a feature, not the workload.

The exhibit does not claim ClickHouse beats those at their chosen peak.
It claims that agent memory looks like filtered retrieval over time-series
data with graph relationships, and that shape fits one database cleanly.

## Running the exhibit

### ClickHouse side

Assumes the cookbook stack is already up (`cd ../cookbooks && make start
seed`).

```
make clickhouse
```

### Stitched side

Optionally bring up the four services:

```
docker compose -f stitched/docker-compose.yml up -d
pip install -r stitched/requirements.txt
```

Then run:

```
make stitched
```

Without the containers, `stitched/agent.py` prints a yellow fallback
notice for each unreachable service and runs against an in-memory double.
This is the "inspect the code" side of the exhibit -- the structural
point does not depend on whether Pinecone or Neo4j is actually running.

### Structural comparison table

```
make compare
```

## Scope guardrail

Everything in this directory is isolated. Nothing under `cookbooks/`,
`librechat/`, `mcp_server/`, or `docs/` has been modified. The
`stitched/requirements.txt` file holds any extra libraries the stitched
side needs; the ClickHouse side reuses what the rest of the project
already has.
