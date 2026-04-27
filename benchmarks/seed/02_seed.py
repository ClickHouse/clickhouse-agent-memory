"""Bench-scale seeder.

Populates the isolated benchmark cluster with volumes that demonstrate the
production characteristics demo-scale data cannot:

- 100k events in obs_events_stream
- 5k incidents in obs_historical_incidents (with 768-dim vectors)
- 5k entries in agent_memory_long (with 768-dim vectors + HNSW index)
- 40 services in obs_services
- 120 edges in obs_dependencies
- 5k turns in agent_memory_hot
- 1k articles in knowledge_base

Determinism: fixed SEED, deterministic 768-d embeddings via numpy with
sha256-derived seeds. No Gemini calls, no external API. Same input ->
same vectors every run.

Override row counts via env vars: N_EVENTS, N_INCIDENTS, N_SERVICES,
N_EDGES, N_HOT_TURNS, N_KB_ARTICLES.
"""
from __future__ import annotations

import hashlib
import os
import random
import sys
import time
import uuid
from datetime import datetime, timedelta

import numpy as np

import clickhouse_connect


SEED = int(os.getenv("SEED", "42"))
EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))

N_EVENTS = int(os.getenv("N_EVENTS", "100000"))
N_INCIDENTS = int(os.getenv("N_INCIDENTS", "5000"))
N_SERVICES = int(os.getenv("N_SERVICES", "40"))
N_EDGES = int(os.getenv("N_EDGES", "120"))
N_HOT_TURNS = int(os.getenv("N_HOT_TURNS", "5000"))
N_LONG_MEMORIES = int(os.getenv("N_LONG_MEMORIES", "5000"))
N_KB_ARTICLES = int(os.getenv("N_KB_ARTICLES", "1000"))

CH_HOST = os.getenv("CH_HOST", "localhost")
CH_PORT = int(os.getenv("CH_PORT", "18124"))
CH_USER = os.getenv("CH_USER", "default")
CH_PASS = os.getenv("CH_PASS", "clickhouse")
CH_DB = os.getenv("CH_DB", "enterprise_memory")

BATCH = int(os.getenv("BATCH", "10000"))


