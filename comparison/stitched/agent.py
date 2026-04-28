"""
Stitched stack implementation of the SRE agent scenario.

Four backing services, four client libraries, four wire protocols,
four failure modes, four operational surfaces.

  - Pinecone    : historical incident vector search (WARM)
  - Redis       : live event stream + investigation workspace (HOT)
  - Neo4j       : service dependency graph traversal (GRAPH)
  - Postgres    : service catalog / runbook relational lookup (WARM)

This file mirrors cookbooks/observability/retrieval.py step-by-step so
the structural cost of federation is visible. Where a real service is
not reachable (no Pinecone key, no running Neo4j, no running Postgres),
we fall back to a clearly marked in-memory double so the code still runs
and the reader can inspect the shape of what production would do.
"""

import os
import sys
import time
import uuid
import hashlib
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "cookbooks"))
from shared.client import console, print_header, print_step, print_insight, print_results


# ---------------------------------------------------------------------------
# Configured backends -- FOUR services
# ---------------------------------------------------------------------------

@dataclass
class StitchedBackends:
    pinecone_index: str = os.getenv("PINECONE_INDEX", "obs-incidents")
    pinecone_api_key: str = os.getenv("PINECONE_API_KEY", "")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "neo4j")
    postgres_dsn: str = os.getenv("POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/obs")
    services_used: list[str] = field(default_factory=lambda: ["Pinecone", "Redis", "Neo4j", "Postgres"])


EMBED_DIM = 768


def embed(text: str) -> list[float]:
    """Deterministic local embedder so stitched/clickhouse stay comparable."""
    words = text.lower().split()
    vec = [0.0] * EMBED_DIM
    for word in words:
        seed = int(hashlib.md5(word.encode()).hexdigest(), 16) % (2 ** 31)
        rng = random.Random(seed)
        for i in range(EMBED_DIM):
            vec[i] += rng.gauss(0, 1)
    magnitude = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / magnitude for v in vec]


def cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    ma = math.sqrt(sum(x * x for x in a)) or 1.0
    mb = math.sqrt(sum(x * x for x in b)) or 1.0
    return 1.0 - dot / (ma * mb)


# ---------------------------------------------------------------------------
# CLIENT 1 -- Redis (HOT: live events + workspace)
# ---------------------------------------------------------------------------

class RedisClient:
    """Thin wrapper over redis-py with an in-memory fallback."""

    def __init__(self, url: str):
        self.url = url
        self._real = None
        self._fallback_streams: dict[str, list[dict]] = {}
        self._fallback_hashes: dict[str, dict[str, str]] = {}
        try:
            import redis
            self._real = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=1)
            self._real.ping()
            console.print(f"    [dim]Redis connected: {url}[/dim]")
        except Exception as e:
            console.print(f"    [yellow]Redis unavailable ({e}); using in-memory double[/yellow]")
            self._real = None
            self._seed_fallback()

    def _seed_fallback(self):
        stream = "events:svc-payments"
        now = datetime.now()
        self._fallback_streams[stream] = [
            {"event_id": str(uuid.uuid4()), "ts": (now - timedelta(seconds=i * 20)).isoformat(),
             "service": "svc-payments", "host": f"svc-payments-pod-{i % 3}",
             "level": "ERROR" if i % 2 == 0 else "CRITICAL",
             "message": "Connection refused to downstream service after 3 retries",
             "latency_ms": 5001.0 + i * 12,
             "error_code": "DB_TIMEOUT", "trace_id": f"trace-{92847 + i}"}
            for i in range(8)
        ]

    def xrevrange_errors(self, service: str, window_seconds: int = 600) -> list[dict]:
        stream = f"events:{service}"
        if self._real is not None:
            cutoff_ms = int((time.time() - window_seconds) * 1000)
            raw = self._real.xrevrange(stream, max="+", min=cutoff_ms, count=200)
            rows = []
            for _id, fields in raw:
                if fields.get("level") in ("ERROR", "CRITICAL"):
                    rows.append(fields)
                if len(rows) >= 20:
                    break
            return rows
        return [r for r in self._fallback_streams.get(stream, []) if r["level"] in ("ERROR", "CRITICAL")][:20]

    def write_workspace(self, incident_id: str, events: list[dict]) -> None:
        key = f"workspace:{incident_id}"
        if self._real is not None:
            pipe = self._real.pipeline()
            for ev in events:
                pipe.rpush(key, str(ev))
            pipe.expire(key, 3600)
            pipe.execute()
            return
        self._fallback_hashes[key] = {"events": events}

    def close(self) -> None:
        if self._real is not None:
            try:
                self._real.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# CLIENT 2 -- Pinecone (WARM: vector search)
