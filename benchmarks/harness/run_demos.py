#!/usr/bin/env python3
"""
Demo scenario runner.

Executes the 4 demo scenarios from docs/demo-script.md against the live
ClickHouse cluster using the EXACT SQL templates the MCP server ships
(imported from cookbooks.mcp_server.queries). That guarantees the
measured envelope matches what the agent sees in production.

For each scenario we capture:
  - read_rows       (deterministic across runs)
  - read_bytes      (deterministic)
  - query_duration_ms  (p50, p95 over N iterations)
  - result_rows

Writes:
  benchmarks/results/demo_scenarios.json
  benchmarks/results/demo_scenarios.md
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import re
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
PROJECT = BENCH.parent
RESULTS = BENCH / "results"
RESULTS.mkdir(exist_ok=True, parents=True)

# Import the live MCP SQL templates — guarantees the runner measures
# what the agent actually executes in production.
sys.path.insert(0, str(PROJECT / "cookbooks"))
from mcp_server.queries import (  # type: ignore  # noqa: E402
    HOT_SCAN_SQL,
    WARM_VECTOR_SQL,
    WARM_LOOKUP_SQL,
    GRAPH_TRAVERSE_SQL,
)

CH_HTTP = os.environ.get("CH_HTTP", "http://localhost:18123")
CH_USER = os.environ.get("CH_USER", "default")
CH_PASS = os.environ.get("CH_PASS", "clickhouse")
CH_DB   = os.environ.get("CH_DB",   "enterprise_memory")

CANONICAL_SERVICE = "svc-orders"
CANONICAL_USER    = "u-maruthi"
CANONICAL_DOMAIN  = "observability"
ITERATIONS        = int(os.environ.get("DEMO_ITERATIONS", "25"))
WARMUP            = int(os.environ.get("DEMO_WARMUP", "3"))


# ---------------------------------------------------------------------------
# ClickHouse helpers
# ---------------------------------------------------------------------------

def ch(sql: str, *, params: dict[str, str] | None = None,
       query_id: str | None = None, fmt: str = "JSONEachRow") -> str:
    qs: dict[str, str] = {"database": CH_DB}
    if query_id:
        qs["query_id"] = query_id
    if params:
        for k, v in params.items():
            qs[f"param_{k}"] = v
    url = f"{CH_HTTP}/?{urlencode(qs)}"
    s = sql.strip().rstrip(";").rstrip()
    head = s[:20].upper()
    is_cmd = head.startswith(("INSERT", "CREATE", "SYSTEM", "TRUNCATE", "DROP", "ALTER", "OPTIMIZE"))
    body = s if (is_cmd or " FORMAT " in s.upper()) else f"{s}\nFORMAT {fmt}"
    req = Request(url, data=body.encode("utf-8"), method="POST")
    req.add_header("Authorization", b"Basic " + base64.b64encode(f"{CH_USER}:{CH_PASS}".encode()))
    req.add_header("Content-Type", "text/plain")
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:400]}") from None


def flush_and_read_log(qid: str) -> dict[str, Any] | None:
    ch("SYSTEM FLUSH LOGS", fmt="TabSeparated")
    sql = (
        "SELECT read_rows, read_bytes, query_duration_ms, result_rows "
        "FROM system.query_log "
        f"WHERE query_id = '{qid}' AND type = 'QueryFinish' "
        "ORDER BY event_time_microseconds DESC LIMIT 1"
    )
    raw = ch(sql, fmt="JSONEachRow").strip()
    return json.loads(raw.splitlines()[0]) if raw else None


def pick_real_embedding() -> str:
    """Pull a real 768-d embedding from obs_historical_incidents (representative query vector)."""
    sql = (
        "SELECT arrayStringConcat(arrayMap(x -> toString(x), embedding), ',') "
        "FROM obs_historical_incidents "
        "WHERE length(embedding) = 768 AND positionCaseInsensitive(title, 'database') > 0 "
        "ORDER BY ts ASC LIMIT 1"
    )
    csv = ch(sql, fmt="TabSeparated").strip()
    if not csv:
        # fall back to any embedding
        sql = "SELECT arrayStringConcat(arrayMap(x -> toString(x), embedding), ',') FROM obs_historical_incidents WHERE length(embedding) = 768 LIMIT 1"
        csv = ch(sql, fmt="TabSeparated").strip()
    return "[" + csv + "]"


def pick_canonical_incident_id() -> str:
    sql = (
        "SELECT toString(incident_id) FROM obs_historical_incidents "
        "WHERE positionCaseInsensitive(title, 'payment') > 0 "
        "ORDER BY ts ASC LIMIT 1"
    )
    return ch(sql, fmt="TabSeparated").strip() or ch(
        "SELECT toString(incident_id) FROM obs_historical_incidents ORDER BY ts ASC LIMIT 1",
        fmt="TabSeparated",
    ).strip()


def resolve_mcp_template(template: str, embedding_literal: str) -> str:
    """Mirror what the MCP server does: replace the {emb} placeholder (Array literal)
    inline before sending, because ClickHouse URL params don't handle Array literals."""
    return template.replace("{emb}", embedding_literal)


