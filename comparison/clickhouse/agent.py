"""
ClickHouse implementation of the same SRE agent scenario.

One client. One database. One query language. Four tiers in one place.

All six steps hit the same cluster, reuse the cookbook tables, and join
across them freely because there is only one place the data lives.
"""

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "cookbooks"))
from shared.client import (
    get_ch_client, embed, console, print_header, print_step,
    print_insight, print_results, query_to_dicts,
)


def step1_detect_anomaly(client, target: str) -> dict:
    print_step(1, 6, f"Scan live event stream for '{target}'", "HOT")
    # Demo default is 10 minutes for the "live incident" narrative.
    # Tests and CI override via COMPARE_HOT_WINDOW_MINUTES when seed may be older.
    window_min = int(os.environ.get("COMPARE_HOT_WINDOW_MINUTES", "10"))
    sql = f"""
    SELECT event_id, ts, service, host, level, message,
           latency_ms, error_code, trace_id
    FROM enterprise_memory.obs_events_stream
    WHERE service = {{s:String}}
      AND level IN ('ERROR', 'CRITICAL')
      AND ts >= now() - INTERVAL {window_min} MINUTE
    ORDER BY ts DESC
    LIMIT 20
    """
    t0 = time.perf_counter()
    rows = query_to_dicts(client, sql, {"s": target})
    elapsed = (time.perf_counter() - t0) * 1000
    print_results(rows[:5], title="Recent Errors (Memory engine)")
    print_insight("Engine", "ClickHouse Memory table")
    print_insight("Events found", f"{len(rows)} ({elapsed:.1f}ms)")
    if not rows:
        raise RuntimeError("no trigger event; would page on-call")
    return rows[0]


def step2_create_workspace(client, trigger: dict) -> str:
    incident_id = f"INC-{int(time.time())}"
    print_step(2, 6, f"Create workspace [{incident_id}] via INSERT ... SELECT", "HOT")
    client.command("TRUNCATE TABLE IF EXISTS enterprise_memory.obs_incident_workspace")
    client.command(
        """
        INSERT INTO enterprise_memory.obs_incident_workspace
        SELECT {iid:String}, event_id, ts, service, host, level, message,
               trace_id, latency_ms, error_code, now64()
        FROM enterprise_memory.obs_events_stream
        WHERE ts >= now() - INTERVAL 15 MINUTE
          AND (trace_id = {tid:String} OR service IN (
                SELECT DISTINCT service FROM enterprise_memory.obs_events_stream
                WHERE level IN ('ERROR', 'CRITICAL')
                  AND ts >= now() - INTERVAL 10 MINUTE
          ))
        """,
        parameters={"iid": incident_id, "tid": trigger.get("trace_id", "")},
    )
    print_insight("Engine", "Same ClickHouse -- INSERT ... SELECT across HOT tables")
    print_insight("Atomicity", "single statement on single server")
    return incident_id


def step3_vector_search_history(client, trigger: dict) -> list[dict]:
    print_step(3, 6, "Vector search historical incidents (cosineDistance)", "WARM")
    qtext = (f"{trigger.get('message', '')} error_code={trigger.get('error_code', '')} "
             f"service={trigger.get('service', '')} latency={trigger.get('latency_ms', 0):.0f}ms")
    emb = embed(qtext)
    emb_str = "[" + ",".join(f"{v:.6f}" for v in emb) + "]"
    sql = f"""
    SELECT incident_id, title, resolution, severity, duration_min,
           round(cosineDistance(embedding, {emb_str}), 4) AS distance
    FROM enterprise_memory.obs_historical_incidents
    ORDER BY distance ASC
    LIMIT 3
    """
    t0 = time.perf_counter()
    rows = query_to_dicts(client, sql)
    elapsed = (time.perf_counter() - t0) * 1000
    print_results(rows, title="Most Similar Historical Incidents (MergeTree + vector)")
    print_insight("Engine", "Same ClickHouse -- MergeTree with cosineDistance()")
    print_insight("Latency", f"{elapsed:.1f}ms")
    return rows


def step4_graph_blast_radius(client, target: str) -> dict:
    print_step(4, 6, f"Blast radius for '{target}' (SQL JOINs)", "GRAPH")
    sql = """
    SELECT d.from_service AS dependent_service, s.criticality, s.team,
           d.dep_type, 1 AS hops
    FROM enterprise_memory.obs_dependencies d
    JOIN enterprise_memory.obs_services s ON s.service_id = d.from_service
    WHERE d.to_service = {t:String}
    UNION ALL
    SELECT d2.from_service, s2.criticality, s2.team, d2.dep_type, 2 AS hops
    FROM enterprise_memory.obs_dependencies d2
    JOIN enterprise_memory.obs_services s2 ON s2.service_id = d2.from_service
    WHERE d2.to_service IN (
        SELECT from_service FROM enterprise_memory.obs_dependencies
        WHERE to_service = {t:String}
    )
    """
    t0 = time.perf_counter()
    rows = query_to_dicts(client, sql, {"t": target})
    elapsed = (time.perf_counter() - t0) * 1000
    print_results(rows, title=f"Services Depending on '{target}'")
    critical = [r for r in rows if r["criticality"] in ("critical", "high")]
    print_insight("Engine", "Same ClickHouse -- SQL JOIN + UNION ALL on MergeTree")
    print_insight("Critical services affected", str(len(critical)))
    print_insight("Latency", f"{elapsed:.1f}ms")
    return {"dependents": rows, "critical_count": len(critical)}


def step5_retrieve_runbook(client, matches: list[dict]) -> str:
    print_step(5, 6, "Runbook lookup in historical incidents table", "WARM")
    if not matches:
        return "No similar incidents. Escalate."
    top = matches[0]
    rows = query_to_dicts(
        client,
        "SELECT title, root_cause, resolution, duration_min, severity "
        "FROM enterprise_memory.obs_historical_incidents "
        "WHERE incident_id = {iid:String}",
        {"iid": str(top["incident_id"])},
    )
    if not rows:
        rows = [top]
    print_results(rows, title="Resolution Playbook")
    print_insight("Engine", "Same ClickHouse -- same table as vector search")
    return rows[0].get("resolution", "")


def step6_synthesise(trigger: dict, incident_id: str, matches: list[dict],
                     blast: dict, playbook: str) -> dict:
    print_step(6, 6, "Synthesise incident context", "RESULT")
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
        "similar": [{"id": str(m["incident_id"]), "title": m.get("title"),
                     "distance": float(m.get("distance", 0))} for m in matches[:3]],
        "playbook": playbook[:240],
        "memory_sources": [
            "HOT:   obs_events_stream (Memory engine)",
            "HOT:   obs_incident_workspace (Memory engine)",
            "WARM:  obs_historical_incidents (MergeTree + cosineDistance)",
            "GRAPH: obs_services + obs_dependencies (same MergeTree tables)",
        ],
        "backends_used": ["ClickHouse"],
    }
    console.print(ctx)
    return ctx


def main(target_service: str = "svc-payments") -> dict:
    print_header(
        "CLICKHOUSE -- AI SRE Agent",
        "One client, one database, all four tiers",
    )
    client = get_ch_client()
    trigger = step1_detect_anomaly(client, target_service)
    incident_id = step2_create_workspace(client, trigger)
    matches = step3_vector_search_history(client, trigger)
    blast = step4_graph_blast_radius(client, target_service)
    playbook = step5_retrieve_runbook(client, matches)
    return step6_synthesise(trigger, incident_id, matches, blast, playbook)


if __name__ == "__main__":
    main()
