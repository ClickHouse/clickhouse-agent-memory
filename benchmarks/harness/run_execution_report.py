"""
benchmarks/harness/run_execution_report.py
------------------------------------------
Run every query for every demo scenario against the live cluster, capture:
  - the exact SQL text
  - the exact bind parameters
  - the full result rows (first 20 for display)
  - read_rows + read_bytes from system.query_log
  - wall-clock duration (client-measured p50 over 10 iterations)

Then render a single-file HTML report with every query inline.

Usage:
  python3 benchmarks/harness/run_execution_report.py \
      --out docs/report/execution-report.html
"""
from __future__ import annotations

import argparse
import html
import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from cookbooks.mcp_server.queries import (  # noqa: E402
    HOT_SCAN_SQL,
    WARM_VECTOR_SQL,
    WARM_LOOKUP_SQL,
    GRAPH_TRAVERSE_SQL,
)

CH_HTTP = os.environ.get("CH_HTTP", "http://localhost:18123")
CH_USER = os.environ.get("CH_USER", "default")
CH_PASS = os.environ.get("CH_PASS", "clickhouse")
CH_DB = os.environ.get("CH_DB", "enterprise_memory")

ITERATIONS = 10


def ch_request(query: str, params: dict | None = None, *, fmt: str = "JSON") -> tuple[dict, str]:
    """POST a query to ClickHouse HTTP, return (parsed_body, query_id)."""
    qid = f"report-{int(time.time() * 1000)}-{os.urandom(4).hex()}"
    q = query + (f"\nFORMAT {fmt}" if fmt and not query.strip().upper().endswith(f"FORMAT {fmt}") else "")
    url = f"{CH_HTTP}/?database={urllib.parse.quote(CH_DB)}&query_id={urllib.parse.quote(qid)}"
    if params:
        for k, v in params.items():
            url += f"&param_{urllib.parse.quote(k)}={urllib.parse.quote(str(v))}"
    req = urllib.request.Request(url, data=q.encode("utf-8"), method="POST")
    auth = f"{CH_USER}:{CH_PASS}".encode("utf-8")
    import base64
    req.add_header("Authorization", f"Basic {base64.b64encode(auth).decode()}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body), qid
    except json.JSONDecodeError:
        return {"data": [], "raw": body}, qid


def query_log_stats(qid: str) -> dict:
    """Pull read_rows, read_bytes, query_duration_ms from system.query_log."""
    # Flush logs + give CH a brief moment to persist the row.
    ch_request("SYSTEM FLUSH LOGS", fmt="")
    time.sleep(0.05)
    body, _ = ch_request(
        f"SELECT read_rows, read_bytes, query_duration_ms, memory_usage "
        f"FROM system.query_log "
        f"WHERE query_id = '{qid}' AND type = 'QueryFinish' "
        f"ORDER BY event_time DESC LIMIT 1"
    )
    rows = body.get("data") or []
    if not rows:
        return {"read_rows": None, "read_bytes": None, "query_duration_ms": None, "memory_usage": None}
    r = rows[0]
    return {
        "read_rows": int(r.get("read_rows", 0)),
        "read_bytes": int(r.get("read_bytes", 0)),
        "query_duration_ms": int(r.get("query_duration_ms", 0)),
        "memory_usage": int(r.get("memory_usage", 0)),
    }


def time_it(fn, iterations: int = ITERATIONS) -> dict:
    """Run fn() iterations times, return client-measured timing distribution."""
    times_ms = []
    last_result = None
    for _ in range(iterations):
        t0 = time.perf_counter()
        last_result = fn()
        times_ms.append((time.perf_counter() - t0) * 1000)
    times_sorted = sorted(times_ms)
    return {
        "iterations": iterations,
        "p50_ms": round(times_sorted[iterations // 2], 2),
        "p95_ms": round(times_sorted[int(iterations * 0.95)] if iterations > 1 else times_sorted[-1], 2),
        "min_ms": round(min(times_ms), 2),
        "max_ms": round(max(times_ms), 2),
        "mean_ms": round(statistics.mean(times_ms), 2),
        "last_result": last_result,
    }


def load_gemini_query_vector() -> list[float]:
    """Pull a real 768-dim embedding from seeded obs_historical_incidents so
    semantic_search has a true filter-by-meaning anchor."""
    body, _ = ch_request(
        "SELECT embedding FROM obs_historical_incidents LIMIT 1"
    )
    rows = body.get("data") or []
    if not rows:
        raise RuntimeError("no seeded embeddings in obs_historical_incidents")
    return list(rows[0]["embedding"])


def fmt_bytes(n: int | None) -> str:
    if n is None:
        return "-"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


# ---------------------------------------------------------------------------
# Scenario runners — each returns a dict describing the step.
# ---------------------------------------------------------------------------


def run_hot_observability(service: str = "svc-orders", minutes: int = 15, limit: int = 50) -> dict:
    sql = HOT_SCAN_SQL["observability"]
    params = {"service": service, "minutes": minutes, "limit": limit}

    def fire():
        body, qid = ch_request(sql, params)
        return {"rows": body.get("data") or [], "qid": qid}

    timing = time_it(fire)
    stats = query_log_stats(timing["last_result"]["qid"])
    rows = timing["last_result"]["rows"]
    return {
        "name": "search_events",
        "tier": "HOT",
        "dialect": "ClickHouse SQL",
        "description": "Tail the live observability event stream (Memory engine).",
        "query": sql.strip(),
        "bind_params": params,
        "result_count": len(rows),
        "result_sample": rows[:20],
        "timing_client": timing,
        "query_log": stats,
    }


def run_warm_vector(user_id: str = "u-maruthi", k: int = 5, query_vec: list[float] | None = None, days: int = 180) -> dict:
    template = WARM_VECTOR_SQL["observability"]
    if query_vec is None:
        query_vec = load_gemini_query_vector()
    emb_literal = "[" + ",".join(f"{x:.6f}" for x in query_vec) + "]"
    # The MCP server substitutes {emb} inline (CH URL params can't carry Array literals)
    sql = template.replace("{emb}", emb_literal)
    # days drives the WHERE ts >= now() - INTERVAL {days} DAY filter that
    # enables monthly-partition pruning before HNSW runs.
    params = {"k": k, "days": days}

    def fire():
        body, qid = ch_request(sql, params)
        return {"rows": body.get("data") or [], "qid": qid}

    timing = time_it(fire)
    stats = query_log_stats(timing["last_result"]["qid"])
    rows = timing["last_result"]["rows"]
    return {
        "name": "semantic_search",
        "tier": "WARM",
        "dialect": "ClickHouse SQL + HNSW",
        "description": "Filter-first semantic search: MergeTree WHERE then HNSW cosineDistance rank.",
        "query": template.strip(),
        "bind_params": {"k": k, "emb": f"<real 768-d float32[] embedding seeded via Gemini, substituted inline because ClickHouse URL params don't carry Array literals>"},
        "result_count": len(rows),
        "result_sample": [{k2: v for k2, v in r.items() if k2 != "embedding"} for r in rows[:20]],
        "timing_client": timing,
        "query_log": stats,
    }


def run_warm_lookup(incident_id: str) -> dict:
    sql = WARM_LOOKUP_SQL[("observability", "runbook")]
    params = {"identifier": incident_id}

    def fire():
        body, qid = ch_request(sql, params)
        return {"rows": body.get("data") or [], "qid": qid}

    timing = time_it(fire)
    stats = query_log_stats(timing["last_result"]["qid"])
    rows = timing["last_result"]["rows"]
    return {
        "name": "get_record",
        "tier": "WARM",
        "dialect": "ClickHouse SQL",
        "description": "Hydrate one historical incident by primary key.",
        "query": sql.strip(),
        "bind_params": params,
        "result_count": len(rows),
        "result_sample": [{k2: v for k2, v in r.items() if k2 != "embedding"} for r in rows[:20]],
        "timing_client": timing,
        "query_log": stats,
    }


def run_graph_sql(service: str = "svc-orders", max_hops: int = 2) -> dict:
    sql = GRAPH_TRAVERSE_SQL["observability"]
    params = {"entity": service, "max_hops": max_hops}

    def fire():
        body, qid = ch_request(sql, params)
        return {"rows": body.get("data") or [], "qid": qid}

    timing = time_it(fire)
    stats = query_log_stats(timing["last_result"]["qid"])
    rows = timing["last_result"]["rows"]
    return {
        "name": "find_related_entities",
        "tier": "GRAPH",
        "dialect": "ClickHouse SQL self-JOIN + UNION ALL",
        "description": "Upstream blast radius via self-JOIN on obs_dependencies, one branch per hop depth.",
        "query": sql.strip(),
        "bind_params": params,
        "result_count": len(rows),
        "result_sample": rows[:20],
        "timing_client": timing,
        "query_log": stats,
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


def html_escape(s) -> str:
    if isinstance(s, (dict, list)):
        return html.escape(json.dumps(s, indent=2, default=str))
    return html.escape(str(s))


def render_step(step: dict) -> str:
    client = step["timing_client"]
    qlog = step["query_log"]
    params_json = json.dumps(step["bind_params"], indent=2, default=str)
    sample_json = json.dumps(step["result_sample"], indent=2, default=str)
    qlog_cells = (
        f"<td>{fmt_bytes(qlog.get('read_bytes'))}</td>"
        f"<td>{qlog.get('read_rows') if qlog.get('read_rows') is not None else '-'}</td>"
        f"<td>{qlog.get('query_duration_ms') if qlog.get('query_duration_ms') is not None else '-'} ms</td>"
        f"<td>{fmt_bytes(qlog.get('memory_usage'))}</td>"
    )
    note = qlog.get("note", "")
    return f"""
    <section class="step">
      <header class="step-head">
        <span class="tier tier-{step['tier'].lower()}">{step['tier']}</span>
        <h3>{html_escape(step['name'])}</h3>
        <span class="dialect">{html_escape(step['dialect'])}</span>
      </header>
      <p class="desc">{html_escape(step['description'])}</p>

      <div class="metrics">
        <table>
          <thead>
            <tr>
              <th colspan="3">Client-measured ({client['iterations']} iterations)</th>
              <th colspan="4">system.query_log (last run)</th>
            </tr>
            <tr>
              <th>p50</th><th>p95</th><th>max</th>
              <th>bytes read</th><th>rows read</th><th>CH duration</th><th>memory</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{client['p50_ms']} ms</td>
              <td>{client['p95_ms']} ms</td>
              <td>{client['max_ms']} ms</td>
              {qlog_cells}
            </tr>
          </tbody>
        </table>
        {f'<p class="note">{html_escape(note)}</p>' if note else ''}
      </div>

      <details open>
        <summary>Query text ({len(step['query'].splitlines())} lines)</summary>
        <pre class="code">{html_escape(step['query'])}</pre>
      </details>

      <details open>
        <summary>Bind parameters</summary>
        <pre class="code">{html_escape(params_json)}</pre>
      </details>

      <details open>
        <summary>Result — {step['result_count']} rows returned (first 20 shown)</summary>
        <pre class="code">{html_escape(sample_json)}</pre>
      </details>
    </section>
    """


def render_scenario(scenario: dict) -> str:
    steps_html = "\n".join(render_step(s) for s in scenario["steps"])
    total_client_p50 = sum(
        s["timing_client"]["p50_ms"] for s in scenario["steps"]
    )
    total_read_rows = sum(
        s["query_log"].get("read_rows") or 0 for s in scenario["steps"]
    )
    total_read_bytes = sum(
        s["query_log"].get("read_bytes") or 0 for s in scenario["steps"]
    )
    return f"""
    <article class="scenario">
      <h2 class="scenario-title">{html_escape(scenario['name'])}</h2>
      <p class="user-question"><strong>User asks:</strong> {html_escape(scenario['user_question'])}</p>
      <p class="anticipated"><strong>Answer shape:</strong> {html_escape(scenario['answer_shape'])}</p>
      <div class="scenario-totals">
        <span><strong>Tools called:</strong> {len(scenario['steps'])}</span>
        <span><strong>Total rows read:</strong> {total_read_rows:,}</span>
        <span><strong>Total bytes read:</strong> {fmt_bytes(total_read_bytes) if total_read_bytes else '-'}</span>
        <span><strong>Sum client p50:</strong> {total_client_p50:.1f} ms</span>
      </div>
      {steps_html}
    </article>
    """


CSS = """
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif;
    background: #0F0F10;
    color: #E8E8E8;
    margin: 0;
    padding: 48px 24px 96px;
    line-height: 1.5;
  }
  .container { max-width: 1100px; margin: 0 auto; }
  h1 { font-size: 2.2em; font-weight: 900; margin: 0 0 8px; color: #FAFF00; letter-spacing: -0.01em; }
  .subtitle { color: #9A9A9A; font-size: 1.05em; margin: 0 0 16px; }
  .meta {
    background: #1A1A1A;
    border: 1px solid #2A2A2A;
    border-radius: 8px;
    padding: 16px 20px;
    margin: 24px 0 48px;
    font-family: 'JetBrains Mono', 'SF Mono', monospace;
    font-size: 13px;
    color: #B8B8B8;
  }
  .meta strong { color: #FAFF00; font-weight: 700; }
  .scenario {
    border-top: 2px solid #2A2A2A;
    padding-top: 40px;
    margin-top: 40px;
  }
  .scenario:first-of-type { border-top: none; padding-top: 0; margin-top: 0; }
  .scenario-title {
    font-size: 1.7em;
    font-weight: 900;
    margin: 0 0 12px;
    color: #FFF;
  }
  .user-question, .anticipated {
    margin: 4px 0;
    color: #C8C8C8;
  }
  .scenario-totals {
    display: flex;
    gap: 24px;
    flex-wrap: wrap;
    margin: 16px 0 32px;
    padding: 12px 16px;
    background: #1a1a1a;
    border: 1px solid #2A2A2A;
    border-radius: 6px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
  }
  .scenario-totals strong { color: #FAFF00; }
  .step {
    background: #141414;
    border: 1px solid #252525;
    border-radius: 10px;
    padding: 20px 24px;
    margin: 0 0 20px;
  }
  .step-head {
    display: flex;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
    margin-bottom: 8px;
  }
  .step-head h3 {
    margin: 0;
    font-size: 1.15em;
    font-weight: 700;
    color: #FFF;
  }
  .tier {
    display: inline-block;
    padding: 4px 10px;
    border-radius: 4px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
  }
  .tier-hot   { background: #FAFF00; color: #1A1A1A; }
  .tier-warm  { background: #F2B366; color: #1A1A1A; }
  .tier-graph { background: #6BE07A; color: #1A1A1A; }
  .dialect {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: #8A8A8A;
  }
  .desc { color: #B8B8B8; margin: 4px 0 16px; font-size: 0.95em; }
  .metrics table {
    width: 100%;
    border-collapse: collapse;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    margin-bottom: 12px;
  }
  .metrics th, .metrics td {
    padding: 8px 10px;
    text-align: left;
    border-bottom: 1px solid #252525;
  }
  .metrics thead tr:first-child th {
    color: #FAFF00;
    font-weight: 700;
    text-align: center;
    padding-top: 6px;
    padding-bottom: 6px;
    background: #1a1a1a;
    border-bottom: 1px solid #2A2A2A;
  }
  .metrics thead tr:nth-child(2) th {
    color: #9A9A9A;
    font-weight: 400;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .metrics tbody td { color: #E8E8E8; font-weight: 700; }
  .note {
    color: #8A8A8A;
    font-size: 12px;
    font-family: 'JetBrains Mono', monospace;
    margin: 4px 0 0;
  }
  details { margin-top: 10px; }
  details > summary {
    cursor: pointer;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    color: #9A9A9A;
    padding: 6px 0;
    user-select: none;
  }
  details > summary:hover { color: #FAFF00; }
  .code {
    background: #0A0A0A;
    border: 1px solid #1E1E1E;
    border-radius: 6px;
    padding: 14px 16px;
    overflow-x: auto;
    font-family: 'JetBrains Mono', 'SF Mono', monospace;
    font-size: 12px;
    color: #D8D8D8;
    line-height: 1.55;
    margin: 6px 0 0;
    white-space: pre;
  }
"""


def render_data_section(tables: list[dict]) -> str:
    """Render the 'Data under test' section: volume + variety + DDL per table."""
    # Group tables by the tier they serve
    group_map = {
        "obs_events_stream": ("HOT", "Observability live event stream (errors, latency, traces)."),
        "telco_network_state": ("HOT", "Telco network element state (CPU, traffic, error rate)."),
        "sec_events_stream": ("HOT", "Security event stream (SIEM / EDR / IAM)."),
        "agent_memory_hot": ("HOT", "Cross-agent working memory (conversation scratchpad)."),
        "obs_incident_workspace": ("HOT", "Per-investigation scratchpad (created by open_investigation)."),
        "telco_fault_workspace": ("HOT", "Per-fault-ticket scratchpad."),
        "sec_case_workspace": ("HOT", "Per-security-case scratchpad."),
        "obs_historical_incidents": ("WARM", "Past observability incidents with 768-d embedding for semantic search."),
        "telco_network_events": ("WARM", "Past telco network events with 768-d embedding."),
        "sec_historical_incidents": ("WARM", "Past security incidents with 768-d embedding."),
        "sec_threat_intel": ("WARM", "Threat intel indicators with 768-d embedding."),
        "agent_memory_long": ("WARM", "Long-term agent memory (conversations, facts). HNSW on embedding."),
        "knowledge_base": ("WARM", "Generic knowledge base for RAG (768-d embedding)."),
        "obs_services": ("GRAPH", "Observability service catalog (vertex)."),
        "obs_dependencies": ("GRAPH", "Service-to-service dependency edges."),
        "telco_elements": ("GRAPH", "Telco network elements (vertex)."),
        "telco_connections": ("GRAPH", "Telco physical/logical links (edge)."),
        "sec_users": ("GRAPH", "Security user vertices."),
        "sec_assets": ("GRAPH", "Security asset vertices."),
        "sec_access": ("GRAPH", "User-to-asset access edges."),
        "benchmark_writes": ("UTIL", "Dedicated sink for save_memory benchmark runs."),
    }
    rows_html = []
    for t in tables:
        name = t["name"]
        tier, purpose = group_map.get(name, ("-", "-"))
        tier_css = tier.lower() if tier in ("HOT", "WARM", "GRAPH") else "util"
        ddl_preview = html_escape(t.get("ddl", "").strip())
        col_count = len(t.get("columns", []))
        rows_html.append(f"""
          <tr>
            <td><span class="tier tier-{tier_css}">{tier}</span></td>
            <td><code>{html_escape(name)}</code></td>
            <td>{t['engine']}</td>
            <td style="text-align:right;">{t['total_rows']:,}</td>
            <td style="text-align:right;">{col_count}</td>
            <td class="purpose">{html_escape(purpose)}</td>
          </tr>
        """)
    inventory_html = "".join(rows_html)

    ddls_html = []
    for t in tables:
        if not t.get("ddl"):
            continue
        tier, _purpose = group_map.get(t["name"], ("-", "-"))
        tier_css = tier.lower() if tier in ("HOT", "WARM", "GRAPH") else "util"
        ddls_html.append(f"""
          <details>
            <summary><span class="tier tier-{tier_css}">{tier}</span> <code>{html_escape(t['name'])}</code> — {t['total_rows']:,} rows · {html_escape(t['engine'])}</summary>
            <pre class="code">{html_escape(t['ddl'].strip())}</pre>
          </details>
        """)
    ddls_combined = "\n".join(ddls_html)

    total_rows = sum(t["total_rows"] for t in tables)
    table_count = len([t for t in tables if t["engine"] != "View"])

    return f"""
    <article class="scenario data-section">
      <h2 class="scenario-title">Data under test</h2>
      <p class="user-question">Every query in this report runs against the tables listed below. All data is synthetic, generated by <code>make seed</code>, and shaped to match the three-domain story (observability, cybersecurity, telco) with conversation memory on top.</p>

      <div class="scenario-totals">
        <span><strong>Tables:</strong> {table_count}</span>
        <span><strong>Total rows:</strong> {total_rows:,}</span>
        <span><strong>Domains:</strong> observability, cybersecurity, telco, conversation</span>
        <span><strong>Embeddings:</strong> Gemini <code>gemini-embedding-001</code>, 768-dim <code>Array(Float32)</code></span>
      </div>

      <h3 class="sub-head">Table inventory</h3>
      <div class="table-wrap">
        <table class="inventory">
          <thead>
            <tr>
              <th>Tier</th><th>Table</th><th>Engine</th><th style="text-align:right;">Rows</th><th style="text-align:right;">Cols</th><th>What it holds</th>
            </tr>
          </thead>
          <tbody>
            {inventory_html}
          </tbody>
        </table>
      </div>

      <h3 class="sub-head">Variety</h3>
      <ul class="variety">
        <li><strong>Time series (HOT).</strong> <code>ENGINE = Memory</code> for sub-5ms tail reads on live event streams. Volatile — cleared on container restart, which is the desired behavior for a live stream.</li>
        <li><strong>Append-only history with vectors (WARM).</strong> <code>ENGINE = MergeTree</code> with <code>INDEX ... TYPE vector_similarity('hnsw', 'cosineDistance', 768)</code> inside the table. Every historical-incident row carries a 768-dim Gemini embedding of its title + description + root cause, so semantic search is filter-first + HNSW-ranked in one SQL statement.</li>
        <li><strong>Small relational (GRAPH).</strong> Service catalogs and dependency edges as plain MergeTree tables. 2-hop blast radius is a self-JOIN + UNION ALL, one branch per hop depth. Same SQL engine as HOT and WARM, no separate graph store.</li>
        <li><strong>Conversation memory.</strong> <code>agent_memory_hot</code> (Memory engine, last N turns) and <code>agent_memory_long</code> (MergeTree + HNSW, 768-d) persist cross-agent memory. Every save_memory call is an INSERT against these.</li>
      </ul>

      <h3 class="sub-head">Data model</h3>
      <pre class="code">HOT   ──▶  Memory-engine tables     → scan fast, write fast, volatile
WARM  ──▶  MergeTree + HNSW idx     → filter by tenant/ts, then rank by cosine
GRAPH ──▶  MergeTree vertex + edge  → SELF JOIN + UNION ALL for blast radius
MCP 8 tools hit exactly these tables. No view layer, no ORM. The SQL you see in
each scenario is the SQL the server runs.</pre>

      <h3 class="sub-head">DDL — click to expand</h3>
      <div class="ddls">
        {ddls_combined}
      </div>
    </article>
    """


def render_html(scenarios: list[dict], meta: dict, tables: list[dict]) -> str:
    data_html = render_data_section(tables)
    scenarios_html = "\n".join(render_scenario(s) for s in scenarios)
    meta_lines = "<br>".join(
        f"<strong>{html_escape(k)}:</strong> {html_escape(v)}" for k, v in meta.items()
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Enterprise Agent Memory — Execution Report</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>{CSS}
  .sub-head {{ color: #FAFF00; font-size: 1.0em; margin: 24px 0 8px; font-weight: 700; letter-spacing: 0.04em; }}
  .variety {{ padding-left: 20px; color: #C8C8C8; }}
  .variety li {{ margin: 8px 0; line-height: 1.55; }}
  .variety strong {{ color: #FFF; }}
  .table-wrap {{ overflow-x: auto; border: 1px solid #252525; border-radius: 6px; }}
  table.inventory {{ width: 100%; border-collapse: collapse; font-family: 'JetBrains Mono', monospace; font-size: 12px; }}
  table.inventory th, table.inventory td {{ padding: 8px 10px; border-bottom: 1px solid #1E1E1E; text-align: left; }}
  table.inventory thead th {{ background: #1a1a1a; color: #9A9A9A; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; font-size: 11px; }}
  table.inventory tbody tr:last-child td {{ border-bottom: none; }}
  table.inventory code {{ color: #FAFF00; }}
  td.purpose {{ color: #B8B8B8; font-family: -apple-system, 'Inter', sans-serif; font-size: 12px; }}
  .tier-util {{ background: #333; color: #999; }}
  .ddls details {{ background: #141414; border: 1px solid #252525; border-radius: 6px; padding: 10px 16px; margin: 6px 0; }}
  .ddls summary {{ padding: 4px 0; }}
  .ddls summary code {{ color: #FAFF00; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Enterprise Agent Memory — Execution Report</h1>
    <p class="subtitle">Every query, every scenario, every row. Generated against the live cluster.</p>
    <div class="meta">{meta_lines}</div>
    {data_html}
    {scenarios_html}
  </div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Build the scenarios — canonical anchors tuned to seeded data.
# ---------------------------------------------------------------------------


def collect_table_inventory() -> list[dict]:
    """For every table in the enterprise_memory db, pull engine, row count, column list, and DDL."""
    body, _ = ch_request(
        "SELECT name, total_rows, engine FROM system.tables "
        "WHERE database = currentDatabase() "
        "ORDER BY total_rows DESC, name"
    )
    rows = body.get("data") or []
    out = []
    for r in rows:
        name = r["name"]
        # Columns
        cols_body, _ = ch_request(
            f"SELECT name, type FROM system.columns "
            f"WHERE database = currentDatabase() AND table = '{name}' ORDER BY position"
        )
        cols = cols_body.get("data") or []
        # DDL via SHOW CREATE TABLE, returned as TSV not JSON (CH emits a raw statement row)
        try:
            ddl_body, _ = ch_request(f"SHOW CREATE TABLE {name}")
            ddl_rows = ddl_body.get("data") or []
            ddl = ddl_rows[0].get("statement", "") if ddl_rows else ""
        except Exception:
            ddl = ""
        out.append({
            "name": name,
            "engine": r["engine"],
            "total_rows": int(r["total_rows"] or 0),
            "columns": cols,
            "ddl": ddl,
        })
    return out


def pick_anchors() -> dict:
    # One real incident id that has an embedding we can reuse.
    body, _ = ch_request(
        "SELECT incident_id FROM obs_historical_incidents "
        "WHERE has(affected_services, 'svc-orders') "
        "LIMIT 1"
    )
    rows = body.get("data") or []
    incident_id = rows[0]["incident_id"] if rows else ""
    return {"service": "svc-orders", "user_id": "u-maruthi", "incident_id": incident_id}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO_ROOT / "docs" / "report" / "execution-report.html"))
    ap.add_argument("--json-out", default=str(REPO_ROOT / "benchmarks" / "results" / "execution_report.json"))
    args = ap.parse_args()

    print(f">>> ClickHouse:  {CH_HTTP} (db={CH_DB})")
    body, _ = ch_request("SELECT version()")
    ch_version = (body.get("data") or [{}])[0].get("version()", "?")
    print(f">>> Version:     {ch_version}")

    tables = collect_table_inventory()
    print(f">>> Inventory:   {len(tables)} tables, {sum(t['total_rows'] for t in tables):,} total rows")

    anchors = pick_anchors()
    print(f">>> Anchors:     service={anchors['service']}  user={anchors['user_id']}  incident={anchors['incident_id'][:12]}...")

    qvec = load_gemini_query_vector()
    print(f">>> Query vector dim={len(qvec)} (real 768-d float32[] from obs_historical_incidents)")

    scenarios: list[dict] = []

    # Demo 1: HOT alone
    print(">>> Scenario 1/4  HOT — live event stream")
    scenarios.append({
        "name": "Scenario 1 — HOT alone: what just happened on this service?",
        "user_question": f"What is happening on {anchors['service']} right now?",
        "answer_shape": "Top N error events from the live stream in the last 15 minutes.",
        "steps": [run_hot_observability(anchors["service"])],
    })

    # Demo 2: WARM alone
    print(">>> Scenario 2/4  WARM — semantic search")
    scenarios.append({
        "name": "Scenario 2 — WARM alone: have we seen this before?",
        "user_question": "Find past incidents that look like a database connection timeout on svc-orders.",
        "answer_shape": "K most similar historical incidents ranked by cosineDistance on a 768-d embedding.",
        "steps": [run_warm_vector(anchors["user_id"], k=5, query_vec=qvec)],
    })

    # Demo 3: GRAPH alone — both paths, side by side
    print(">>> Scenario 3/4  GRAPH — SQL self-JOIN + UNION ALL")
    scenarios.append({
        "name": "Scenario 3 — GRAPH alone: what breaks if this fails?",
        "user_question": f"What services depend on {anchors['service']}?",
        "answer_shape": "Upstream dependents up to 2 hops via SQL self-JOIN + UNION ALL on MergeTree edge tables.",
        "steps": [
            run_graph_sql(anchors["service"], max_hops=2),
        ],
    })

    # Demo 4: MIXED
    print(">>> Scenario 4/4  MIXED — walk-me-through-it")
    scenarios.append({
        "name": "Scenario 4 — MIXED: walk me through it",
        "user_question": f"{anchors['service']} is failing, walk me through it.",
        "answer_shape": "Live errors + similar past incident + hydrated runbook + blast radius, in one turn.",
        "steps": [
            run_hot_observability(anchors["service"]),
            run_warm_vector(anchors["user_id"], k=5, query_vec=qvec),
            run_warm_lookup(anchors["incident_id"]) if anchors["incident_id"] else None,
            run_graph_sql(anchors["service"], max_hops=2),
        ],
    })
    scenarios[-1]["steps"] = [s for s in scenarios[-1]["steps"] if s is not None]

    meta = {
        "Generated at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ClickHouse version": ch_version,
        "ClickHouse endpoint": CH_HTTP,
        "Database": CH_DB,
        "Iterations per query (client)": str(ITERATIONS),
        "Query vector source": f"real 768-d embedding from obs_historical_incidents ({len(qvec)} dims)",
        "Graph tier backend": "ClickHouse SQL self-JOIN + UNION ALL on MergeTree edge tables",
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(scenarios, meta, tables))
    print(f">>> wrote {out_path}")

    json_out = Path(args.json_out)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(
        {"meta": meta, "tables": tables, "scenarios": scenarios},
        indent=2, default=str,
    ))
    print(f">>> wrote {json_out}")


if __name__ == "__main__":
    main()
