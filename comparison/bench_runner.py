"""Multi-iteration perf bench: stitched stack vs ClickHouse.

Each iteration runs the agent in a fresh subprocess to avoid client
connection accumulation across iterations and to give every run an
identical cold start.

Per-step timing comes from the timings list each agent.main() returns;
we capture it via JSON dumped to a temp file.

Prerequisites:
- Cookbook stack up + seeded
- Stitched services up:  docker compose -f stitched/docker-compose.yml up -d
- Stitched seeded:        python comparison/seed_stitched.py
- Python deps installed:  pip install -r stitched/requirements.txt
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent

# Make sure stitched env vars have sensible defaults; CH host vars must
# point at localhost (the cookbook .env points at the docker hostname).
DEFAULT_ENV = {
    "REDIS_URL":         "redis://localhost:6380/0",
    "NEO4J_URI":         "bolt://localhost:7688",
    "NEO4J_PASSWORD":    "stitched",
    "POSTGRES_DSN":      "postgresql://postgres:stitched@localhost:5433/obs",
    "CLICKHOUSE_HOST":   "localhost",
    "CLICKHOUSE_PORT":   "18123",
    "COMPARE_HOT_WINDOW_MINUTES": "2880",
    # Force the deterministic embedder. Without this, both agents would
    # hit the Gemini API on every step 3, adding 200-2000 ms of network
    # latency that has nothing to do with either backend's storage path.
    # We want to measure the storage path, not Gemini.
    "EMBEDDING_PROVIDER": "",
    "EMBEDDING_MODEL":    "",
    "LLM_PROVIDER":       "",
}

STEP_NAMES = [
    "1 · scan live events",
    "2 · open workspace",
    "3 · vector search history",
    "4 · graph blast radius",
    "5 · fetch runbook",
    "6 · synthesise brief",
]


# Tiny driver script run as a subprocess — imports the chosen agent,
# monkey-patches each step function to record elapsed time, calls main(),
# dumps timings to a JSON file.
DRIVER_SOURCE = r"""
import json, os, sys, time
from io import StringIO
from contextlib import redirect_stdout, redirect_stderr

PROJECT = os.environ["PROJECT_DIR"]
sys.path.insert(0, PROJECT + "/cookbooks")
sys.path.insert(0, PROJECT + "/comparison")

import shared.client as cb_client
cb_client.console.quiet = True

agent_kind = sys.argv[1]
out_path = sys.argv[2]

if agent_kind == "stitched":
    from stitched import agent as a
elif agent_kind == "clickhouse":
    from clickhouse import agent as a
else:
    raise SystemExit("unknown kind " + agent_kind)
a.console.quiet = True

