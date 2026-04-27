# Benchmark run — 2026-04-22T07:34:03Z

- ClickHouse: `26.3.9.8` at `http://localhost:18123` (db=`enterprise_memory`)
- Iterations: 50 (warmup 5)
- Total wall time: 22.59s

## Seed size at run time

| Table | Rows |
|---|---|
| `obs_events_stream` | 200 |
| `agent_memory_long` | 195 |
| `agent_memory_hot` | 185 |
| `knowledge_base` | 30 |
| `sec_access` | 12 |
| `telco_elements` | 12 |
| `telco_network_state` | 12 |
| `obs_dependencies` | 11 |
| `telco_connections` | 11 |
| `obs_services` | 10 |
| `obs_historical_incidents` | 8 |
| `sec_assets` | 8 |
| `sec_events_stream` | 8 |
| `sec_users` | 8 |
| `telco_network_events` | 7 |
| `sec_historical_incidents` | 5 |
| `sec_threat_intel` | 5 |
| `benchmark_writes` | 0 |
| `obs_incident_workspace` | 0 |
| `sec_case_workspace` | 0 |
| `telco_fault_workspace` | 0 |

## Per-tool precision + latency

| Tool | Tier | Rows read (p50) | Bytes read (p50) | Dur p50 ms | Dur p95 ms | Result rows |
|---|---|---:|---:|---:|---:|---:|
| `scan_live_stream` | HOT | 200 | 18.0 KB | 0.0 | 1.0 | 25 |
| `open_investigation` | HOT | 0 | 0 B | 0.0 | 1.0 | 0 |
| `recall_memory` | HOT | 185 | 22.6 KB | 0.0 | 1.0 | 9 |
| `semantic_search` | WARM | 195 | 24.4 KB | 2.5 | 4.0 | 4 |
| `fetch_record` | WARM | 1 | 394 B | 1.0 | 4.0 | 1 |
| `replay_session` | WARM | 195 | 21.5 KB | 2.0 | 3.0 | 10 |
| `save_memory` | WARM (INSERT) | 1 | 1 B | 3.0 | 4.0 | 1 |
| `graph_traverse` | GRAPH | 38 | 1.1 KB | 3.0 | 5.0 | 8 |

## Session totals (excludes INSERT)

- **Total rows read** across a 7-tool read session: **814**
- **Total bytes read**: **87.9 KB**
- **Sum of p50 tool-call durations**: **8.5 ms**

