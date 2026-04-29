# Enterprise Agent Memory on ClickHouse

**One cluster. Three memory tiers. No stitched stack.**

AI agents need three shapes of memory: hot signals (what's happening now), history plus semantics (have we seen this before), and relationships (what's downstream). Most teams stitch four databases together to serve them, Redis plus Pinecone plus Postgres plus Neo4j, with glue code absorbing the cost. This project shows one ClickHouse cluster doing the same job with typed SQL, one auth story, and one bill.

Three runnable demos ship in the box, SRE observability, telco NetOps, and security SOC, each one walking a real investigation scenario across all three tiers. A FastMCP server exposes the same tiers as typed tools, so LibreChat and any MCP-aware agent get identical context as the CLI.

> **Memory vs context.** Memory is what's stored. Context is what reaches the LLM. This project is the memory substrate that makes good context cheap.

---

## Table of contents

1. [Quick start](#quick-start)
2. [Getting started in detail](#getting-started-in-detail)
3. [Architecture](#architecture)
4. [Memory tiers, end to end](#memory-tiers-end-to-end)
   - [HOT tier](#hot-tier-memory-engine-sub-5ms)
   - [WARM tier](#warm-tier-mergetree--hnsw-50-500ms)
   - [GRAPH tier](#graph-tier-sql-joins-on-mergetree-3-10ms)
5. [MCP tools](#mcp-tools)
6. [Demo flow](#demo-flow)
7. [Directory map](#directory-map)
8. [Reproduce every number](#reproduce-every-number)

---

## Quick start

Two paths depending on whether you want the terminal demo or the chat UI.

### Path A · CLI only (fastest)

```bash
cd cookbooks/
make setup          # writes .env from template
make start          # starts ClickHouse + demo-app containers
make seed           # populates 17 tables with synthetic data
make run            # runs all three cookbook demos (SRE, NetOps, SOC)
```

No API key needed. A deterministic hash-based embedding fallback keeps the demos reproducible without any provider credentials. Add `GOOGLE_KEY` or `OPENAI_API_KEY` to `.env` if you want live LLM narration.

### Path B · CLI plus LibreChat (full experience)

From `project_final/`:

```bash
make setup          # writes .env files in cookbooks/ and librechat/
# edit librechat/.env, add GOOGLE_KEY (Gemini is the default preset)
make all-up         # brings up cookbooks + seeds + LibreChat
open http://localhost:3080
```

LibreChat gives you the AI SRE preset wired to the `memory` MCP server. Every tool response renders tier, SQL, and latency inline so the HOT / WARM / GRAPH narrative surfaces in the chat.

---

## Getting started in detail

### Prerequisites

| Tool          | Version     | Why                                          |
|---------------|-------------|----------------------------------------------|
| Docker        | 24+         | ClickHouse + demo-app + LibreChat containers |
| Python        | 3.11+       | Local dev, benchmarks, pytest                |
| `make`        | any         | Top-level orchestration                      |
| 4 GB RAM free |             | ClickHouse + Mongo + Meilisearch + LibreChat |

### First-run checklist

1. `docker ps` returns empty or unrelated containers (no port 8123 / 3080 conflicts).
2. `cookbooks/.env` exists after `make setup`. Optional: paste a provider key.
3. `make start` logs end with `ClickHouse is ready` from the demo-app healthcheck.
4. `make seed` finishes with row counts per table.
5. `make run` prints three coloured tier transition banners, one per cookbook.

---

## Architecture

```mermaid
flowchart TB
    subgraph CLIENTS["Clients"]
        CLI[CLI cookbook runner<br/>python main.py]
        UI[LibreChat chat UI<br/>Gemini / OpenAI / Anthropic / Ollama]
        AGENTS[Any MCP-aware agent]
    end

    CLIENTS --> MCP

    MCP["FastMCP server<br/>streamable-http :18765<br/>8 typed tools"]

    MCP --> CH[(ClickHouse 26.3 cluster<br/>enterprise_memory db)]

    subgraph TIERS["One cluster, three tiers"]
        direction LR
        HOT["HOT<br/>Memory engine<br/>sub-5ms<br/>live events, workspaces"]
        WARM["WARM<br/>MergeTree + HNSW<br/>50-500ms<br/>history, semantics"]
        GRAPH["GRAPH<br/>MergeTree edges<br/>3-10ms at 2 hops<br/>deps, topology, access"]
    end

    CH -.-> HOT
    CH -.-> WARM
    CH -.-> GRAPH

    style CLI fill:#1a1a1a,stroke:#b085dd,color:#fff
    style UI fill:#1a1a1a,stroke:#b085dd,color:#fff
    style AGENTS fill:#1a1a1a,stroke:#b085dd,color:#fff
    style MCP fill:#faff00,stroke:#1a1a1a,color:#1a1a1a
    style CH fill:#f2b366,stroke:#1a1a1a,color:#1a1a1a
    style HOT fill:#faff00,stroke:#1a1a1a,color:#1a1a1a
    style WARM fill:#f2b366,stroke:#1a1a1a,color:#1a1a1a
    style GRAPH fill:#6be07a,stroke:#1a1a1a,color:#1a1a1a
```

The agent talks to one endpoint. One cluster holds everything. Each tier is a ClickHouse table engine choice, not a separate piece of infrastructure.

> **Cold tier note.** ClickHouse natively supports TTL-based tiering of MergeTree parts to S3, GCS, or ABS via storage policies. This demo does NOT configure that — every table lives on the cluster's local volume. To enable it in production, attach an object-store disk in `storage_config.xml` and add `TTL ts + INTERVAL 90 DAY TO VOLUME 'cold'` to the WARM tables. See `docs/ARCHITECTURE.md` for the rough shape.

### Why one cluster beats four databases

| Pain in the stitched stack | What disappears here                                    |
|----------------------------|---------------------------------------------------------|
| Four SDKs, four auth stories | One SDK, one TLS cert, one IAM role                   |
| ETL sync between stores    | No sync, all data lives in ClickHouse                  |
| N+3 round trips per agent turn | Single cluster, typed SQL per tier                 |
| Four billing lines, four on-calls | One cluster to monitor and size                  |
| Mock / prod drift across stores | Same engine in dev, CI, and prod                  |

---

## Memory tiers, end to end

Each tier maps to a ClickHouse engine choice. The diagrams below show the data and process flow for a single tool call, from the agent picking the tool to the typed envelope landing back in the LLM context.

### HOT tier · Memory engine · sub-5ms

Real-time telemetry and investigation workspaces. Volatile on restart, in-RAM, exact scans are cheap because rows stay tiny and time-bounded.

```mermaid
sequenceDiagram
    autonumber
    participant Agent as Agent / LLM
    participant MCP as FastMCP server
    participant CH as ClickHouse<br/>(Memory engine)

    Agent->>MCP: search_events(domain=observability,<br/>filter="service='svc-orders'", window_minutes=60)
    MCP->>MCP: validate domain + param types
    MCP->>CH: SELECT ts, service, host, level, message<br/>FROM obs_events_stream<br/>WHERE service='svc-orders'<br/>  AND ts >= now() - INTERVAL 60 MINUTE<br/>ORDER BY ts DESC LIMIT 200
    Note over CH: Memory engine:<br/>no disk, no merge,<br/>exact scan of recent rows
    CH-->>MCP: 200 rows, ~0.2 ms server time
    MCP->>MCP: wrap in envelope:<br/>{tier, domain, operation, sql,<br/> latency_ms, row_count, rows_preview,<br/> precision, insights}
    MCP-->>Agent: typed JSON envelope
    Agent->>Agent: read summary + decide next tool
```

**Tables that live here:** `obs_events_stream`, `obs_incident_workspace`, `telco_network_state`, `telco_fault_workspace`, `sec_events_stream`, `sec_case_workspace`, `conv_session_messages`.

**When HOT is the right answer:** you need the last few minutes, you need low p50 over sequential scans of a small window, you do not need historical recall.

**Honest caveat:** Redis alone is faster at single-key lookups. HOT wins here because the same cluster also serves WARM and GRAPH in the next breath of the same agent turn.

### WARM tier · MergeTree + HNSW · 50-500ms

Historical incidents, runbooks, threat intel, past conversations. MergeTree on disk with a `vector_similarity` HNSW index on a 768-dim `Array(Float32)`. The tier splits conceptually into two calls: vector similarity to find candidate IDs, then a keyed fetch to hydrate the record.

```mermaid
flowchart TB
    START([Agent: have we seen<br/>this pattern before?]) --> EMBED

    EMBED[1 · Embed query<br/>Gemini / OpenAI / hash fallback<br/>→ 768-dim vector] --> SEMANTIC

    SEMANTIC[2 · semantic_search tool<br/>SELECT id, title, body,<br/>  cosineDistance emb, :q AS dist<br/>FROM obs_historical_incidents<br/>WHERE ts &gt;= now - INTERVAL 180 DAY<br/>ORDER BY dist ASC LIMIT 5]

    SEMANTIC --> HNSW{HNSW index<br/>on emb column}
    HNSW --> TOPK[Top-5 candidate IDs<br/>+ distances]

    TOPK --> LOOKUP[3 · get_record tool<br/>SELECT title, body, severity<br/>FROM obs_historical_incidents<br/>WHERE id IN :top_ids]

    LOOKUP --> HYDRATED[Full records<br/>hydrated]

    HYDRATED --> RESULT([Ranked history<br/>returned to agent])

    style START fill:#faff00,stroke:#1a1a1a,color:#1a1a1a
    style EMBED fill:#1a1a1a,stroke:#b085dd,color:#fff
    style SEMANTIC fill:#f2b366,stroke:#1a1a1a,color:#1a1a1a
    style HNSW fill:#333,stroke:#f2b366,color:#fff
    style TOPK fill:#1a1a1a,stroke:#f2b366,color:#fff
    style LOOKUP fill:#f2b366,stroke:#1a1a1a,color:#1a1a1a
    style HYDRATED fill:#1a1a1a,stroke:#f2b366,color:#fff
    style RESULT fill:#6be07a,stroke:#1a1a1a,color:#1a1a1a
```

Two SQL calls live in one engine. No vector-store-to-record-store sync problem, no ID drift, same backup and same auth.

**Tables that live here:** `obs_historical_incidents`, `telco_network_events`, `sec_historical_incidents`, `sec_threat_intel`, `conv_long_term_memory`.

**Filter-first retrieval matters.** The SQL above uses the `ts >= now() - INTERVAL N DAY` predicate and a bloom-filter skip index on high-cardinality columns to prune before HNSW runs. Shrink the candidate set by metadata first, then rank by similarity. That is the tax your tokens avoid.

### GRAPH tier · SQL JOINs on MergeTree · 3-10ms

Dependency graphs, network topology, access graphs. No separate graph database, edges live in MergeTree tables, traversal is `LEFT ANY JOIN` + `UNION ALL` per hop.

```mermaid
flowchart LR
    START([Agent: what breaks<br/>if svc-orders fails?])

    START --> TOOL[find_related_entities tool<br/>domain=observability<br/>entity='svc-orders'<br/>max_hops=2]

    TOOL --> H1

    subgraph HOPS["Traversal per hop"]
        direction TB
        H1[Hop 1<br/>SELECT s.service_id, 1 AS hops<br/>FROM obs_dependencies d<br/>LEFT ANY JOIN obs_services s<br/>  ON s.service_id = d.from_service<br/>WHERE d.to_service = 'svc-orders']
        H1 --> UNION[UNION ALL]
        UNION --> H2[Hop 2<br/>... same pattern,<br/>d2.to_service IN<br/>  Hop 1 result set]
    end

    H2 --> SKIP{bloom_filter<br/>skipping index on<br/>to_service}
    SKIP -.prunes granules.-> H1
    SKIP -.prunes granules.-> H2

    H2 --> RESULT([Related services<br/>+ hop distance<br/>3-10 ms · bloom prunes<br/>98.67% of rows at bench scale])

    style START fill:#faff00,stroke:#1a1a1a,color:#1a1a1a
    style TOOL fill:#6be07a,stroke:#1a1a1a,color:#1a1a1a
    style H1 fill:#1a1a1a,stroke:#6be07a,color:#fff
    style H2 fill:#1a1a1a,stroke:#6be07a,color:#fff
    style UNION fill:#333,stroke:#6be07a,color:#fff
    style SKIP fill:#333,stroke:#faff00,color:#fff
    style RESULT fill:#6be07a,stroke:#1a1a1a,color:#1a1a1a
```

**Tables that live here:** `obs_services` + `obs_dependencies`, `telco_elements` + `telco_connections`, `sec_assets` + `sec_users` + `sec_access`.

**Honest caveat:** Neo4j and Memgraph still win at shortest-path and community detection at scale. For the 2-hop blast-radius questions enterprise agents actually ask, SQL is faster because it reuses the same engine and skips the network hop.

### The mixed case · one agent turn, three tiers

The payoff is when one agent question needs all three tiers in the same turn. `svc-orders is failing, walk me through it.` becomes four tool calls, three tiers, one cluster.

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant A as Agent / LLM
    participant M as MCP server
    participant CH as ClickHouse

    U->>A: svc-orders is failing,<br/>walk me through it.

    rect rgb(250, 255, 0, 0.1)
    Note right of A: HOT
    A->>M: search_events(service='svc-orders')
    M->>CH: Memory-engine scan, last 60 min
    CH-->>M: 200 rows · 27.5 KB · 1 ms
    M-->>A: envelope
    end

    rect rgb(242, 179, 102, 0.1)
    Note right of A: WARM · semantic
    A->>M: semantic_search(query=error message, k=5)
    M->>CH: HNSW top-5 on obs_historical_incidents
    CH-->>M: 13 rows · 25.5 KB · 4 ms
    M-->>A: envelope
    end

    rect rgb(242, 179, 102, 0.1)
    Note right of A: WARM · lookup
    A->>M: get_record(kind=runbook, id=top_incident_id)
    M->>CH: keyed MergeTree read
    CH-->>M: 1 row · 283 B · 1 ms
    M-->>A: envelope
    end

    rect rgb(107, 224, 122, 0.1)
    Note right of A: GRAPH
    A->>M: find_related_entities(entity='svc-orders', max_hops=2)
    M->>CH: self-JOIN + UNION ALL per hop
    CH-->>M: 32 rows · 0.9 KB · 3 ms
    M-->>A: envelope
    end

    A->>U: synthesized brief:<br/>what's happening, what's similar,<br/>what's downstream
    Note over U,CH: 245 rows · 54.0 KB · 6 ms total<br/>one cluster, one auth, one bill
```

---

## MCP tools

The FastMCP server at `cookbooks/mcp_server/` exposes eight typed tools. Each returns the same envelope shape, `{tier, domain, operation, sql, latency_ms, row_count, rows_preview, precision, insights}`, so the LLM sees identical structure across tiers. The `precision` block carries `rows_read`, `bytes_read`, `selectivity`, and `index_hint` so the LLM (and the reader) can audit how much data each tool actually touched.

```mermaid
flowchart LR
    subgraph DOMAIN["Domain-scoped tools"]
        direction TB
        T1[search_events<br/>HOT · Memory engine]
        T2[create_case<br/>HOT · workspace]
        T3[semantic_search<br/>WARM · HNSW]
        T4[get_record<br/>WARM · keyed lookup]
        T5[find_related_entities<br/>GRAPH · self-JOIN]
    end

    subgraph CONV["Conversation-scoped tools"]
        direction TB
        T6[list_session_messages<br/>HOT · current chat]
        T7[get_conversation_history<br/>WARM · past sessions]
        T8[add_memory<br/>WARM · write long-term fact]
    end

    LLM[Agent / LLM] --> T1 & T2 & T3 & T4 & T5 & T6 & T7 & T8

    T1 & T2 & T6 -.-> HOT[(HOT tables)]
    T3 & T4 & T7 & T8 -.-> WARM[(WARM tables)]
    T5 -.-> GRAPH[(GRAPH tables)]

    style LLM fill:#1a1a1a,stroke:#b085dd,color:#fff
    style T1 fill:#faff00,stroke:#1a1a1a,color:#1a1a1a
    style T2 fill:#faff00,stroke:#1a1a1a,color:#1a1a1a
    style T6 fill:#faff00,stroke:#1a1a1a,color:#1a1a1a
    style T3 fill:#f2b366,stroke:#1a1a1a,color:#1a1a1a
    style T4 fill:#f2b366,stroke:#1a1a1a,color:#1a1a1a
    style T7 fill:#f2b366,stroke:#1a1a1a,color:#1a1a1a
    style T8 fill:#f2b366,stroke:#1a1a1a,color:#1a1a1a
    style T5 fill:#6be07a,stroke:#1a1a1a,color:#1a1a1a
```

| Tool                       | Tier  | Engine path                      | Use for                                       |
|----------------------------|-------|----------------------------------|-----------------------------------------------|
| `search_events`            | HOT   | Memory engine scan               | Live telemetry, last N minutes                |
| `create_case`              | HOT   | Memory workspace insert          | Investigation sandbox                         |
| `semantic_search`          | WARM  | HNSW `cosineDistance` top-K      | Have we seen this pattern                     |
| `get_record`               | WARM  | Keyed MergeTree read             | Hydrate runbook / resolution by ID            |
| `find_related_entities`    | GRAPH | Self-JOIN + UNION ALL            | Blast radius, topology walks                  |
| `list_session_messages`    | HOT   | Memory engine, current chat      | Replay last N turns of this session           |
| `get_conversation_history` | WARM  | HNSW across all past sessions    | Cross-session recall for this user            |
| `add_memory`               | WARM  | MergeTree insert with embedding  | Write a long-term fact the agent remembers    |

Full handler source: `cookbooks/mcp_server/server.py` and `cookbooks/mcp_server/conversation.py`. SQL templates: `cookbooks/mcp_server/queries.py`.

---

## Demo flow

Three cookbooks, one canonical entity (`svc-orders`), same three-tier pattern.

```mermaid
flowchart TB
    S([User opens LibreChat<br/>SRE preset]) --> Q[svc-orders is failing,<br/>walk me through it.]

    Q --> D1[Demo 1 · HOT alone<br/>What just happened?]
    D1 --> D2[Demo 2 · WARM alone<br/>Have we seen this before?]
    D2 --> D3[Demo 3 · GRAPH alone<br/>What breaks if this fails?]
    D3 --> D4[Demo 4 · MIXED<br/>Walk me through it]

    D4 --> OUT([Typed rows land<br/>in agent context<br/>LLM writes the brief])

    D1 -.-> HOT1[(obs_events_stream)]
    D2 -.-> WARM1[(obs_historical_incidents)]
    D3 -.-> GRAPH1[(obs_dependencies<br/>+ obs_services)]
    D4 -.-> ALL[(all three tiers)]

    style S fill:#faff00,stroke:#1a1a1a,color:#1a1a1a
    style Q fill:#1a1a1a,stroke:#b085dd,color:#fff
    style D1 fill:#faff00,stroke:#1a1a1a,color:#1a1a1a
    style D2 fill:#f2b366,stroke:#1a1a1a,color:#1a1a1a
    style D3 fill:#6be07a,stroke:#1a1a1a,color:#1a1a1a
    style D4 fill:#b085dd,stroke:#1a1a1a,color:#1a1a1a
    style OUT fill:#6be07a,stroke:#1a1a1a,color:#1a1a1a
```

**Scenario numbers, measured live** (`python3 benchmarks/harness/run_demos.py`):

| Demo            | Tool call                     | Rows | Bytes    | p50 latency |
|-----------------|-------------------------------|------|----------|-------------|
| 1 · HOT         | `search_events`               | 200  | 27.5 KB  | 1 ms        |
| 2 · WARM        | `semantic_search`             | 14   | 25.8 KB  | 3 ms        |
| 3 · GRAPH       | `find_related_entities`       | 32   | 0.9 KB   | 3 ms        |
| 4 · MIXED       | all four tool calls           | 245  | 54.0 KB  | 6 ms total  |

Full walk-through with the exact agent prompts, tool calls, and rendered envelopes: [`docs/demo-script.md`](docs/demo-script.md).

### Other presets

- **Telco NetOps** · `make run-one COOKBOOK=telco` · anchor entity `core-router-01`
- **Security SOC** · `make run-one COOKBOOK=cybersecurity` · anchor entity `user-008`

Same three-tier pattern, different domain schema. All three share `cookbooks/shared/client.py` for the ClickHouse client, embedding, and tier-aware CLI formatting.

---

## Directory map

```
project_final/
├─ Makefile                   top-level: cli-up, librechat-up, all-up
├─ README.md                  this file
├─ docs/
│  ├─ ARCHITECTURE.md         deeper design doc (data model, deployment, scaling)
│  ├─ demo-script.md          demo walk-through with exact tool prompts
│  └─ diagrams/               source .mmd diagrams (rendered inline above)
├─ cookbooks/
│  ├─ observability/          AI SRE demo
│  ├─ telco/                  AI NetOps demo
│  ├─ cybersecurity/          AI SOC demo
│  ├─ mcp_server/             FastMCP streamable-http, 8 tools
│  ├─ shared/
│  │  ├─ client.py            ClickHouse client, embeddings, LLM, CLI formatting
│  │  ├─ schema/              ClickHouse DDL (17 tables, 3 domains)
│  │  └─ seeders/             synthetic data generators
│  ├─ docker-compose.yml      ClickHouse + demo-app
│  └─ Makefile                make setup / start / seed / run
├─ librechat/
│  ├─ docker-compose.yml      LibreChat + MongoDB + Meilisearch + memory-mcp
│  ├─ librechat.yaml          endpoints, MCP server, model presets
│  ├─ agents/                 SRE / NetOps / SOC agent builder templates
│  └─ Makefile                make setup / start / stop / logs
├─ benchmarks/
│  ├─ harness/                run_demos.py + screenshot_deck.py + run_bench.py
│  └─ results/                captured demo_scenarios.json / latest.md
└─ tests/                     integration + unit, `pytest -q`
```

---

## Reproduce every number

The latency, row-count, and byte numbers in this README and the slide deck are emitted by a single harness that hits the live ClickHouse cluster.

```bash
# bring the stack up first (see Quick start)
python3 benchmarks/harness/run_demos.py           # re-runs all 4 scenarios
python3 benchmarks/harness/run_execution_report.py # full HTML report, every SQL
```

Results land in `benchmarks/results/`:
- `demo_scenarios.json` · machine-readable, one row per tier call
- `demo_scenarios.md`   · Markdown table, what the docs cite
- `latest.md`           · last run's summary

All SQL in `cookbooks/mcp_server/queries.py`. All schema in `cookbooks/shared/schema/01_schema.sql`.

---

## License and credits

Open source demo repo. Built against ClickHouse 26.3.9.8, FastMCP, LibreChat. Gemini, OpenAI, Anthropic, Ollama, and vLLM are all first-class providers for both LLM generation and embeddings.