# ---------------------------------------------------------------------------

class PineconeClient:
    """Thin wrapper over pinecone-client with an in-memory fallback."""

    def __init__(self, api_key: str, index_name: str):
        self.index_name = index_name
        self._index = None
        self._fallback_vectors: list[dict] = []
        try:
            if not api_key:
                raise RuntimeError("no PINECONE_API_KEY")
            from pinecone import Pinecone
            pc = Pinecone(api_key=api_key)
            self._index = pc.Index(index_name)
            console.print(f"    [dim]Pinecone connected: index={index_name}[/dim]")
        except Exception as e:
            console.print(f"    [yellow]Pinecone unavailable ({e}); using in-memory double[/yellow]")
            self._seed_fallback()

    def _seed_fallback(self):
        seeds = [
            ("inc-001", "Payment service DB timeout during peak traffic",
             "db connection pool exhausted; increased pool size and added retry with backoff",
             "P1", 45),
            ("inc-002", "Checkout latency spike from downstream timeout",
             "downstream cart service degraded; circuit breaker tripped; rolled back deploy",
             "P2", 60),
            ("inc-003", "Auth service 500 errors after config drift",
             "stale JWT signing key on 2/5 pods; rolling restart restored", "P2", 30),
        ]
        for iid, title, resolution, sev, dur in seeds:
            self._fallback_vectors.append({
                "id": iid,
                "values": embed(title + " " + resolution),
                "metadata": {"title": title, "resolution": resolution,
                             "severity": sev, "duration_min": dur},
            })

    def query(self, vector: list[float], top_k: int = 3) -> list[dict]:
        if self._index is not None:
            res = self._index.query(vector=vector, top_k=top_k, include_metadata=True)
            out = []
            for m in res.matches:
                out.append({"incident_id": m.id, "distance": 1.0 - float(m.score),
                            **(m.metadata or {})})
            return out
        scored = [(cosine_distance(vector, v["values"]), v) for v in self._fallback_vectors]
        scored.sort(key=lambda x: x[0])
        out = []
        for dist, v in scored[:top_k]:
            out.append({"incident_id": v["id"], "distance": round(dist, 4), **v["metadata"]})
        return out


# ---------------------------------------------------------------------------
# CLIENT 3 -- Neo4j (GRAPH: dependency traversal)
# ---------------------------------------------------------------------------

class Neo4jClient:
    """Thin wrapper over neo4j driver with an in-memory fallback."""

    def __init__(self, uri: str, user: str, password: str):
        self._driver = None
        self._fallback_edges: list[tuple[str, str, str]] = []
        self._fallback_nodes: dict[str, dict] = {}
        try:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(uri, auth=(user, password),
                                                connection_timeout=1.0)
            self._driver.verify_connectivity()
            console.print(f"    [dim]Neo4j connected: {uri}[/dim]")
        except Exception as e:
            console.print(f"    [yellow]Neo4j unavailable ({e}); using in-memory double[/yellow]")
            self._driver = None
            self._seed_fallback()

    def _seed_fallback(self):
        self._fallback_nodes = {
            "svc-payments":  {"team": "payments",  "criticality": "critical"},
            "svc-checkout":  {"team": "commerce",  "criticality": "critical"},
            "svc-orders":    {"team": "commerce",  "criticality": "high"},
            "svc-web":       {"team": "frontend",  "criticality": "high"},
            "svc-mobile":    {"team": "mobile",    "criticality": "high"},
            "svc-reporting": {"team": "analytics", "criticality": "medium"},
            "svc-email":     {"team": "growth",    "criticality": "low"},
        }
        self._fallback_edges = [
            ("svc-checkout", "svc-payments", "sync"),
            ("svc-orders",   "svc-payments", "sync"),
            ("svc-web",      "svc-checkout", "sync"),
            ("svc-mobile",   "svc-checkout", "sync"),
            ("svc-reporting","svc-orders",   "async"),
            ("svc-email",    "svc-orders",   "async"),
        ]

    def blast_radius(self, target: str) -> list[dict]:
        if self._driver is not None:
            cypher = (
                "MATCH (s:Service)-[r:DEPENDS_ON*1..2]->(t:Service {service_id: $target}) "
                "RETURN s.service_id AS dependent_service, s.team AS team, "
                "s.criticality AS criticality, size(r) AS hops"
            )
            with self._driver.session() as sess:
                return [dict(rec) for rec in sess.run(cypher, target=target)]
        direct = [(f, dt) for (f, t, dt) in self._fallback_edges if t == target]
        hop1_names = {f for (f, _) in direct}
        indirect = [(f, dt) for (f, t, dt) in self._fallback_edges
                    if t in hop1_names and f not in hop1_names and f != target]
        rows = []
        for name, dep_type in direct:
            meta = self._fallback_nodes.get(name, {})
            rows.append({"dependent_service": name, "team": meta.get("team", "unknown"),
                         "criticality": meta.get("criticality", "low"),
                         "dep_type": dep_type, "hops": 1})
        for name, dep_type in indirect:
            meta = self._fallback_nodes.get(name, {})
            rows.append({"dependent_service": name, "team": meta.get("team", "unknown"),
                         "criticality": meta.get("criticality", "low"),
                         "dep_type": dep_type, "hops": 2})
        return rows

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# CLIENT 4 -- Postgres (WARM: service catalog / runbook lookup)
# ---------------------------------------------------------------------------