# ---------------------------------------------------------------------------
# Build scenarios from LIVE MCP templates
# ---------------------------------------------------------------------------

def build_scenarios(query_vec_literal: str, incident_id: str):
    hot_sql    = HOT_SCAN_SQL[CANONICAL_DOMAIN]
    warm_sql   = resolve_mcp_template(WARM_VECTOR_SQL[CANONICAL_DOMAIN], query_vec_literal)
    lookup_sql = WARM_LOOKUP_SQL[(CANONICAL_DOMAIN, "runbook")]
    graph_sql  = GRAPH_TRAVERSE_SQL[CANONICAL_DOMAIN]

    return [
        # ---------------- DEMO 1 : HOT alone ----------------
        {
            "demo": 1,
            "demo_name": "HOT alone — what just happened?",
            "tier": "HOT",
            "tool": "search_events",
            "user_question": f'"What is happening on {CANONICAL_SERVICE} right now?"',
            "sql": hot_sql,
            "params": {"service": CANONICAL_SERVICE, "minutes": "60", "limit": "20"},
            "feature": "Memory engine · WHERE + ORDER BY ts DESC · time window + severity filter",
            "proves": "Single-digit ms signals from a SQL surface. Same engine as warm + graph.",
            "caveat": "Redis is faster at this in isolation (sub-ms). CH wins only when the hot tier needs to compose with the others.",
            "source": "cookbooks/mcp_server/queries.py :: HOT_SCAN_SQL['observability']",
        },

        # ---------------- DEMO 2 : WARM alone ----------------
        {
            "demo": 2,
            "demo_name": "WARM alone — have we seen this before?",
            "tier": "WARM",
            "tool": "semantic_search",
            "user_question": f'"Find past incidents that look like a database connection timeout on {CANONICAL_SERVICE}."',
            "sql": warm_sql,
            "params": {"k": "5", "days": "180"},
            "feature": "MergeTree + HNSW vector_similarity('hnsw','cosineDistance',768) inside the table; WHERE ts >= now() - 180 DAY prunes whole monthly partitions before HNSW runs",
            "proves": "Filter-first retrieval. HNSW rank on the surviving set.",
            "caveat": "pgvector / Qdrant do this too. CH wins when filter volumes + data are heavy.",
            "source": "cookbooks/mcp_server/queries.py :: WARM_VECTOR_SQL['observability']",
        },

        # ---------------- DEMO 3 : GRAPH alone ----------------
        {
            "demo": 3,
            "demo_name": "GRAPH alone — what breaks if this fails?",
            "tier": "GRAPH",
            "tool": "find_related_entities",
            "user_question": f'"What services depend on {CANONICAL_SERVICE}?"',
            "sql": graph_sql,
            "params": {"entity": CANONICAL_SERVICE, "max_hops": "2"},
            "feature": "Two-hop upstream dependency walk · SQL JOIN + UNION ALL",
            "proves": "Graph walks don't need a separate graph DB for 2-hop blast radius.",
            "caveat": "Neo4j / Memgraph still win at hard graph algorithms.",
            "source": "cookbooks/mcp_server/queries.py :: GRAPH_TRAVERSE_SQL['observability']",
        },

        # ---------------- DEMO 4 : MIXED (4 sub-calls) ----------------
        {
            "demo": 4, "demo_name": "MIXED — walk me through it", "tier": "ALL",
            "tool": "search_events", "step": "1/4", "sub_intent": "live errors on the failing service",
            "user_question": f'"{CANONICAL_SERVICE} is failing, walk me through it."',
            "sql": hot_sql,
            "params": {"service": CANONICAL_SERVICE, "minutes": "60", "limit": "10"},
            "source": "HOT_SCAN_SQL['observability']",
        },
        {
            "demo": 4, "demo_name": "MIXED — walk me through it", "tier": "ALL",
            "tool": "semantic_search", "step": "2/4", "sub_intent": "find similar past incident",
            "sql": warm_sql,
            "params": {"k": "3", "days": "180"},
            "source": "WARM_VECTOR_SQL['observability']",
        },
        {
            "demo": 4, "demo_name": "MIXED — walk me through it", "tier": "ALL",
            "tool": "get_record", "step": "3/4", "sub_intent": "hydrate top match (runbook + resolution)",
            "sql": lookup_sql,
            "params": {"identifier": incident_id},
            "source": "WARM_LOOKUP_SQL[('observability','runbook')]",
        },
        {
            "demo": 4, "demo_name": "MIXED — walk me through it", "tier": "ALL",
            "tool": "find_related_entities", "step": "4/4", "sub_intent": "2-hop blast radius downstream",
            "sql": graph_sql,
            "params": {"entity": CANONICAL_SERVICE, "max_hops": "2"},
            "source": "GRAPH_TRAVERSE_SQL['observability']",
        },
    ]


