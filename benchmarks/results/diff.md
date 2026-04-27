# Benchmark run — 2026-04-22T09:24:22Z

- ClickHouse: `26.3.9.8` at `http://localhost:18123` (db=`enterprise_memory`)
- Iterations: 50 (warmup 5)
- Total wall time: 20.54s

## Seed size at run time

| Table | Rows |
|---|---|
| `agent_memory_long` | 201 |
| `obs_events_stream` | 200 |
| `agent_memory_hot` | 185 |
| `obs_incident_workspace` | 75 |
| `knowledge_base` | 30 |
| `sec_access` | 12 |
| `telco_elements` | 12 |
| `telco_fault_workspace` | 12 |
| `telco_network_state` | 12 |
| `obs_dependencies` | 11 |
| `telco_connections` | 11 |
| `obs_services` | 10 |
| `obs_historical_incidents` | 8 |
| `sec_assets` | 8 |
| `sec_events_stream` | 8 |
| `sec_users` | 8 |
| `telco_network_events` | 7 |
| `sec_case_workspace` | 5 |
| `sec_historical_incidents` | 5 |
| `sec_threat_intel` | 5 |
| `benchmark_writes` | 0 |

## Per-tool precision + latency

| Tool | Tier | Rows read (p50) | Bytes read (p50) | Dur p50 ms | Dur p95 ms | Result rows |
|---|---|---:|---:|---:|---:|---:|
| `scan_live_stream` | HOT | 200 | 18.0 KB | 0.0 | 1.0 | 25 |
| `open_investigation` | HOT | 75 | 8.4 KB | 0.0 | 1.0 | 75 |
| `recall_memory` | HOT | 185 | 22.6 KB | 0.0 | 1.0 | 9 |
| `semantic_search` | WARM | 197 | 24.6 KB | 2.0 | 4.0 | 4 |
| `fetch_record` | WARM | 1 | 394 B | 1.0 | 1.0 | 1 |
| `replay_session` | WARM | 201 | 22.1 KB | 2.0 | 3.0 | 16 |
| `save_memory` | WARM (INSERT) | 1 | 1 B | 3.0 | 5.0 | 1 |
| `graph_traverse` | GRAPH | 38 | 1.1 KB | 3.0 | 4.0 | 8 |

## Session totals (excludes INSERT)

- **Total rows read** across a 7-tool read session: **897**
- **Total bytes read**: **97.3 KB**
- **Sum of p50 tool-call durations**: **8.0 ms**

## Diff vs baseline

| Tool | rows now | rows base | Δ rows | bytes now | bytes base | Δ bytes |
|---|---:|---:|---:|---:|---:|---:|
| `T1_scan_live_stream` | 200 | 200 | +0 | 18,455 | 18,455 | +0 |
| `T2_open_investigation` | 75 | 181 | -106 | 8,637 | 20,451 | -11,814 |
| `T3_recall_memory` | 185 | 185 | +0 | 23,108 | 23,108 | +0 |
| `T4_semantic_search` | 197 | 303 | -106 | 25,241 | 31,407 | -6,166 |
| `T5_fetch_record` | 1 | 1 | +0 | 394 | 394 | +0 |
| `T6_replay_session` | 201 | 303 | -102 | 22,660 | 27,065 | -4,405 |
| `T7_save_memory` | 1 | 1 | +0 | 1 | 1 | +0 |
| `T8_graph_traverse` | 38 | 38 | +0 | 1,096 | 1,096 | +0 |

