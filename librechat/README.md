# LibreChat companion

LibreChat UI + the `memory` MCP server (FastMCP, streamable-http) wired
to the same three-tier memory the CLI cookbooks use. Every tool response
returns tier label, SQL, latency, and row count so the chat UI visibly
shows which memory tier each answer came from.

For the full project story (architecture, tier diagrams, demo flow), see
the project root README: [`../README.md`](../README.md).

## Bring it up

The cookbook stack must be up first -- LibreChat attaches to its docker
network and queries the same ClickHouse:

```bash
cd ../cookbooks
make setup && make start && make seed
```

Then:

```bash
cd ../librechat
make setup            # writes .env from .env.example
# edit .env -- add GOOGLE_KEY (default presets use Gemini)
make start            # LibreChat + MongoDB + Meilisearch + memory-mcp
open http://localhost:3080
```

First time: register a local account (stored in the demo MongoDB).

## Two ways to drive the memory tools

### 1. One-click persona chat

From the model-spec dropdown, pick:

- **AI SRE Agent (Observability)** -- incidents, blast radius, past root-cause match
- **AI NetOps Agent (Telco)** -- network faults, topology, customer impact
- **AI SOC Agent (Cybersecurity)** -- security events, threat intel, lateral movement

Each preset ships with a tailored system prompt that tells the model
when to call each `memory_*` tool and to announce HOT / WARM / GRAPH on
every call.

### 2. Full tool-calling via Agents

Switch endpoint to **Agents**, create a new agent, and copy the template
from `agents/ai-sre-agent.md` (or NetOps / SOC). Toggle on the five MCP
tools (`memory_hot_scan`, `memory_hot_workspace`, `memory_warm_search`,
`memory_warm_lookup`, `memory_graph_traverse`). Save and chat.

## MCP tool envelope

Every tool returns the same shape:

```json
{
  "tier": "HOT",
  "tier_banner": ">>> HOT MEMORY >>>",
  "tier_engine": "ClickHouse Memory Engine",
  "tier_latency_profile": "sub-5ms | volatile | in-memory",
  "domain": "observability",
  "operation": "search_events",
  "sql": "SELECT ... FROM obs_events_stream ...",
  "latency_ms": 0.82,
  "row_count": 5,
  "rows_preview": [...],
  "insights": {"top_service": "svc-payments", "top_error": "DB_TIMEOUT"},
  "next_tool_hint": "memory_hot_workspace ..."
}
```

LibreChat renders this JSON in the tool-call panel -- that is how the
HOT / WARM / GRAPH story surfaces in chat.

| Tool                      | Tier   | Purpose                                               |
|---------------------------|--------|-------------------------------------------------------|
| `memory_hot_scan`         | HOT    | Scan live event / state stream for a domain           |
| `memory_hot_workspace`    | HOT    | Materialise per-case workspace and group it           |
| `memory_warm_search`      | WARM   | Cosine-similarity search over historical incidents    |
| `memory_warm_lookup`      | WARM   | Pull playbook by id, or vector lookup of threat intel |
| `memory_graph_traverse`   | GRAPH  | Multi-hop traversal across deps / topology / access   |

## Provider configuration

All four providers are pre-wired in `librechat.yaml`. Set only what you
need in `.env`:

| Variable            | Provider                                                       |
|---------------------|----------------------------------------------------------------|
| `GOOGLE_KEY`        | Gemini (default for all presets)                               |
| `OPENAI_API_KEY`    | OpenAI                                                         |
| `ANTHROPIC_API_KEY` | Anthropic                                                      |
| `OLLAMA_BASE_URL`   | Local Ollama (default `http://host.docker.internal:11434`)     |

Switch providers per-agent in the UI, or edit the preset `endpoint` /
`model` fields in `librechat.yaml`.

## Tearing down

```bash
make stop             # stop, keep data
make clean            # stop and remove LibreChat volumes (Mongo, Meili)
```

The cookbook stack (ClickHouse) is independent -- tear it down via
`cd ../cookbooks && make stop` / `make clean`.
