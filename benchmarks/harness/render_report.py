#!/usr/bin/env python3
"""Render a benchmark results JSON to a markdown table."""
from __future__ import annotations

import argparse
import json
import pathlib


def fmt_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b/1024:.1f} KB"
    return f"{b/1024/1024:.2f} MB"


def render(run: dict, baseline: dict | None) -> str:
    out: list[str] = []
    out.append(f"# Benchmark run — {run['ran_at']}\n")
    out.append(f"- ClickHouse: `{run['version']}` at `{run['ch_http']}` (db=`{run['database']}`)")
    out.append(f"- Iterations: {run['iterations']} (warmup {run['warmup']})")
    out.append(f"- Total wall time: {run['duration_s']}s\n")

    out.append("## Seed size at run time\n")
    out.append("| Table | Rows |")
    out.append("|---|---|")
    for name, rows in sorted(run.get("seed_row_counts", {}).items(), key=lambda kv: -kv[1]):
        out.append(f"| `{name}` | {rows:,} |")
    out.append("")

    out.append("## Per-tool precision + latency\n")
    out.append("| Tool | Tier | Rows read (p50) | Bytes read (p50) | Dur p50 ms | Dur p95 ms | Result rows |")
    out.append("|---|---|---:|---:|---:|---:|---:|")

    tier_map = {
        "T1_scan_live_stream": "HOT",
        "T2_open_investigation": "HOT",
        "T3_recall_memory": "HOT",
        "T4_semantic_search": "WARM",
        "T5_fetch_record": "WARM",
        "T6_replay_session": "WARM",
        "T7_save_memory": "WARM (INSERT)",
        "T8_graph_traverse": "GRAPH",
    }

    total_rows = 0
    total_bytes = 0
    total_dur = 0.0

    for t in run["tools"]:
        if "error" in t:
            out.append(f"| `{t['tool']}` | — | — | — | — | — | ERROR: {t['error']} |")
            continue
        tier = tier_map.get(t["tool"], "?")
        rr = t.get("read_rows_p50", 0)
        rb = t.get("read_bytes_p50", 0)
        dp50 = t.get("query_duration_ms_p50", 0)
        dp95 = t.get("query_duration_ms_p95", 0)
        rs = t.get("result_rows_p50", 0)
        out.append(
            f"| `{t['tool'].split('_',1)[1]}` | {tier} | {rr:,} | {fmt_bytes(rb)} | {dp50} | {dp95} | {rs} |"
        )
        if t["tool"] != "T7_save_memory":
            total_rows += rr
            total_bytes += rb
            total_dur += dp50

    out.append("")
    out.append("## Session totals (excludes INSERT)\n")
    out.append(f"- **Total rows read** across a 7-tool read session: **{total_rows:,}**")
    out.append(f"- **Total bytes read**: **{fmt_bytes(total_bytes)}**")
    out.append(f"- **Sum of p50 tool-call durations**: **{round(total_dur, 2)} ms**")
    out.append("")

    if baseline:
        out.append("## Diff vs baseline\n")
        out.append("| Tool | rows now | rows base | Δ rows | bytes now | bytes base | Δ bytes |")
        out.append("|---|---:|---:|---:|---:|---:|---:|")
        b_by_tool = {t["tool"]: t for t in baseline.get("tools", [])}
        for t in run["tools"]:
            if "error" in t:
                continue
            b = b_by_tool.get(t["tool"])
            if not b:
                out.append(f"| `{t['tool']}` | {t['read_rows_p50']:,} | — | (new) | {t['read_bytes_p50']:,} | — | (new) |")
                continue
            d_r = t["read_rows_p50"] - b.get("read_rows_p50", 0)
            d_b = t["read_bytes_p50"] - b.get("read_bytes_p50", 0)
            out.append(
                f"| `{t['tool']}` | {t['read_rows_p50']:,} | {b.get('read_rows_p50',0):,} | "
                f"{d_r:+,} | {t['read_bytes_p50']:,} | {b.get('read_bytes_p50',0):,} | {d_b:+,} |"
            )
        out.append("")

    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=pathlib.Path, required=True)
    ap.add_argument("--baseline", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()

    run = json.loads(args.run.read_text())
    baseline = json.loads(args.baseline.read_text()) if args.baseline and args.baseline.exists() else None
    md = render(run, baseline)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md + "\n")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