def run_scenario(s: dict[str, Any]) -> dict[str, Any]:
    sql = s["sql"].strip()
    params = s.get("params", {})

    for _ in range(WARMUP):
        ch(sql, params=params, query_id=f"demo-warm-{uuid.uuid4()}", fmt="Null")

    rows_list: list[int] = []
    bytes_list: list[int] = []
    dur_list: list[float] = []
    result_rows_list: list[int] = []

    for _ in range(ITERATIONS):
        qid = f"demo-{s['tool']}-{uuid.uuid4()}"
        ch(sql, params=params, query_id=qid, fmt="Null")
        row = None
        for _ in range(5):
            row = flush_and_read_log(qid)
            if row is not None:
                break
            time.sleep(0.04)
        if row is None:
            continue
        rows_list.append(int(row["read_rows"]))
        bytes_list.append(int(row["read_bytes"]))
        dur_list.append(float(row["query_duration_ms"]))
        result_rows_list.append(int(row["result_rows"]))

    def pct(xs: list[float], p: float) -> float:
        if not xs:
            return 0.0
        xs_sorted = sorted(xs)
        k = max(0, min(len(xs_sorted) - 1, int(round((p / 100.0) * (len(xs_sorted) - 1)))))
        return xs_sorted[k]

    # strip the giant embedding literal if present for readable JSON
    sql_for_report = re.sub(r"\[[-0-9.,e\s]{200,}\]", "[…768-d query vector…]", sql)

    return {
        **{k: v for k, v in s.items() if k not in ("sql",)},
        "sql_rendered": sql_for_report,
        "iterations": len(dur_list),
        "read_rows_p50": int(statistics.median(rows_list)) if rows_list else 0,
        "read_bytes_p50": int(statistics.median(bytes_list)) if bytes_list else 0,
        "query_duration_ms_p50": round(statistics.median(dur_list), 2) if dur_list else 0.0,
        "query_duration_ms_p95": round(pct(dur_list, 95), 2) if dur_list else 0.0,
        "result_rows_p50": int(statistics.median(result_rows_list)) if result_rows_list else 0,
    }


def fmt_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b/1024:.1f} KB"
    return f"{b/1024/1024:.2f} MB"


