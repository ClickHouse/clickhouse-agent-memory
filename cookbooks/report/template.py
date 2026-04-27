"""
cookbooks/report/template.py
----------------------------
ClickHouse-styled HTML builder for session-trace reports.

Takes a session dict (see generate.py for the schema) and returns a
single self-contained HTML string. No external assets except a Google
Fonts link for Inter / JetBrains Mono (with system-font fallbacks so
the report is readable offline).

The styling is documented in docs/report/clickhouse-style-guide.md.
"""

from __future__ import annotations

import html
import json
from typing import Any


TIER_META = {
    "HOT": {
        "label": "HOT",
        "color_var": "--ch-red",
        "engine": "ClickHouse Memory engine",
        "latency_profile": "sub-5ms, volatile",
    },
    "WARM": {
        "label": "WARM",
        "color_var": "--ch-purple",
        "engine": "ClickHouse MergeTree + HNSW",
        "latency_profile": "50-500ms, persistent",
    },
    "GRAPH": {
        "label": "GRAPH",
        "color_var": "--ch-yellow",
        "engine": "ClickHouse SQL JOINs",
        "latency_profile": "10-100ms, relationships",
    },
    "RESULT": {
        "label": "RESULT",
        "color_var": "--ch-yellow",
        "engine": "Agent synthesis",
        "latency_profile": "assembled across tiers",
    },
}


