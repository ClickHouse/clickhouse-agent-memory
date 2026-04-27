# Enterprise Agent Memory test suite

Two test layers: pure-function unit tests that run anywhere, and
integration tests that talk to a live ClickHouse seeded via `make seed`.

## Layout

```
tests/
  conftest.py                  top-level sys.path + marker registration
  unit/                        no external services required
    test_tiers.py              _format_latency, tier_badge_markdown, tier_banner
    test_envelope.py           envelope shape, row preview, _serialise_rows
  integration/                 require a running, seeded ClickHouse
    conftest.py                ch_client + seeded fixtures
    test_schema.py             20 expected tables, HNSW index, Memory engines
    test_domain_tools.py       five domain MCP tools across three domains
    test_conversation_tools.py three conversation tools + remember/recall round-trip
    test_comparison.py         both stitched and clickhouse agents run clean
```

## Running

All commands assume you are at the repo root (`project_final/`).

Unit tests only, no services needed:

```
pytest -m unit
```

Full suite (assumes `cd cookbooks && make start && make seed` has been run):

```
pytest
```

Integration tests only:

```
pytest -m integration
```

## Environment variables

- `CLICKHOUSE_HOST` (default `localhost`)
- `CLICKHOUSE_PORT` (default `8123`)
- `CLICKHOUSE_PASSWORD` (default empty)
- `EMBED_DIM` (default `768`). Must match the dim the seeder used.

## When ClickHouse is unreachable

Integration tests skip cleanly with a message pointing you at
`cd cookbooks && make start && make seed`. Unit tests still run. The
suite is always green; "can't reach CH" never turns into a red failure.
