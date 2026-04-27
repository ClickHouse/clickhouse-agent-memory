# Cookbooks

Three runnable CLI demos -- AI SRE (observability), AI NetOps (telco),
AI SOC (cybersecurity) -- backed by one ClickHouse cluster with HOT,
WARM, and GRAPH tiers.

For the full story (architecture, tier diagrams, MCP tools, demo flow,
reproducibility), see the project root README:
[`../README.md`](../README.md).

## Run the demos

```bash
make setup          # write .env from template
make start          # start ClickHouse + demo-app
make seed           # populate 17 tables with synthetic data
make run            # run all three cookbooks
```

Single cookbook: `make run-one COOKBOOK=observability` (or `telco`,
`cybersecurity`).

No API key needed -- a deterministic hash-based embedding fallback keeps
demos reproducible. Add `GOOGLE_KEY` or `OPENAI_API_KEY` to `.env` for
live LLM narration.

## Make targets

| Target    | Description                                  |
|-----------|----------------------------------------------|
| `setup`   | Copy `.env` template                         |
| `start`   | Start ClickHouse + demo-app via compose      |
| `stop`    | Stop services (keep data)                    |
| `seed`    | Seed ClickHouse with demo data               |
| `run`     | Run all cookbook demos                       |
| `run-one` | Run single cookbook (`COOKBOOK=observability`) |
| `logs`    | Follow container logs                        |
| `status`  | Show service status                          |
| `clean`   | Stop and remove all data volumes             |

## Directory layout

```
cookbooks/
  observability/        AI SRE demo (6 steps)
  telco/                AI NetOps demo (6 steps)
  cybersecurity/        AI SOC demo (7 steps)
  mcp_server/           FastMCP streamable-http, 8 tools
  shared/
    client.py           ClickHouse client, embeddings, LLM, CLI formatting
    schema/             ClickHouse DDL (17 tables, 3 domains)
    seeders/            Synthetic data generators
  docker-compose.yml    ClickHouse + demo-app
  Makefile              Orchestration commands
  main.py               CLI runner (seed / run / run-all)
```

Provider configuration, full tier explanations, and the MCP tool list
are documented in the [root README](../README.md).
