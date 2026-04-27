#!/usr/bin/env python3
"""
Benchmark harness for the enterprise agent memory stack.

Runs every tool query in queries/ against the ClickHouse HTTP endpoint,
captures read_rows / read_bytes / query_duration_ms straight from
system.query_log using a unique query_id per iteration, and writes
results/latest.json.

We do NOT measure the LLM; this isolates the storage path so results
are reproducible regardless of model or agent framework.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import sys
import time
import uuid
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

HERE = pathlib.Path(__file__).resolve().parent
BENCH = HERE.parent
QUERY_DIR = BENCH / "queries"


def env(key: str, default: str) -> str:
    return os.environ.get(key, default)


CH_HTTP = env("CH_HTTP", "http://localhost:18123")
CH_USER = env("CH_USER", "default")
CH_PASS = env("CH_PASS", "clickhouse")
CH_DB = env("CH_DB", "enterprise_memory")


def ch_query(sql: str, *, settings: dict[str, Any] | None = None,
             params: dict[str, str] | None = None,
             query_id: str | None = None,
             fmt: str = "JSONEachRow") -> str:
    """POST the query, return the raw response body."""
    qs: dict[str, str] = {"database": CH_DB}
    if query_id:
        qs["query_id"] = query_id
    if settings:
        for k, v in settings.items():
            qs[k] = str(v)
    if params:
        for k, v in params.items():
            qs[f"param_{k}"] = v

    url = f"{CH_HTTP}/?{urlencode(qs)}"
    s = sql.strip().rstrip(";").rstrip()
    upper_head = s[:20].upper()
    is_dml = upper_head.startswith(("INSERT", "CREATE", "SYSTEM", "TRUNCATE", "DROP", "ALTER"))
    if is_dml or " FORMAT " in s.upper() or s.upper().endswith(("WITH CTE", "SETTINGS")):
        body = s.encode("utf-8")
    else:
        body = f"{s}\nFORMAT {fmt}".encode("utf-8")

    auth = f"{CH_USER}:{CH_PASS}".encode("utf-8")
    import base64
    req = Request(url, data=body, method="POST")
    req.add_header("Authorization", b"Basic " + base64.b64encode(auth))
    req.add_header("Content-Type", "text/plain")

    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ClickHouse HTTP {e.code}: {detail[:500]}") from None


def ch_flush_logs() -> None:
    ch_query("SYSTEM FLUSH LOGS", fmt="TabSeparated")


def ch_fetch_query_log(query_id: str) -> dict[str, Any] | None:
    """Pull the completed row for this query_id from system.query_log."""
    sql = (
        "SELECT read_rows, read_bytes, query_duration_ms, "
        "result_rows, result_bytes, memory_usage, "
        "ProfileEvents['SelectedParts'] AS selected_parts, "
        "ProfileEvents['SelectedMarks'] AS selected_marks, "
        "ProfileEvents['UserTimeMicroseconds'] AS user_us "
        "FROM system.query_log "
        f"WHERE query_id = '{query_id}' AND type = 'QueryFinish' "
        "ORDER BY event_time_microseconds DESC LIMIT 1"
    )
    raw = ch_query(sql, fmt="JSONEachRow").strip()
    if not raw:
        return None
    return json.loads(raw.splitlines()[0])


def pick_params() -> dict[str, dict[str, str]]:
    """
    Pull real parameter values from the actual cluster so every query targets
    real data. Deterministic: picks the row with the most content for each
    table so reruns pin to the same keys unless the data changes.
    """
    # scan_live_stream: most-hit service in obs_events_stream
    svc = ch_query(
        "SELECT service FROM obs_events_stream GROUP BY service ORDER BY count() DESC LIMIT 1",
        fmt="TabSeparated",
    ).strip()

    # open_investigation: workspace with the most rows
    inc_ws = ch_query(
        "SELECT incident_id FROM obs_incident_workspace "
        "GROUP BY incident_id ORDER BY count() DESC LIMIT 1",
        fmt="TabSeparated",
    ).strip()

    # recall_memory: agent_memory_hot session with most turns (exclude bench writes)
    hot_sess = ch_query(
        "SELECT session_id FROM agent_memory_hot "
        "WHERE session_id NOT LIKE 'bench%' "
        "GROUP BY session_id ORDER BY count() DESC LIMIT 1",
        fmt="TabSeparated",
    ).strip()

    # semantic_search: pick a real user + pull the middle row's embedding as the query vec
    # Exclude bench writes so the query vector and filter are stable across runs.
    row_raw = ch_query(
        "SELECT user_id, arrayStringConcat(arrayMap(x -> toString(x), content_embedding), ',') AS vec "
        "FROM agent_memory_long "
        "WHERE length(content_embedding) = 768 AND user_id NOT LIKE 'bench%' "
        "ORDER BY ts ASC LIMIT 1",
        fmt="TabSeparated",
    ).strip()
    user_id, vec_csv = row_raw.split("\t", 1)
    query_vec_literal = "[" + vec_csv + "]"

    # fetch_record: pick a real incident_id
    inc_uuid = ch_query(
        "SELECT incident_id FROM obs_historical_incidents ORDER BY ts ASC LIMIT 1",
        fmt="TabSeparated",
    ).strip()

    # replay_session: long-memory session with most turns (exclude bench writes)
    long_sess = ch_query(
        "SELECT session_id FROM agent_memory_long "
        "WHERE session_id NOT LIKE 'bench%' "
        "GROUP BY session_id ORDER BY count() DESC LIMIT 1",
        fmt="TabSeparated",
    ).strip()

    # graph_traverse: service with outbound edges
    graph_svc = ch_query(
        "SELECT from_service FROM obs_dependencies "
        "GROUP BY from_service ORDER BY count() DESC LIMIT 1",
        fmt="TabSeparated",
    ).strip()

    # save_memory params (fixed, small, deterministic)
    save_embedding = "[" + ",".join(["0.0"] * 768) + "]"

    return {
        "T1_scan_live_stream": {"service": svc},
        "T2_open_investigation": {"incident_id": inc_ws},
        "T3_recall_memory": {"session_id": hot_sess},
        "T4_semantic_search": {"user_id": user_id, "query_vec": query_vec_literal},
        "T5_fetch_record": {"incident_id": inc_uuid},
        "T6_replay_session": {"session_id": long_sess},
        "T7_save_memory": {
            "user_id": "bench-user",
            "agent_id": "bench",
            "session_id": "bench-session",
            "turn_id": "0",
            "content": "benchmark write",
            "embedding": save_embedding,
        },
        "T8_graph_traverse": {"service": graph_svc},
    }


def run_query_file(qfile: pathlib.Path, params: dict[str, str],
                   iterations: int, warmup: int) -> dict[str, Any]:
    sql = qfile.read_text().strip()
    # For parameterised queries, CH needs `param_` URL args, already handled by ch_query.
    # But we need to inline the embedding literal for T4 because CH params don't support
    # Array(Float32) literals cleanly via `param_` (they parse as strings).
    # Substitute {query_vec:Array(Float32)} with the actual literal.
    inline_params = {}
    http_params = {}
    for k, v in params.items():
        placeholder = "{" + k + ":Array(Float32)}"
        if placeholder in sql:
            sql = sql.replace(placeholder, v)
        else:
            http_params[k] = v

    # Warmup
    for _ in range(warmup):
        qid = f"bench-warm-{uuid.uuid4()}"
        ch_query(sql, params=http_params, query_id=qid, fmt="Null")

    rows_each: list[int] = []
    bytes_each: list[int] = []
    durations_ms: list[float] = []
    result_rows_each: list[int] = []
    wallclock_ms: list[float] = []

    for i in range(iterations):
        qid = f"bench-{qfile.stem}-{uuid.uuid4()}"
        t0 = time.perf_counter()
        ch_query(sql, params=http_params, query_id=qid, fmt="Null")
        wall = (time.perf_counter() - t0) * 1000.0
        wallclock_ms.append(wall)

        # Flush + read the log entry for this query_id
        ch_flush_logs()
        row = None
        # small retry in case the log row hasn't landed
        for _ in range(5):
            row = ch_fetch_query_log(qid)
            if row is not None:
                break
            time.sleep(0.05)
        if row is None:
            continue
        rows_each.append(int(row["read_rows"]))
        bytes_each.append(int(row["read_bytes"]))
        durations_ms.append(float(row["query_duration_ms"]))
        result_rows_each.append(int(row["result_rows"]))

    def pct(xs: list[float], p: float) -> float:
        if not xs:
            return 0.0
        xs_sorted = sorted(xs)
        k = max(0, min(len(xs_sorted) - 1, int(round((p / 100.0) * (len(xs_sorted) - 1)))))
        return xs_sorted[k]

    return {
        "tool": qfile.stem,
        "iterations": len(durations_ms),
        "read_rows_p50": int(statistics.median(rows_each)) if rows_each else 0,
        "read_rows_unique_values": sorted(set(rows_each)),
        "read_bytes_p50": int(statistics.median(bytes_each)) if bytes_each else 0,
        "read_bytes_unique_values": sorted(set(bytes_each)),
        "query_duration_ms_p50": round(statistics.median(durations_ms), 3) if durations_ms else 0.0,
        "query_duration_ms_p95": round(pct(durations_ms, 95), 3) if durations_ms else 0.0,
        "wallclock_ms_p50": round(statistics.median(wallclock_ms), 3) if wallclock_ms else 0.0,
        "result_rows_p50": int(statistics.median(result_rows_each)) if result_rows_each else 0,
        "params_used": {k: (v[:60] + "…" if len(v) > 60 else v) for k, v in params.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=50)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()

    print(f">>> ClickHouse: {CH_HTTP} db={CH_DB}")
    version = ch_query("SELECT version()", fmt="TabSeparated").strip()
    print(f">>> Version: {version}")

    # Isolate T7 writes in a dedicated sink so reads are reproducible.
    # The sink mirrors agent_memory_long's schema including the HNSW index.
    ch_query("DROP TABLE IF EXISTS benchmark_writes", fmt="TabSeparated")
    ch_query(
        "CREATE TABLE benchmark_writes AS agent_memory_long",
        fmt="TabSeparated",
    )
    print(">>> benchmark_writes sink ready")

    # Verify seed
    counts = ch_query(
        "SELECT name, total_rows FROM system.tables "
        "WHERE database = currentDatabase() "
        "AND engine IN ('MergeTree','Memory') "
        "ORDER BY total_rows DESC FORMAT TabSeparated",
        fmt="TabSeparated",
    )
    row_count_table = {}
    for line in counts.strip().splitlines():
        name, rows = line.split("\t")
        row_count_table[name] = int(rows or 0)

    params = pick_params()
    print(">>> Params picked:", json.dumps(params, indent=2)[:500] + "…")

    qfiles = sorted(QUERY_DIR.glob("T*.sql"))
    if not qfiles:
        print("no query files under queries/", file=sys.stderr)
        return 1

    run_started = time.time()
    results: list[dict[str, Any]] = []
    for qf in qfiles:
        tool = qf.stem
        tool_params = params.get(tool, {})
        print(f"  running {tool:30s} ...", end=" ", flush=True)
        try:
            r = run_query_file(qf, tool_params, args.iterations, args.warmup)
            results.append(r)
            print(f"rows_p50={r['read_rows_p50']:<8} bytes_p50={r['read_bytes_p50']:<10} "
                  f"dur_p50={r['query_duration_ms_p50']}ms dur_p95={r['query_duration_ms_p95']}ms")
        except Exception as e:
            print(f"FAILED: {e}")
            results.append({"tool": tool, "error": str(e)})

    payload = {
        "version": version,
        "ch_http": CH_HTTP,
        "database": CH_DB,
        "iterations": args.iterations,
        "warmup": args.warmup,
        "seed_row_counts": row_count_table,
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(run_started)),
        "duration_s": round(time.time() - run_started, 2),
        "tools": results,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f">>> wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
