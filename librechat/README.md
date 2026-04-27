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
from `agents/ai-sre-agent.md` (or NetOps / SOC). Toggle on the eight MCP
tools — five domain (`search_events`, `create_case`, `semantic_search`,
`get_record`, `find_related_entities`) plus three conversation-memory
(`list_session_messages`, `get_conversation_history`, `add_memory`). Save
and chat.

## MCP tool envelope

Every tool returns the same envelope shape (source: `cookbooks/mcp_server/tiers.py:envelope()`):

```json
{
  "tier": "HOT",
  "domain": "observability",
  "operation": "search_events",
  "latency_ms": 0.82,
  "row_count": 5,
  "insights": {"top_service": "svc-payments", "top_error": "DB_TIMEOUT"},
  "precision": {
    "filters_applied": ["service = 'svc-payments'", "ts >= now() - INTERVAL 15 MINUTE"],
    "index_hint": "Memory engine, no index needed",
    "embedding_dim": null,
    "rows_read": 200,
    "bytes_read": 28193,
    "rows_returned": 5,
    "selectivity": "2.5%",
    "written_rows": null
  },
  "rows_preview": [...],
  "sql": "SELECT ts, service, host, level, message\nFROM obs_events_stream\nWHERE service = 'svc-payments'\n  AND ts >= now() - INTERVAL 15 MINUTE\nORDER BY ts DESC LIMIT 20"
}
```

LibreChat renders this JSON in the tool-call panel -- that is how the
HOT / WARM / GRAPH story surfaces in chat.

| Tool                          | Tier   | Purpose                                                        |
|-------------------------------|--------|----------------------------------------------------------------|
| `search_events`               | HOT    | Scan live event / state stream for a domain                    |
| `create_case`                 | HOT    | Materialise a per-case investigation workspace                 |
| `semantic_search`             | WARM   | Cosine-similarity search over historical incidents             |
| `get_record`                  | WARM   | Pull a runbook or threat-intel record by id or by query        |
| `find_related_entities`       | GRAPH  | Multi-hop traversal across deps / topology / access            |
| `list_session_messages`       | HOT    | Replay the last N turns of the current chat session            |
| `get_conversation_history`    | WARM   | Cross-session semantic recall for this user                    |
| `add_memory`                  | WARM   | Persist a long-term fact the agent should remember             |

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
