"""Seed the four stitched services with the same scenario data the in-memory
doubles in stitched/agent.py use.

Run after `docker compose -f stitched/docker-compose.yml up -d`. Idempotent:
truncates and reloads each backend on every run so a perf bench gets clean
state.

Defaults match the env vars stitched/agent.py reads:
- REDIS_URL=redis://localhost:6380/0
- NEO4J_URI=bolt://localhost:7688, NEO4J_PASSWORD=stitched
- POSTGRES_DSN=postgresql://postgres:stitched@localhost:5433/obs
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta

import redis
import psycopg2
from neo4j import GraphDatabase


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380/0")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7688")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD", "stitched")
PG_DSN = os.getenv("POSTGRES_DSN", "postgresql://postgres:stitched@localhost:5433/obs")


def seed_redis() -> None:
    """events:svc-payments stream (HOT) + workspace key (warm-up)."""
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    stream = "events:svc-payments"
    r.delete(stream)
    now = datetime.now()
    for i in range(8):
        r.xadd(stream, {
            "event_id": str(uuid.uuid4()),
            "ts": (now - timedelta(seconds=i * 20)).isoformat(),
            "service": "svc-payments",
            "host": f"svc-payments-pod-{i % 3}",
            "level": "ERROR" if i % 2 == 0 else "CRITICAL",
            "message": "Connection refused to downstream service after 3 retries",
            "latency_ms": str(5001.0 + i * 12),
            "error_code": "DB_TIMEOUT",
            "trace_id": f"trace-{92847 + i}",
        })
    print(f"  + redis: 8 events on {stream}")


def seed_neo4j() -> None:
    """Service dependency graph (GRAPH)."""
    drv = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    nodes = {
        "svc-payments":  {"team": "payments",  "criticality": "critical"},
        "svc-checkout":  {"team": "commerce",  "criticality": "critical"},
        "svc-orders":    {"team": "commerce",  "criticality": "high"},
        "svc-web":       {"team": "frontend",  "criticality": "high"},
        "svc-mobile":    {"team": "mobile",    "criticality": "high"},
        "svc-reporting": {"team": "analytics", "criticality": "medium"},
        "svc-email":     {"team": "growth",    "criticality": "low"},
    }
    edges = [
        ("svc-checkout",  "svc-payments", "sync"),
        ("svc-orders",    "svc-payments", "sync"),
        ("svc-web",       "svc-checkout", "sync"),
        ("svc-mobile",    "svc-checkout", "sync"),
        ("svc-reporting", "svc-orders",   "async"),
        ("svc-email",     "svc-orders",   "async"),
    ]
    with drv.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
        for sid, meta in nodes.items():
            s.run(
                "CREATE (s:Service {service_id: $sid, team: $team, criticality: $crit})",
                sid=sid, team=meta["team"], crit=meta["criticality"],
            )
        for f, t, dep in edges:
            s.run(
                "MATCH (a:Service {service_id: $f}), (b:Service {service_id: $t}) "
                "CREATE (a)-[:DEPENDS_ON {dep_type: $dep}]->(b)",
                f=f, t=t, dep=dep,
            )
        s.run(
            "CREATE RANGE INDEX service_id_idx IF NOT EXISTS FOR (s:Service) ON (s.service_id)"
        )
    drv.close()
    print(f"  + neo4j: {len(nodes)} nodes, {len(edges)} edges")


def seed_postgres() -> None:
    """runbooks table (WARM relational lookup)."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS runbooks (
            incident_id   TEXT PRIMARY KEY,
            title         TEXT NOT NULL,
            root_cause    TEXT,
            resolution    TEXT,
            duration_min  INTEGER,
            severity      TEXT
        )
    """)
    cur.execute("TRUNCATE runbooks")
    rows = [
        ("inc-001", "Payment service DB timeout during peak traffic",
         "db connection pool exhausted",
         "1) scale pool to 200, 2) enable retry with jitter, 3) verify downstream PG max_connections headroom",
         45, "P1"),
        ("inc-002", "Checkout latency spike from downstream timeout",
         "cart service degraded",
         "trip circuit breaker; roll back last deploy; confirm SLO", 60, "P2"),
        ("inc-003", "Auth 500s after config drift",
         "stale JWT key on subset of pods",
         "rolling restart; re-sync secret; add drift alert", 30, "P2"),
    ]
    cur.executemany(
        "INSERT INTO runbooks (incident_id, title, root_cause, resolution, "
        "duration_min, severity) VALUES (%s, %s, %s, %s, %s, %s)",
        rows,
    )
    cur.close()
    conn.close()
    print(f"  + postgres: {len(rows)} runbooks")


def main() -> int:
    print(">>> Seeding stitched services with the canonical scenario data")
    t0 = time.perf_counter()
    seed_redis()
    seed_neo4j()
    seed_postgres()
    elapsed = time.perf_counter() - t0
    print(f">>> Stitched seed complete in {elapsed:.1f}s")
    print()
    print("    Note: Pinecone (vector) is left as the in-memory double. Real")
    print("    Pinecone needs an API key + paid account; we substitute Weaviate")
    print("    in the compose file but the agent doesn't speak Weaviate, so")
    print("    the WARM-vector path uses 3 in-memory rows. This biases the")
    print("    perf comparison TOWARDS the stitched stack (in-mem is faster")
    print("    than a real vector DB call); ClickHouse wins by even more in")
    print("    a real production setup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