class PostgresClient:
    """Thin wrapper over psycopg2 with an in-memory fallback."""

    def __init__(self, dsn: str):
        self._conn = None
        self._fallback_runbooks: dict[str, dict] = {}
        try:
            import psycopg2
            self._conn = psycopg2.connect(dsn, connect_timeout=1)
            console.print(f"    [dim]Postgres connected[/dim]")
        except Exception as e:
            console.print(f"    [yellow]Postgres unavailable ({e}); using in-memory double[/yellow]")
            self._seed_fallback()

    def _seed_fallback(self):
        self._fallback_runbooks = {
            "inc-001": {"title": "Payment service DB timeout during peak traffic",
                        "root_cause": "db connection pool exhausted",
                        "resolution": "1) scale pool to 200, 2) enable retry with jitter, "
                                      "3) verify downstream PG max_connections headroom",
                        "duration_min": 45, "severity": "P1"},
            "inc-002": {"title": "Checkout latency spike from downstream timeout",
                        "root_cause": "cart service degraded",
                        "resolution": "trip circuit breaker; roll back last deploy; confirm SLO",
                        "duration_min": 60, "severity": "P2"},
            "inc-003": {"title": "Auth 500s after config drift",
                        "root_cause": "stale JWT key on subset of pods",
                        "resolution": "rolling restart; re-sync secret; add drift alert",
                        "duration_min": 30, "severity": "P2"},
        }

    def fetch_runbook(self, incident_id: str) -> dict | None:
        if self._conn is not None:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT title, root_cause, resolution, duration_min, severity "
                    "FROM runbooks WHERE incident_id = %s",
                    (incident_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {"title": row[0], "root_cause": row[1], "resolution": row[2],
                        "duration_min": row[3], "severity": row[4]}
        return self._fallback_runbooks.get(incident_id)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# SCENARIO STEPS
# ---------------------------------------------------------------------------

def step1_detect_anomaly(redis_c: RedisClient, target: str) -> dict:
    print_step(1, 6, f"Redis XREVRANGE live events for '{target}'", "HOT")
    t0 = time.perf_counter()
    rows = redis_c.xrevrange_errors(target, window_seconds=600)
    elapsed = (time.perf_counter() - t0) * 1000
    print_results(rows[:5], title=f"Recent Errors (Redis stream)")
    print_insight("Service", "Redis")
    print_insight("Events found", f"{len(rows)} in last 10 minutes ({elapsed:.1f}ms)")
    if not rows:
        raise RuntimeError("no trigger event; would page on-call")
    return rows[0]


def step2_create_workspace(redis_c: RedisClient, trigger: dict) -> str:
    incident_id = f"INC-{int(time.time())}"
    print_step(2, 6, f"Redis RPUSH workspace [{incident_id}]", "HOT")
    redis_c.write_workspace(incident_id, [trigger])
    print_insight("Service", "Redis (separate key space from the stream)")
    print_insight("Workspace key", f"workspace:{incident_id}")
    print_insight("Atomicity", "stream + workspace are separate Redis ops -- no cross-key txn")
    return incident_id


def step3_vector_search_history(pine: PineconeClient, trigger: dict) -> list[dict]:
    print_step(3, 6, "Pinecone similarity query on historical incidents", "WARM")
    # Redis returns all stream fields as strings; coerce latency to float.
    _lat = trigger.get('latency_ms', 0) or 0
    _lat = float(_lat) if isinstance(_lat, str) else _lat
    query_text = (f"{trigger.get('message', '')} "
                  f"error_code={trigger.get('error_code', '')} "
                  f"service={trigger.get('service', '')} "
                  f"latency={_lat:.0f}ms")
    vec = embed(query_text)
    t0 = time.perf_counter()
    matches = pine.query(vec, top_k=3)
    elapsed = (time.perf_counter() - t0) * 1000
    print_results(matches, title="Most Similar Historical Incidents (Pinecone)")
    print_insight("Service", "Pinecone")
    print_insight("Top match", matches[0]["title"] if matches else "none")
    print_insight("Distance metric", "cosine (Pinecone 'score' = 1 - distance)")
    print_insight("Latency", f"{elapsed:.1f}ms (network round trip to managed index)")
    return matches


def step4_graph_blast_radius(neo: Neo4jClient, target: str) -> dict:
    print_step(4, 6, f"Neo4j Cypher blast radius for '{target}'", "GRAPH")
    t0 = time.perf_counter()
    rows = neo.blast_radius(target)
    elapsed = (time.perf_counter() - t0) * 1000
    print_results(rows, title=f"Services Depending on '{target}' (Neo4j)")
    critical = [r for r in rows if r["criticality"] in ("critical", "high")]
    print_insight("Service", "Neo4j")
    print_insight("Direct dependents", str(len([r for r in rows if r["hops"] == 1])))
    print_insight("Indirect dependents", str(len([r for r in rows if r["hops"] == 2])))
    print_insight("Critical services affected", str(len(critical)))
    print_insight("Latency", f"{elapsed:.1f}ms (Cypher over Bolt)")
    return {"dependents": rows, "critical_count": len(critical)}


def step5_retrieve_runbook(pg: PostgresClient, matches: list[dict]) -> str:
    print_step(5, 6, "Postgres SELECT runbook by incident id", "WARM")
    if not matches:
        return "No similar incidents found. Escalate to on-call."
    top_id = matches[0]["incident_id"]
    rb = pg.fetch_runbook(top_id)
    print_insight("Service", "Postgres")
    print_insight("Lookup key", top_id)
    if rb is None:
        print_insight("Result", "no runbook row; escalate")
        return "No runbook found. Escalate."
    print_results([rb], title="Resolution Playbook (Postgres)")
    return rb.get("resolution", "")


def step6_synthesise(backends: StitchedBackends, trigger: dict, incident_id: str,
                     matches: list[dict], blast: dict, playbook: str) -> dict:
    print_step(6, 6, "Synthesise incident context (in-process join across 4 services)", "RESULT")
    ctx = {
        "incident_id": incident_id,
        "triggered_at": datetime.now().isoformat(),
        "trigger": {k: trigger.get(k) for k in ("service", "host", "error_code",
                                                "latency_ms", "message")},
        "blast_radius": {
            "direct": len([r for r in blast["dependents"] if r["hops"] == 1]),
            "indirect": len([r for r in blast["dependents"] if r["hops"] == 2]),
            "critical": blast["critical_count"],
        },
        "similar": [{"id": m["incident_id"], "title": m.get("title"),
                     "distance": m.get("distance")} for m in matches[:3]],
        "playbook": playbook[:240],
        "memory_sources": [
            "HOT/Redis   : events:* stream + workspace:* list",
            "WARM/Pinecone: obs-incidents index (cosine)",
            "GRAPH/Neo4j : (:Service)-[:DEPENDS_ON*1..2]->",
            "WARM/Postgres: runbooks table",
        ],
        "backends_used": backends.services_used,
    }
    console.print(ctx)
    return ctx


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main(target_service: str = "svc-payments") -> dict:
    print_header(
        "STITCHED STACK -- AI SRE Agent",
        "Pinecone + Redis + Neo4j + Postgres (four services, four clients)",
    )
    backends = StitchedBackends()
    console.print(f"[dim]Backends configured: {', '.join(backends.services_used)}[/dim]")

    redis_c = RedisClient(backends.redis_url)
    pine = PineconeClient(backends.pinecone_api_key, backends.pinecone_index)
    neo = Neo4jClient(backends.neo4j_uri, backends.neo4j_user, backends.neo4j_password)
    pg = PostgresClient(backends.postgres_dsn)

    try:
        trigger = step1_detect_anomaly(redis_c, target_service)
        incident_id = step2_create_workspace(redis_c, trigger)
        matches = step3_vector_search_history(pine, trigger)
        blast = step4_graph_blast_radius(neo, target_service)
        playbook = step5_retrieve_runbook(pg, matches)
        return step6_synthesise(backends, trigger, incident_id, matches, blast, playbook)
    finally:
        redis_c.close()
        neo.close()
        pg.close()


if __name__ == "__main__":
    main()
