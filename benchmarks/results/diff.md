# Benchmark run — 2026-04-27T05:16:05Z

- ClickHouse: `26.3.9.8` at `http://localhost:18123` (db=`enterprise_memory`)
- Iterations: 50 (warmup 5)
- Total wall time: 18.78s

## Seed size at run time

| Table | Rows |
|---|---|
| `agent_memory_long` | 201 |
| `obs_events_stream` | 200 |
| `agent_memory_hot` | 185 |
| `obs_incident_workspace` | 181 |
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
| `open_investigation` | HOT | 181 | 19.1 KB | 0.0 | 1.0 | 181 |
| `recall_memory` | HOT | 185 | 22.6 KB | 0.0 | 1.0 | 9 |
| `semantic_search` | WARM | 199 | 24.9 KB | 2.0 | 4.0 | 5 |
| `fetch_record` | WARM | 1 | 394 B | 1.0 | 1.0 | 1 |
| `replay_session` | WARM | 181 | 19.6 KB | 1.0 | 1.0 | 14 |
| `save_memory` | WARM (INSERT) | 1 | 1 B | 2.0 | 4.0 | 1 |
| `graph_traverse` | GRAPH | 38 | 1.1 KB | 2.0 | 4.0 | 8 |

## Session totals (excludes INSERT)

- **Total rows read** across a 7-tool read session: **985**
- **Total bytes read**: **105.6 KB**
- **Sum of p50 tool-call durations**: **6.0 ms**

## Diff vs baseline

| Tool | rows now | rows base | Δ rows | bytes now | bytes base | Δ bytes |
|---|---:|---:|---:|---:|---:|---:|
| `T1_scan_live_stream` | 200 | 200 | +0 | 18,455 | 18,455 | +0 |
| `T2_open_investigation` | 181 | 181 | +0 | 19,546 | 20,451 | -905 |
| `T3_recall_memory` | 185 | 185 | +0 | 23,108 | 23,108 | +0 |
| `T4_semantic_search` | 199 | 303 | -104 | 25,479 | 31,407 | -5,928 |
| `T5_fetch_record` | 1 | 1 | +0 | 394 | 394 | +0 |
| `T6_replay_session` | 181 | 303 | -122 | 20,046 | 27,065 | -7,019 |
| `T7_save_memory` | 1 | 1 | +0 | 1 | 1 | +0 |
| `T8_graph_traverse` | 38 | 38 | +0 | 1,096 | 1,096 | +0 |