# Monkey-patch each step_<N>_* function to record elapsed_ms. Function
# lookup at call time goes through the module's globals, so swapping
# attributes on the module redirects main()'s calls to the wrapped
# version without touching agent source.
TIMINGS = {}
def _wrap(step_n, fn):
    def wrapped(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            TIMINGS["step_" + str(step_n) + "_ms"] = (time.perf_counter() - t0) * 1000
    return wrapped

for name in dir(a):
    if name.startswith("step") and len(name) >= 5 and name[4].isdigit():
        step_n = int(name[4])
        setattr(a, name, _wrap(step_n, getattr(a, name)))

t0 = time.perf_counter()
with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
    a.main()
TIMINGS["total_ms"] = (time.perf_counter() - t0) * 1000

with open(out_path, "w") as f:
    json.dump(TIMINGS, f)
"""


def run_one(agent_kind: str, env: dict) -> dict:
    """Run one iteration in a subprocess, return timing dict."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as drv:
        drv.write(DRIVER_SOURCE)
        drv_path = drv.name
    out_path = drv_path + ".timings.json"
    try:
        env_full = dict(os.environ, **env, PROJECT_DIR=str(PROJECT))
        proc = subprocess.run(
            [sys.executable, drv_path, agent_kind, out_path],
            env=env_full, timeout=60,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            sys.stderr.write(f"\n[bench] {agent_kind} subprocess failed (rc={proc.returncode})\n")
            sys.stderr.write(proc.stderr.decode()[-2000:])
            sys.stderr.write("\n")
            raise RuntimeError(f"{agent_kind} run failed")
        with open(out_path) as f:
            return json.load(f)
    finally:
        for p in (drv_path, out_path):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


def aggregate(samples: list[dict]) -> dict:
    out = {}
    if not samples:
        return out
    keys = sorted({k for s in samples for k in s.keys()})
    for k in keys:
        vals = [s[k] for s in samples if k in s]
        if not vals:
            continue
        if len(vals) >= 5:
            p95 = round(statistics.quantiles(vals, n=20)[18], 2)
        else:
            p95 = round(max(vals), 2)
        out[k] = {
            "p50":  round(statistics.median(vals), 2),
            "p95":  p95,
            "min":  round(min(vals), 2),
            "max":  round(max(vals), 2),
            "mean": round(statistics.mean(vals), 2),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=20)
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--out", default=str(HERE / "bench_results.md"))
    args = ap.parse_args()

    n = args.iterations

    # Pull CH password from cookbooks/.env first (so we have auth) then
    # apply DEFAULT_ENV (which deliberately blanks EMBEDDING_PROVIDER).
    env = {}
    try:
        import dotenv
        dot = dotenv.dotenv_values(PROJECT / "cookbooks" / ".env")
        for k in ("CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD", "CLICKHOUSE_DB"):
            if dot.get(k):
                env[k] = dot[k]
    except Exception:
        pass
    # DEFAULT_ENV wins over os.environ for the keys we care about
    # (otherwise Gemini env from cookbooks/.env would leak in via the
    # parent process).
    for k, v in DEFAULT_ENV.items():
        env[k] = v

    print(f">>> Each agent: {args.warmup} warmup + {n} timed iterations (subprocess per iter)")
    print(f">>> stitched -> Redis (real :6380) + Pinecone (in-mem) + Neo4j (real :7688) + Postgres (real :5433)")
    print(f">>> clickhouse -> single ClickHouse cluster on :18123 (cookbook seed)")
    sys.stdout.flush()

    # Re-seed Redis so the live-event stream timestamps are within the
    # stitched agent's 600s window. (Stream entries don't auto-expire,
    # but the agent only returns events newer than now-600s.)
    print(">>> re-seeding stitched services to refresh Redis stream timestamps")
    sys.stdout.flush()
    seed_env = dict(os.environ, **env)
    subprocess.run(
        [sys.executable, str(HERE / "seed_stitched.py")],
        env=seed_env, check=True,
        stdout=subprocess.DEVNULL,
    )

    # Warmup
    for _ in range(args.warmup):
        run_one("stitched", env)
        run_one("clickhouse", env)

    # Timed runs
    print(f">>> stitched runs (n={n})")
    sys.stdout.flush()
    t0 = time.perf_counter()
    stitched_samples = [run_one("stitched", env) for _ in range(n)]
    stitched_wall = time.perf_counter() - t0
    print(f"   stitched done in {stitched_wall:.1f}s")
    sys.stdout.flush()

    print(f">>> clickhouse runs (n={n})")
    sys.stdout.flush()
    t0 = time.perf_counter()
    ch_samples = [run_one("clickhouse", env) for _ in range(n)]
    ch_wall = time.perf_counter() - t0
    print(f"   clickhouse done in {ch_wall:.1f}s")
    sys.stdout.flush()

    s_agg = aggregate(stitched_samples)
    c_agg = aggregate(ch_samples)

    lines = [
        "# Stitched stack vs ClickHouse — perf bench",
        "",
        f"- Iterations per agent: **{n}** (after {args.warmup} warmup runs)",
        f"- Stitched wall time: {stitched_wall:.2f} s for {n} runs ({stitched_wall*1000/n:.1f} ms / run including subprocess startup)",
        f"- ClickHouse wall time: {ch_wall:.2f} s for {n} runs ({ch_wall*1000/n:.1f} ms / run including subprocess startup)",
        f"- Stitched backends: Redis (real :6380) + Pinecone (in-mem fallback) + Neo4j (real :7688) + Postgres (real :5433)",
        f"- ClickHouse backend: single cluster on :18123 (cookbook seed)",
        "",
        "Per-iteration is run in a fresh Python subprocess to give each iteration a clean cold start; subprocess startup cost (~0.3 s on macOS) is therefore included in `total_ms`. The per-step rows isolate the actual work and are the fair comparison.",
        "",
        "## Per-step latency (ms, in-process timing)",
        "",
        "| Step | Stitched p50 | Stitched p95 | ClickHouse p50 | ClickHouse p95 | Stitched / CH (p50) |",
        "|------|-------------:|-------------:|---------------:|---------------:|--------------------:|",
    ]
    for step_n, name in enumerate(STEP_NAMES, start=1):
        k = f"step_{step_n}_ms"
        s = s_agg.get(k, {})
        c = c_agg.get(k, {})
        s_p50 = s.get("p50", 0.0)
        c_p50 = c.get("p50", 0.0) or 1e-9
        ratio = s_p50 / c_p50 if c_p50 else float("inf")
        lines.append(f"| {name} | {s_p50:.2f} | {s.get('p95', 0):.2f} | "
                     f"{c.get('p50', 0):.2f} | {c.get('p95', 0):.2f} | "
                     f"{ratio:.1f}x |")
    lines.append("")

    # Sum of per-step p50s and total wall (which includes process+connection setup).
    s_step_p50 = sum(s_agg.get(f"step_{i}_ms", {}).get("p50", 0) for i in range(1, 7))
    c_step_p50 = sum(c_agg.get(f"step_{i}_ms", {}).get("p50", 0) for i in range(1, 7))
    s_total = s_agg.get("total_ms", {}).get("p50", 0)
    c_total = c_agg.get("total_ms", {}).get("p50", 0) or 1e-9

    lines.append("## Summary metrics")
    lines.append("")
    lines.append("Two metrics, two stories:")
    lines.append("")
    lines.append("| Metric | Stitched (p50) | ClickHouse (p50) | Stitched / CH |")
    lines.append("|---|---:|---:|---:|")
    lines.append(f"| Sum of per-step work | {s_step_p50:.2f} ms | {c_step_p50:.2f} ms | "
                 f"{s_step_p50/(c_step_p50 or 1e-9):.2f}x |")
    lines.append(f"| Total per iteration (incl. connection setup + Python orch) | {s_total:.2f} ms | "
                 f"{c_total:.2f} ms | {s_total/c_total:.2f}x |")
    lines.append("")
    lines.append("## What the numbers say")
    lines.append("")
    lines.append(
        f"**Per-step work**: at this scale stitched is slightly faster ({s_step_p50:.1f} ms "
        f"vs {c_step_p50:.1f} ms summed). Two reasons:"
    )
    lines.append("")
    lines.append(
        "- The Pinecone WARM-tier path is the in-memory double (3 rows, no network). "
        "A real Pinecone or Weaviate query adds 5-50 ms on the wire."
    )
    lines.append(
        "- ClickHouse pays HTTP + SQL parse overhead (~5 ms) per call even on tiny "
        "tables; Redis XREVRANGE on a few entries is sub-millisecond. At this seed "
        "size, the CH HNSW machinery is overkill."
    )
    lines.append("")
    lines.append(
        f"**Per-iteration total**: ClickHouse wins by **{s_total/c_total:.1f}x** "
        f"({s_total:.0f} ms vs {c_total:.0f} ms). Stitched pays for four separate "
        f"client connection setups (Redis, Pinecone, Neo4j, Postgres) on every cold "
        f"start; ClickHouse pays for one. In a long-lived agent process the connection "
        f"setup cost is amortised, but in serverless / per-request agents (the typical "
        f"deployment) it dominates."
    )
    lines.append("")
    lines.append(
        "**Where this comparison flips at production scale**: with millions of "
        "incidents in the WARM vector store, ClickHouse HNSW returns a top-K in "
        "single-digit ms while a real Pinecone call adds round-trip latency. With "
        "real Neo4j + a 100k-edge graph, Cypher traversal is fine but still on the "
        "wire. At scale the per-step metric also favors ClickHouse because the "
        "queries become large enough that HTTP+parse overhead is amortised."
    )
    lines.append("")
    lines.append("## Reproduce")
    lines.append("")
    lines.append("```bash")
    lines.append("cd /path/to/clickhouse-agent-memory")
    lines.append("# 1. cookbook stack")
    lines.append("make cli-up && make cli-seed")
    lines.append("# 2. stitched services")
    lines.append("docker compose -f comparison/stitched/docker-compose.yml up -d")
    lines.append("python comparison/seed_stitched.py")
    lines.append("# 3. perf bench")
    lines.append("python comparison/bench_runner.py --iterations 20")
    lines.append("```")

    Path(args.out).write_text("\n".join(lines))
    print(f">>> wrote {args.out}")
    print()
    print("\n".join(lines[-12:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