def main() -> int:
    print(f">>> ClickHouse: {CH_HTTP} db={CH_DB}")
    ver = ch("SELECT version()", fmt="TabSeparated").strip()
    print(f">>> Version: {ver}")

    qvec = pick_real_embedding()
    incident_id = pick_canonical_incident_id()
    print(f">>> canonical service:   {CANONICAL_SERVICE}")
    print(f">>> canonical user:      {CANONICAL_USER}")
    print(f">>> canonical incident:  {incident_id}")
    print(f">>> query vector:        real 768-d embedding from obs_historical_incidents")
    print(f">>> SQL source:          cookbooks/mcp_server/queries.py (LIVE TEMPLATES)")

    scenarios = build_scenarios(qvec, incident_id)
    ran_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    t0 = time.time()

    print(f"\n>>> Running {len(scenarios)} scenario-steps × {ITERATIONS} iterations\n")
    results = []
    for s in scenarios:
        label = s["tool"] if s["demo"] != 4 else f"{s['tool']} ({s['step']})"
        print(f"  Demo {s['demo']}  {label:42s} ... ", end="", flush=True)
        try:
            r = run_scenario(s)
            results.append(r)
            print(f"rows_p50={r['read_rows_p50']:<6} "
                  f"bytes_p50={fmt_bytes(r['read_bytes_p50']):<10} "
                  f"dur_p50={r['query_duration_ms_p50']:<5}ms  "
                  f"result={r['result_rows_p50']}")
        except Exception as e:
            print(f"FAILED: {e}")
            results.append({**{k: v for k, v in s.items() if k != "sql"}, "error": str(e)})

    payload = {
        "ran_at": ran_at, "ch_version": ver, "ch_http": CH_HTTP, "database": CH_DB,
        "canonical_service": CANONICAL_SERVICE, "canonical_user": CANONICAL_USER,
        "canonical_domain": CANONICAL_DOMAIN, "canonical_incident_id": incident_id,
        "iterations": ITERATIONS, "warmup": WARMUP,
        "sql_source": "cookbooks/mcp_server/queries.py (live MCP templates, same as production)",
        "duration_s": round(time.time() - t0, 2),
        "scenarios": results,
    }
    json_path = RESULTS / "demo_scenarios.json"
    json_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\n>>> wrote {json_path}")

    # Markdown
    md = [f"# Demo scenarios — measured\n"]
    md.append(f"- **Ran at:** {ran_at}")
    md.append(f"- **ClickHouse:** `{ver}` at `{CH_HTTP}`")
    md.append(f"- **Iterations per scenario:** {ITERATIONS} (warmup {WARMUP})")
    md.append(f"- **Canonical anchor:** `{CANONICAL_SERVICE}` · user `{CANONICAL_USER}` · incident `{incident_id}`")
    md.append(f"- **SQL source of truth:** `cookbooks/mcp_server/queries.py` — the **live** MCP templates.\n")

    by_demo: dict[int, list[dict]] = {}
    for r in results:
        by_demo.setdefault(r["demo"], []).append(r)

    for d in sorted(by_demo.keys()):
        first = by_demo[d][0]
        md.append(f"## Demo {d}: {first['demo_name']}\n")
        md.append(f"**User:** {first['user_question']}\n")
        md.append(f"**SQL origin:** `{first.get('source','')}`\n")
        if d != 4:
            md.append(f"| Tool | Tier | Rows (p50) | Bytes (p50) | Dur p50 | Dur p95 | Result rows |")
            md.append(f"|---|---|---:|---:|---:|---:|---:|")
            md.append(f"| `{first['tool']}` | {first['tier']} | "
                      f"{first.get('read_rows_p50',0):,} | "
                      f"{fmt_bytes(first.get('read_bytes_p50',0))} | "
                      f"{first.get('query_duration_ms_p50',0)} ms | "
                      f"{first.get('query_duration_ms_p95',0)} ms | "
                      f"{first.get('result_rows_p50',0)} |")
            md.append(f"\n**Feature exercised:** {first.get('feature','')}  ")
            md.append(f"**What this proves:** {first.get('proves','')}  ")
            md.append(f"**Honest caveat:** {first.get('caveat','')}\n")
        else:
            md.append("| Step | Tool | Intent | Rows (p50) | Bytes (p50) | Dur p50 |")
            md.append("|---|---|---|---:|---:|---:|")
            total_rows = total_bytes = 0
            total_dur = 0.0
            for r in by_demo[d]:
                total_rows += r.get("read_rows_p50", 0)
                total_bytes += r.get("read_bytes_p50", 0)
                total_dur += r.get("query_duration_ms_p50", 0.0)
                md.append(f"| {r.get('step','')} | `{r['tool']}` | "
                          f"{r.get('sub_intent','')} | "
                          f"{r.get('read_rows_p50',0):,} | "
                          f"{fmt_bytes(r.get('read_bytes_p50',0))} | "
                          f"{r.get('query_duration_ms_p50',0)} ms |")
            md.append(f"\n**Session totals:** **{total_rows:,} rows** · "
                      f"**{fmt_bytes(total_bytes)}** · "
                      f"**{round(total_dur,2)} ms** sum of p50 latencies across 4 tool calls.\n")

    (RESULTS / "demo_scenarios.md").write_text("\n".join(md) + "\n")
    print(f">>> wrote {RESULTS / 'demo_scenarios.md'}")
    print(f">>> total run time: {payload['duration_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