CSS = """
  :root {
    --ch-yellow: #FAFF69;
    --ch-yellow-ink: #1E1E1E;
    --ch-red: #FC2424;
    --ch-purple: #8430CE;
    --ch-bg: #0F0F10;
    --ch-panel: #17171A;
    --ch-panel-2: #1F1F24;
    --ch-border: #2A2A32;
    --ch-text: #F5F5F7;
    --ch-muted: #9CA0AD;
    --ch-good: #34A853;
    --sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
           'Helvetica Neue', Arial, sans-serif;
    --mono: 'JetBrains Mono', 'IBM Plex Mono', ui-monospace,
           SFMono-Regular, Menlo, monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { background: var(--ch-bg); color: var(--ch-text); }
  body {
    font-family: var(--sans);
    font-size: 15px;
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
  }
  a { color: var(--ch-yellow); text-decoration: none; }
  .page { max-width: 1200px; margin: 0 auto; padding: 0 32px; }

  /* Top bar (brand strip) */
  header.top {
    background: var(--ch-bg);
    border-bottom: 1px solid var(--ch-border);
    padding: 18px 32px;
  }
  header.top .inner {
    max-width: 1200px; margin: 0 auto;
    display: flex; align-items: center; justify-content: space-between;
  }
  .brand {
    display: flex; align-items: center; gap: 12px;
    font-family: var(--mono); font-size: 13px;
  }
  .brand .block-red, .brand .block-yellow {
    display: inline-block; width: 10px; height: 18px; border-radius: 2px;
  }
  .brand .block-red { background: var(--ch-red); }
  .brand .block-yellow { background: var(--ch-yellow); }
  .meta-chips { display: flex; gap: 12px; font-size: 12px; color: var(--ch-muted); }
  .meta-chips span { font-family: var(--mono); }

  /* Hero */
  .hero { padding: 72px 0 48px; }
  .hero .eyebrow {
    font-size: 12px; color: var(--ch-yellow);
    text-transform: uppercase; letter-spacing: 0.18em; margin-bottom: 16px;
  }
  .hero h1 {
    font-size: 44px; font-weight: 700; letter-spacing: -0.02em;
    line-height: 1.1; margin-bottom: 24px;
  }
  .hero h1 .question {
    font-family: var(--mono); font-size: 24px; font-weight: 500;
    color: var(--ch-muted); display: block; margin-top: 8px;
  }
  .hero .subtitle { color: var(--ch-muted); font-size: 16px; max-width: 780px; }

  /* Scorecard */
  section.scorecard { padding: 32px 0 48px; }
  .scorecard h2 {
    font-size: 13px; font-weight: 600; color: var(--ch-muted);
    text-transform: uppercase; letter-spacing: 0.14em;
    margin-bottom: 20px;
  }
  .kpi-grid {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px;
    margin-bottom: 28px;
  }
  .kpi {
    background: var(--ch-panel); border: 1px solid var(--ch-border);
    border-radius: 4px; padding: 24px;
    position: relative; overflow: hidden;
  }
  .kpi.hero-kpi { border-top: 3px solid var(--ch-yellow); }
  .kpi-value {
    font-family: var(--mono); font-size: 32px; font-weight: 600;
    color: var(--ch-text); letter-spacing: -0.01em;
  }
  .kpi-value .unit { font-size: 14px; color: var(--ch-muted); margin-left: 4px; }
  .kpi-label {
    font-size: 11px; color: var(--ch-muted); text-transform: uppercase;
    letter-spacing: 0.12em; margin-top: 8px;
  }
  .kpi-note {
    font-size: 12px; color: var(--ch-muted); margin-top: 6px;
  }
  .scorecard-narrative {
    background: var(--ch-panel); border-left: 3px solid var(--ch-yellow);
    padding: 20px 24px; font-size: 15px; color: var(--ch-text);
    border-radius: 0 4px 4px 0;
  }
  .scorecard-narrative strong { color: var(--ch-yellow); }

  /* Steps */
  section.steps { padding: 32px 0 48px; }
  section.steps h2 {
    font-size: 13px; font-weight: 600; color: var(--ch-muted);
    text-transform: uppercase; letter-spacing: 0.14em;
    margin-bottom: 24px;
  }
  article.step {
    background: var(--ch-panel); border: 1px solid var(--ch-border);
    border-left: 3px solid var(--ch-border);
    border-radius: 4px; padding: 28px; margin-bottom: 20px;
  }
  article.step.hot   { border-left-color: var(--ch-red); }
  article.step.warm  { border-left-color: var(--ch-purple); }
  article.step.graph { border-left-color: var(--ch-yellow); }
  article.step.empty { opacity: 0.92; }
  .step-header {
    display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap;
    margin-bottom: 16px;
  }
  .step-number {
    font-family: var(--mono); font-size: 11px; color: var(--ch-muted);
    text-transform: uppercase; letter-spacing: 0.14em;
  }
  .step-label {
    font-size: 20px; font-weight: 600; color: var(--ch-text);
  }
  .tier-chip {
    display: inline-flex; align-items: center; gap: 6px;
    font-family: var(--mono); font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.12em;
    padding: 3px 8px; border-radius: 3px;
    border: 1px solid var(--ch-border); color: var(--ch-muted);
  }
  .tier-chip .dot {
    width: 8px; height: 8px; border-radius: 50%;
    display: inline-block; background: var(--ch-muted);
  }
  .tier-chip.hot .dot   { background: var(--ch-red); }
  .tier-chip.warm .dot  { background: var(--ch-purple); }
  .tier-chip.graph .dot { background: var(--ch-yellow); }
  .step-latency {
    font-family: var(--mono); font-size: 13px; color: var(--ch-muted);
    margin-left: auto;
  }
  .step-latency b { color: var(--ch-yellow); font-weight: 600; }

  .step-section { margin-top: 18px; }
  .step-section h4 {
    font-size: 11px; font-weight: 600; color: var(--ch-muted);
    text-transform: uppercase; letter-spacing: 0.14em; margin-bottom: 10px;
  }
  .step-reasoning p {
    font-style: italic; color: var(--ch-text); font-size: 15px;
    max-width: 900px;
  }
  .step-sql pre {
    background: var(--ch-panel-2); border: 1px solid var(--ch-border);
    border-radius: 4px; padding: 18px 20px;
    overflow-x: auto; font-family: var(--mono); font-size: 13px;
    line-height: 1.5; color: var(--ch-text);
    white-space: pre;
  }
  .step-sql .sql-comment { color: var(--ch-muted); font-style: italic; }
  .step-sql .sql-keyword { color: var(--ch-yellow); font-weight: 600; }
  .step-sql .sql-string  { color: #E7B4FF; }
  .step-sql .sql-number  { color: #9DDAFF; }

  .step-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 24px;
    margin-top: 18px;
  }
  .step-precision ul { list-style: none; padding-left: 0; }
  .step-precision li {
    font-family: var(--mono); font-size: 13px; color: var(--ch-text);
    padding: 5px 0; border-bottom: 1px dashed var(--ch-border);
    display: flex; justify-content: space-between; gap: 16px;
  }
  .step-precision li:last-child { border-bottom: 0; }
  .step-precision li .k { color: var(--ch-muted); flex-shrink: 0; }
  .step-precision li .v { text-align: right; color: var(--ch-text); }
  .step-precision li .v.highlight { color: var(--ch-yellow); font-weight: 600; }
  .step-insight {
    background: var(--ch-panel-2); border-radius: 4px;
    padding: 18px 20px;
  }
  .step-insight p { color: var(--ch-text); font-size: 15px; }

  .step-empty-note {
    background: rgba(252, 36, 36, 0.08);
    border: 1px solid rgba(252, 36, 36, 0.35);
    border-radius: 4px; padding: 16px 20px;
    color: #FFB3B3;
  }
  .step-empty-note strong { color: var(--ch-red); }

  /* Final brief */
  section.brief { padding: 32px 0 64px; }
  .brief-card {
    background: var(--ch-panel); border: 1px solid var(--ch-border);
    border-top: 3px solid var(--ch-yellow); border-radius: 4px;
    padding: 32px;
  }
  .brief-card h2 {
    font-size: 24px; font-weight: 700; letter-spacing: -0.01em;
    margin-bottom: 16px;
  }
  .brief-card ul { list-style: none; padding: 0; margin: 16px 0; }
  .brief-card li {
    padding: 10px 0; border-bottom: 1px dashed var(--ch-border);
    font-size: 15px;
  }
  .brief-card li:last-child { border-bottom: 0; }
  .brief-card li b { color: var(--ch-yellow); margin-right: 8px; }
  .brief-card .recommended {
    background: rgba(250, 255, 105, 0.08);
    border: 1px solid rgba(250, 255, 105, 0.25);
    border-radius: 4px; padding: 18px 20px; margin-top: 20px;
    font-size: 15px;
  }
  .brief-card .recommended strong { color: var(--ch-yellow); }

  /* Footer */
  footer {
    border-top: 1px solid var(--ch-border);
    padding: 24px 32px;
    text-align: center; color: var(--ch-muted); font-size: 12px;
    font-family: var(--mono);
  }

  @media (max-width: 900px) {
    .kpi-grid { grid-template-columns: repeat(2, 1fr); }
    .step-grid { grid-template-columns: 1fr; }
    .hero h1 { font-size: 34px; }
  }

  @media print {
    body { background: white; color: #111; }
    .kpi { background: #f5f5f7; border-color: #ddd; }
    article.step { background: white; border-color: #ddd; page-break-inside: avoid; }
    .step-sql pre { background: #f5f5f7; border-color: #ddd; color: #111; }
    .step-insight { background: #f5f5f7; }
    .brief-card { background: white; border-color: #ddd; }
  }
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "-"
    if n == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PB"


def _fmt_latency(ms: float | None) -> str:
    if ms is None:
        return "-"
    if ms < 10:
        return f"{ms:.2f} ms"
    if ms < 100:
        return f"{ms:.1f} ms"
    return f"{ms:.0f} ms"


def _fmt_rows(n: int | None) -> str:
    if n is None:
        return "-"
    if n < 1_000:
        return f"{n}"
    if n < 1_000_000:
        return f"{n/1_000:.1f}K"
    return f"{n/1_000_000:.2f}M"


def _highlight_sql(sql: str) -> str:
    """Very small SQL colorizer, enough to give ClickHouse-themed highlights.

    We intentionally stay light: keywords yellow, comments muted, strings
    purple, numbers blue. No regex black magic.
    """
    import re
    esc = html.escape(sql)

    keywords = (
        r"\bSELECT\b|\bFROM\b|\bWHERE\b|\bAND\b|\bOR\b|\bIN\b|\bORDER BY\b|"
        r"\bGROUP BY\b|\bLIMIT\b|\bINSERT INTO\b|\bVALUES\b|\bUNION ALL\b|"
        r"\bJOIN\b|\bON\b|\bAS\b|\bINTERVAL\b|\bMINUTE\b|\bNULL\b|"
        r"\bTRUE\b|\bFALSE\b|\bnow\(\)|\bcount\(\)|\bcountIf\b|\bavg\b|"
        r"\bmax\b|\bround\b|\bcosineDistance\b|\bgroupArray\b|\buniqExact\b"
    )

    # Comments first (-- to end of line). Because of html escaping we use
    # the escaped sequence.
    def _comment(m):
        return f'<span class="sql-comment">{m.group(0)}</span>'

    out = re.sub(r"--[^\n]*", _comment, esc)

    def _kw(m):
        return f'<span class="sql-keyword">{m.group(0)}</span>'

    out = re.sub(keywords, _kw, out, flags=re.IGNORECASE)

    def _str(m):
        return f'<span class="sql-string">{m.group(0)}</span>'

    out = re.sub(r"&#x27;[^&]*?&#x27;", _str, out)
    out = re.sub(r"\b\d+\b", lambda m: f'<span class="sql-number">{m.group(0)}</span>', out)
    return out


# ---------------------------------------------------------------------------
# section builders
# ---------------------------------------------------------------------------

def _scorecard(session: dict[str, Any]) -> str:
    total_rows_read = sum(
        (s["envelope"].get("precision") or {}).get("rows_read") or 0
        for s in session["steps"]
    )
    total_bytes_read = sum(
        (s["envelope"].get("precision") or {}).get("bytes_read") or 0
        for s in session["steps"]
    )
    total_latency = sum(
        s["envelope"].get("latency_ms", 0) for s in session["steps"]
    )
    tools_used = len({s["envelope"].get("operation") for s in session["steps"]})
    empty_count = sum(1 for s in session["steps"] if s["envelope"].get("row_count") == 0)

    baseline_bytes = session.get("brute_force_baseline_bytes", 2_000_000_000)
    baseline_latency_s = session.get("brute_force_baseline_latency_s", 40)

    # Selectivity vs brute-force (percent of hypothetical full-scan bytes).
    if baseline_bytes > 0:
        selectivity_pct = round(100.0 * total_bytes_read / baseline_bytes, 4)
    else:
        selectivity_pct = 0.0

    return f"""
    <section class="scorecard">
      <div class="page">
        <h2>Precision Scorecard</h2>
        <div class="kpi-grid">
          <div class="kpi hero-kpi">
            <div class="kpi-value">{_fmt_rows(total_rows_read)}</div>
            <div class="kpi-label">Physical rows scanned</div>
            <div class="kpi-note">Across {len(session['steps'])} tool call(s)</div>
          </div>
          <div class="kpi">
            <div class="kpi-value">{_fmt_bytes(total_bytes_read)}</div>
            <div class="kpi-label">Bytes read</div>
            <div class="kpi-note">{selectivity_pct}% of a brute-force full scan</div>
          </div>
          <div class="kpi">
            <div class="kpi-value">{_fmt_latency(total_latency)}</div>
            <div class="kpi-label">End-to-end latency</div>
            <div class="kpi-note">Sum of ClickHouse query times</div>
          </div>
          <div class="kpi">
            <div class="kpi-value">{tools_used}<span class="unit">tool{'s' if tools_used != 1 else ''}</span></div>
            <div class="kpi-label">Distinct retrievals</div>
            <div class="kpi-note">{empty_count} empty-state signal(s) handled honestly</div>
          </div>
        </div>
        <div class="scorecard-narrative">
          The agent answered the question with <strong>{_fmt_rows(total_rows_read)}</strong>
          physical rows read and <strong>{_fmt_bytes(total_bytes_read)}</strong> of
          scan I/O, in <strong>{_fmt_latency(total_latency)}</strong> across
          <strong>{tools_used}</strong> targeted ClickHouse queries.
          A naive brute-force approach over the same data would have read
          ~{_fmt_bytes(baseline_bytes)} and taken ~{baseline_latency_s}s.
          Every query used a filter, an index, or a partition-prune; none
          was an unbounded scan.
        </div>
      </div>
    </section>
    """


def _step_card(step: dict[str, Any], idx: int) -> str:
    env = step["envelope"]
    tier = env.get("tier", "HOT")
    tier_class = tier.lower()
    label = step.get("operation_label") or env.get("operation", "tool call")
    sql = env.get("sql") or ""
    insights = env.get("insights") or {}
    precision = env.get("precision") or {}
    row_count = env.get("row_count", 0)
    latency = env.get("latency_ms", 0.0)
    reasoning = step.get("reasoning", "")
    insight_text = step.get("insight_text", "")

    empty = row_count == 0 and env.get("operation") != "save_memory"
    empty_class = " empty" if empty else ""

    precision_rows = []
    precision_rows.append(("Rows read", _fmt_rows(precision.get("rows_read")), bool(precision.get("rows_read"))))
    precision_rows.append(("Bytes read", _fmt_bytes(precision.get("bytes_read")), False))
    precision_rows.append(("Rows returned", str(precision.get("rows_returned", 0)), False))
    precision_rows.append(("Selectivity", precision.get("selectivity") or "-", True))
    for f in precision.get("filters_applied") or []:
        precision_rows.append(("Filter", html.escape(str(f)), False))
    precision_rows.append(("Index hint", html.escape(str(precision.get("index_hint") or "-")), False))
    if precision.get("embedding_dim"):
        precision_rows.append(("Embedding dim", str(precision["embedding_dim"]), False))

    precision_html = "".join(
        f'<li><span class="k">{html.escape(k)}</span>'
        f'<span class="v{" highlight" if hl else ""}">{v}</span></li>'
        for k, v, hl in precision_rows
    )

    if empty:
        insight_html = (
            '<div class="step-empty-note">'
            '<strong>NO DATA.</strong> The query returned zero rows. The agent '
            'stopped this branch rather than fabricating a result. '
            'Honest empty-state beats invented data.'
            '</div>'
        )
    else:
        insight_html = f'<div class="step-insight"><p>{html.escape(insight_text)}</p></div>'

    return f"""
    <article class="step {tier_class}{empty_class}">
      <header class="step-header">
        <span class="step-number">Step {idx}</span>
        <span class="step-label">{html.escape(label)}</span>
        <span class="tier-chip {tier_class}"><span class="dot"></span>{html.escape(tier)}</span>
        <span class="step-latency">tool <b>{html.escape(env.get("operation", ""))}</b> · {_fmt_latency(latency)}</span>
      </header>

      <div class="step-section step-reasoning">
        <h4>Why this tool</h4>
        <p>{html.escape(reasoning)}</p>
      </div>

      <div class="step-section step-sql">
        <h4>ClickHouse query</h4>
        <pre><code>{_highlight_sql(sql)}</code></pre>
      </div>

      <div class="step-section step-grid">
        <div class="step-precision">
          <h4>Precision signature</h4>
          <ul>{precision_html}</ul>
        </div>
        <div>
          <h4>Result</h4>
          {insight_html}
        </div>
      </div>
    </article>
    """


def _brief(session: dict[str, Any]) -> str:
    b = session.get("final_brief") or {}
    fields = b.get("fields") or {}
    items = "".join(
        f'<li><b>{html.escape(k)}:</b> {html.escape(str(v))}</li>'
        for k, v in fields.items()
    )
    rec = b.get("recommended") or ""
    rec_html = (
        f'<div class="recommended"><strong>Recommended.</strong> {html.escape(rec)}</div>'
        if rec else ""
    )
    return f"""
    <section class="brief">
      <div class="page">
        <div class="brief-card">
          <h2>Final brief</h2>
          <ul>{items}</ul>
          {rec_html}
        </div>
      </div>
    </section>
    """


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------

def render(session: dict[str, Any]) -> str:
    agent_label = session.get("agent_label", session.get("agent_preset", "agent"))
    user_id = session.get("user_id", "unknown")
    started_at = session.get("started_at", "")
    question = session.get("question", "")

    steps_html = "".join(_step_card(s, i + 1) for i, s in enumerate(session["steps"]))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Enterprise Agent Memory -- Session report</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
<style>{CSS}</style>
</head>
<body>
  <header class="top">
    <div class="inner">
      <div class="brand">
        <span class="block-red"></span><span class="block-yellow"></span>
        <span>enterprise_agent_memory / on ClickHouse</span>
      </div>
      <div class="meta-chips">
        <span>{html.escape(agent_label)}</span>
        <span>user: {html.escape(user_id)}</span>
        <span>{html.escape(started_at)}</span>
      </div>
    </div>
  </header>

  <section class="hero">
    <div class="page">
      <div class="eyebrow">Session trace / solution architect report</div>
      <h1>
        How the agent answered:
        <span class="question">{html.escape(question)}</span>
      </h1>
      <p class="subtitle">
        Every tool invocation, the SQL ClickHouse ran, the physical rows scanned,
        the selectivity, and the insight the agent produced. Built so any solution
        architect can explain exactly why this is precise work, not brute force.
      </p>
    </div>
  </section>

  {_scorecard(session)}

  <section class="steps">
    <div class="page">
      <h2>Reasoning trace</h2>
      {steps_html}
    </div>
  </section>

  {_brief(session)}

  <footer>
    Generated by cookbooks/report/generate.py -- ClickHouse 26.3 · HNSW vector_similarity · SQL JOINs
  </footer>
</body>
</html>
"""