def det_embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic 768-d unit vector from sha256(text)."""
    h = hashlib.sha256(text.encode()).digest()
    seed = int.from_bytes(h[:8], "big", signed=False) & ((1 << 63) - 1)
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype("float32")
    n = np.linalg.norm(v)
    return (v / n).tolist() if n > 0 else v.tolist()


def main() -> int:
    rng = random.Random(SEED)
    np.random.seed(SEED)

    print(f">>> Connecting to ClickHouse at {CH_HOST}:{CH_PORT} (db={CH_DB})")
    client = clickhouse_connect.get_client(
        host=CH_HOST,
        port=CH_PORT,
        username=CH_USER,
        password=CH_PASS,
        database=CH_DB,
        settings={"allow_experimental_vector_similarity_index": 1},
    )

    # If already seeded, skip. Idempotent re-seed: TRUNCATE first.
    existing = int(client.query("SELECT count() FROM obs_events_stream").result_rows[0][0])
    if existing >= N_EVENTS:
        print(f">>> Already seeded ({existing} events). Skipping. Set REBUILD=1 to force.")
        if os.getenv("REBUILD") != "1":
            return 0
        print(">>> REBUILD=1 set; truncating tables...")

    for t in (
        "obs_events_stream", "obs_incident_workspace", "obs_historical_incidents",
        "obs_services", "obs_dependencies",
        "agent_memory_hot", "agent_memory_long", "knowledge_base",
    ):
        client.command(f"TRUNCATE TABLE IF EXISTS {t}")

    print(f">>> Seeding bench-scale dataset (SEED={SEED})")
    print(f"    events={N_EVENTS:,}  incidents={N_INCIDENTS:,}  services={N_SERVICES}  edges={N_EDGES}")
    print(f"    long_mem={N_LONG_MEMORIES:,}  hot_turns={N_HOT_TURNS:,}  kb={N_KB_ARTICLES:,}")

    t0 = time.perf_counter()

    # ---------- obs_services ----------
    teams = ["payments", "orders", "inventory", "auth", "search", "platform", "data", "ml", "billing", "shipping"]
    languages = ["go", "python", "java", "rust", "node"]
    regions = ["us-east", "us-west", "eu-west", "ap-south"]
    crit_levels = ["low", "medium", "high", "critical"]
    services_rows = []
    for i in range(N_SERVICES):
        sid = f"svc-{i:03d}" if i > 0 else "svc-orders"  # keep canonical anchor
        services_rows.append([
            sid, sid,
            rng.choice(teams), rng.choice(languages),
            rng.choice(crit_levels), rng.choice(regions),
        ])
    client.insert("obs_services", services_rows,
                  column_names=["service_id", "name", "team", "language", "criticality", "region"])
    print(f"  + obs_services: {len(services_rows)} rows")

    # ---------- obs_dependencies ----------
    edges_set = set()
    edges_rows = []
    while len(edges_rows) < N_EDGES:
        a = rng.choice(services_rows)[0]
        b = rng.choice(services_rows)[0]
        if a == b or (a, b) in edges_set:
            continue
        edges_set.add((a, b))
        edges_rows.append([a, b, rng.choice(["sync", "async", "batch"]), float(rng.randint(5, 200))])
    client.insert("obs_dependencies", edges_rows,
                  column_names=["from_service", "to_service", "dep_type", "latency_p99"])
    print(f"  + obs_dependencies: {len(edges_rows)} rows")

    # ---------- obs_events_stream (Memory engine, large) ----------
    levels = ["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"]
    msg_templates = [
        "request handled in {ms}ms",
        "DB connection timeout after {ms}ms",
        "rate limit exceeded for tenant {t}",
        "OOM killer triggered, RSS={mb}MB",
        "TLS handshake failed for upstream",
        "auth check failed: invalid token",
        "circuit breaker tripped on downstream",
        "cache miss for key {key}",
        "retry attempt {n} succeeded",
        "graceful shutdown initiated",
    ]
    err_codes = [None, "ETIMEDOUT", "ECONNRESET", "EOOM", "EAUTH", "ERATELIM"]
    envs = ["prod", "staging", "canary"]
    now = datetime.utcnow()
    written = 0
    while written < N_EVENTS:
        chunk = min(BATCH, N_EVENTS - written)
        rows = []
        for _ in range(chunk):
            svc = rng.choice(services_rows)[0]
            mins_back = rng.randint(0, 60 * 24 * 7)  # last 7 days
            ts = now - timedelta(minutes=mins_back, seconds=rng.randint(0, 59))
            tmpl = rng.choice(msg_templates)
            msg = tmpl.format(ms=rng.randint(10, 5000),
                              t=f"tenant-{rng.randint(1, 50)}",
                              mb=rng.randint(512, 8192),
                              key=f"k:{rng.randint(0, 9999)}",
                              n=rng.randint(1, 5))
            rows.append([
                str(uuid.UUID(int=rng.getrandbits(128))),
                ts, svc, f"host-{rng.randint(1, 200):03d}",
                rng.choice(levels), msg,
                f"trace-{rng.getrandbits(64):x}", f"span-{rng.getrandbits(32):x}",
                float(rng.randint(1, 1000)),
                rng.choice(err_codes),
                rng.choice(regions), rng.choice(envs),
            ])
        client.insert("obs_events_stream", rows, column_names=[
            "event_id", "ts", "service", "host", "level", "message",
            "trace_id", "span_id", "latency_ms", "error_code", "region", "env",
        ])
        written += chunk
    print(f"  + obs_events_stream: {written:,} rows")

    # ---------- obs_historical_incidents (with vectors) ----------
    severities = ["P1", "P2", "P3", "P4"]
    title_templates = [
        "Database connection pool exhaustion on {svc}",
        "TLS certificate expiry causing 503s on {svc}",
        "Memory leak in {svc} causing OOM restarts",
        "Deadlock during peak traffic on {svc}",
        "Rate limit misconfiguration on {svc}",
        "DNS resolution failure cascading from {svc}",
        "Disk full on hosts running {svc}",
        "GC pause spike causing {svc} latency",
        "Auth cache stampede on {svc}",
        "Schema migration deadlock on {svc}",
    ]
    written = 0
    while written < N_INCIDENTS:
        chunk = min(BATCH // 4, N_INCIDENTS - written)
        rows = []
        for _ in range(chunk):
            svc = rng.choice(services_rows)[0]
            title = rng.choice(title_templates).format(svc=svc)
            desc = f"{title}. Started during {rng.choice(['peak', 'off-peak', 'deploy'])} window."
            rows.append([
                str(uuid.UUID(int=rng.getrandbits(128))),
                now - timedelta(days=rng.randint(1, 365)),
                title, desc,
                [svc, rng.choice(services_rows)[0]],
                f"Root cause: {rng.choice(['config drift', 'capacity', 'bug', 'cert expiry'])}",
                f"Resolution: {rng.choice(['restart', 'rollback', 'patch', 'scale up'])}",
                rng.choice(severities),
                rng.randint(5, 240),
                det_embed(title + " " + desc),
            ])
        client.insert("obs_historical_incidents", rows, column_names=[
            "incident_id", "ts", "title", "description", "affected_services",
            "root_cause", "resolution", "severity", "duration_min", "embedding",
        ])
        written += chunk
    print(f"  + obs_historical_incidents: {written:,} rows (with 768-d vectors)")

    # ---------- agent_memory_hot ----------
    roles = ["user", "assistant", "tool"]
    n_sessions = max(1, N_HOT_TURNS // 20)
    written = 0
    while written < N_HOT_TURNS:
        chunk = min(BATCH, N_HOT_TURNS - written)
        rows = []
        for _ in range(chunk):
            sid = f"sess-{rng.randint(0, n_sessions - 1):05d}"
            rows.append([
                sid, rng.randint(0, 100),
                rng.choice(roles),
                f"turn content {rng.randint(0, 99999)}",
                "" if rng.random() < 0.7 else rng.choice(["search_events", "semantic_search", "find_related_entities"]),
                "",
                now - timedelta(minutes=rng.randint(0, 60 * 24)),
            ])
        client.insert("agent_memory_hot", rows, column_names=[
            "session_id", "turn_id", "role", "content", "tool_name", "metadata", "ts",
        ])
        written += chunk
    print(f"  + agent_memory_hot: {written:,} rows across {n_sessions} sessions")

    # ---------- agent_memory_long (with vectors + HNSW) ----------
    users = [f"u-{i:05d}" for i in range(50)]
    agents = ["ai-sre-agent", "ai-netops-agent", "ai-soc-agent", "support-copilot"]
    memory_types = ["episodic", "semantic", "procedural"]
    written = 0
    while written < N_LONG_MEMORIES:
        chunk = min(BATCH // 4, N_LONG_MEMORIES - written)
        rows = []
        for _ in range(chunk):
            content = f"long-term memory content {rng.randint(0, 999999)} about {rng.choice(services_rows)[0]}"
            rows.append([
                str(uuid.UUID(int=rng.getrandbits(128))),
                rng.choice(users), rng.choice(agents),
                f"sess-{rng.randint(0, n_sessions - 1):05d}", rng.randint(0, 100),
                rng.choice(roles), content,
                det_embed(content),
                rng.choice(memory_types),
                round(rng.random(), 3),
                now - timedelta(days=rng.randint(0, 90)),
            ])
        client.insert("agent_memory_long", rows, column_names=[
            "memory_id", "user_id", "agent_id", "session_id", "turn_id",
            "role", "content", "content_embedding", "memory_type", "importance", "ts",
        ])
        written += chunk
    print(f"  + agent_memory_long: {written:,} rows (with 768-d vectors + HNSW)")

    # ---------- knowledge_base ----------
    kb_categories = ["runbook", "playbook", "policy", "howto", "postmortem"]
    rows = []
    for i in range(N_KB_ARTICLES):
        title = f"Knowledge article {i}: {rng.choice(['handling', 'investigating', 'resolving'])} {rng.choice(['outages', 'latency', 'auth issues', 'data loss'])}"
        body = f"Detailed knowledge content for article {i}, ~200 words synthetic."
        rows.append([
            str(uuid.UUID(int=rng.getrandbits(128))),
            title, body,
            det_embed(title + " " + body),
            rng.choice(kb_categories),
            [rng.choice(["sre", "platform", "security", "data"])],
            now - timedelta(days=rng.randint(0, 365)),
            now,
            0,
        ])
    client.insert("knowledge_base", rows, column_names=[
        "article_id", "title", "content", "content_embedding",
        "category", "tags", "created_at", "updated_at", "access_count",
    ])
    print(f"  + knowledge_base: {len(rows):,} rows (with 768-d vectors + HNSW)")

    elapsed = time.perf_counter() - t0
    print(f">>> Seed complete in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
