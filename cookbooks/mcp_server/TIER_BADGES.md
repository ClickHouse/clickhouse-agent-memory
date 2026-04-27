# Tier Badges

## What is `banner_markdown`

Every MCP tool response envelope produced by `tiers.envelope()` includes a
`banner_markdown` field. It is a single-line markdown blockquote that names
the memory tier, the ClickHouse engine, the domain and operation, and the
measured latency for that call.

## Why it exists

The three memory tiers are the whole story of this project. The raw JSON
envelope already carries `tier`, `tier_engine`, and `latency_ms`, but those
values live inside a collapsible tool-call panel in LibreChat. Users skim
past them. `banner_markdown` promotes the tier identity into the agent's
visible text reply so HOT, WARM, and GRAPH become felt on every turn.

The LibreChat preset prompt instructs the agent to echo `banner_markdown`
verbatim before its natural-language answer. That is the only wiring needed
on the LibreChat side; this file owns the server-side format.

## Format

```
> [ TIER ] Engine Name | domain.operation | N.NN ms
```

- Markdown blockquote so LibreChat renders a visually distinct quoted line.
- Tier label in square brackets with one space padding: `[ HOT ]`, `[ WARM ]`,
  `[ GRAPH ]`, `[ RESULT ]`.
- Engine comes from `TIER_META[tier]["engine"]`.
- `domain.operation` identifies the exact tool work performed.
- Latency uses `N.NN ms` under 10 ms, `N.N ms` under 100 ms, `N ms` otherwise.

## Examples

```
> [ HOT ] ClickHouse Memory Engine | observability.scan_live_stream | 0.82 ms
> [ WARM ] ClickHouse MergeTree + Vector Search | telco.vector_similarity_search | 134.7 ms
> [ GRAPH ] ClickHouse SQL JOINs | cybersecurity.graph_multi_hop_traversal | 3.0 ms
> [ RESULT ] Agent Context Assembly | observability.context_assembly | 178 ms
```

## Self-check

Run `python cookbooks/mcp_server/tiers.py` to print a sample badge for each
of the four tiers.
