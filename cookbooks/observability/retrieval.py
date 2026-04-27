"""
COOKBOOK 1: Application / Infrastructure Observability
Context Retrieval Across Three Memory Tiers

Scenario: svc-payments is throwing errors. The AI SRE Agent must:
  1. [HOT]   Scan the live event stream for recent errors
  2. [HOT]   Create an investigation workspace and load correlated events
  3. [WARM]  Search historical incidents for similar past failures
  4. [GRAPH] Traverse the service dependency graph to find blast radius
  5. [WARM]  Retrieve the resolution playbook from the most similar incident
  6. [RESULT] Synthesise a complete incident context for the AI agent
"""

import sys
import os
import uuid
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.client import (
    get_ch_client, embed, generate, console,
    print_header, print_tier_banner, print_step, print_sql,
    print_results, print_insight, print_query_time, print_tier_summary,
    time_query, query_to_dicts,
)
from rich.panel import Panel
from rich.rule import Rule


# ---------------------------------------------------------------------------
# STEP 1 -- HOT MEMORY: Detect anomaly in live event stream
# ---------------------------------------------------------------------------

def step1_detect_anomaly(client, target_service: str = "svc-payments") -> tuple[dict, dict]:
    print_tier_banner("HOT")
    print_step(1, 6, f"Scanning live event stream for anomalies on '{target_service}'", "HOT")

    sql = """
    SELECT
        event_id,
        ts,
        service,
        host,
        level,
        message,
        latency_ms,
        error_code,
        trace_id
    FROM enterprise_memory.obs_events_stream
    WHERE service = {service:String}
      AND level IN ('ERROR', 'CRITICAL')
      AND ts >= now() - INTERVAL 10 MINUTE
    ORDER BY ts DESC
    LIMIT 20
    """
    print_sql(sql)

    rows, elapsed = time_query(query_to_dicts, client, sql, {"service": target_service})
    print_query_time(elapsed, "Memory Engine -- in-memory, volatile")

    if not rows:
        rows = [{
            "event_id": str(uuid.uuid4()),
            "ts": datetime.now().isoformat(),
            "service": target_service,
            "host": f"{target_service}-pod-3",
            "level": "ERROR",
            "message": "Connection refused to downstream service after 3 retries",
            "latency_ms": 5001.0,
            "error_code": "DB_TIMEOUT",
            "trace_id": "trace-92847",
        }]

    print_results(rows[:5], title="Recent Errors (Hot Memory)")
    print_insight("Events found", f"{len(rows)} errors in last 10 minutes")
    print_insight("Dominant error", rows[0]["error_code"] or rows[0]["message"][:60])

    timing = {"step": 1, "tier": "HOT", "description": "Scan live event stream for anomalies",
              "elapsed_ms": elapsed, "rows_returned": len(rows)}
    return rows[0], timing


# ---------------------------------------------------------------------------
# STEP 2 -- HOT MEMORY: Create investigation workspace
# ---------------------------------------------------------------------------

def step2_create_workspace(client, trigger_event: dict) -> tuple[str, dict]:
    incident_id = f"INC-{int(time.time())}"
    print_step(2, 6, f"Creating investigation workspace [{incident_id}]", "HOT")

    client.command("TRUNCATE TABLE IF EXISTS enterprise_memory.obs_incident_workspace")

    sql_load = """
    INSERT INTO enterprise_memory.obs_incident_workspace
    SELECT
        {incident_id:String} AS incident_id,
        event_id, ts, service, host, level, message,
        trace_id, latency_ms, error_code,
        now64() AS added_at
    FROM enterprise_memory.obs_events_stream
    WHERE (
        service IN (
            SELECT DISTINCT service
            FROM enterprise_memory.obs_events_stream
            WHERE level IN ('ERROR', 'CRITICAL')
              AND ts >= now() - INTERVAL 10 MINUTE
        )
        OR trace_id = {trace_id:String}
    )
    AND ts >= now() - INTERVAL 15 MINUTE
    """
    print_sql(sql_load)
    client.command(sql_load, parameters={
        "incident_id": incident_id,
        "trace_id": trigger_event.get("trace_id", ""),
    })

    summary_sql = """
    SELECT
        service,
        countIf(level = 'ERROR')    AS errors,
        countIf(level = 'CRITICAL') AS criticals,
        round(avg(latency_ms), 1)   AS avg_latency_ms,
        round(max(latency_ms), 1)   AS max_latency_ms,
        groupArray(DISTINCT error_code) AS error_codes
    FROM enterprise_memory.obs_incident_workspace
    WHERE incident_id = {incident_id:String}
    GROUP BY service
    ORDER BY criticals DESC, errors DESC
    """
    rows, elapsed = time_query(query_to_dicts, client, summary_sql, {"incident_id": incident_id})
    print_query_time(elapsed, "Memory Engine -- workspace aggregation")

    print_results(rows, title=f"Investigation Workspace [{incident_id}]")
    total_events = sum(r["errors"] + r["criticals"] for r in rows) if rows else 0
    print_insight("Workspace created", f"{incident_id} -- {total_events} events loaded")

    timing = {"step": 2, "tier": "HOT", "description": "Create investigation workspace + correlate events",
              "elapsed_ms": elapsed, "rows_returned": len(rows)}
    return incident_id, timing


# ---------------------------------------------------------------------------
# STEP 3 -- WARM MEMORY: Vector search for similar historical incidents
# ---------------------------------------------------------------------------

def step3_vector_search_history(client, trigger_event: dict) -> tuple[list[dict], dict]:
    print_tier_banner("WARM")
    print_step(3, 6, "Searching historical incidents via vector similarity", "WARM")

    query_text = (
        f"{trigger_event.get('message', '')} "
        f"error_code={trigger_event.get('error_code', '')} "
        f"service={trigger_event.get('service', '')} "
        f"latency={trigger_event.get('latency_ms', 0):.0f}ms"
    )
    console.print(f"    [dim]Embedding query: \"{query_text}\"[/dim]")

    query_emb = embed(query_text)
    emb_str = "[" + ",".join(f"{v:.6f}" for v in query_emb) + "]"

    sql = f"""
    SELECT
        incident_id,
        ts,
        title,
        affected_services,
        root_cause,
        resolution,
        severity,
        duration_min,
        round(cosineDistance(embedding, {emb_str}), 4) AS similarity_distance
    FROM enterprise_memory.obs_historical_incidents
    ORDER BY similarity_distance ASC
    LIMIT 3
    """
    print_sql(sql[:500] + "\n  -- [embedding vector truncated for display]")

    rows, elapsed = time_query(query_to_dicts, client, sql)
    print_query_time(elapsed, "MergeTree + cosineDistance vector search")

    print_results(rows, title="Most Similar Historical Incidents (Warm Memory)")
    if rows:
        print_insight("Top match", rows[0]["title"])
        print_insight("Similarity distance", str(rows[0]["similarity_distance"]))
        print_insight("Past resolution", rows[0]["resolution"][:100])

    timing = {"step": 3, "tier": "WARM", "description": "Vector search for similar historical incidents",
              "elapsed_ms": elapsed, "rows_returned": len(rows)}
    return rows, timing


# ---------------------------------------------------------------------------
# STEP 4 -- GRAPH MEMORY: Traverse service dependency graph
# ---------------------------------------------------------------------------

def step4_graph_blast_radius(client, target_service: str = "svc-payments") -> tuple[dict, dict]:
    print_tier_banner("GRAPH")
    print_step(4, 6, f"Traversing service dependency graph for blast radius of '{target_service}'", "GRAPH")

    console.print("    [dim]ClickHouse SQL JOINs on obs_services + obs_dependencies[/dim]")

    sql_upstream = """
    SELECT
        d.from_service AS dependent_service,
        s.criticality,
        s.team,
        d.dep_type,
        d.latency_p99,
        1 AS hops
    FROM enterprise_memory.obs_dependencies d
    JOIN enterprise_memory.obs_services s ON s.service_id = d.from_service
    WHERE d.to_service = {target:String}

    UNION ALL

    SELECT
        d2.from_service,
        s2.criticality,
        s2.team,
        d2.dep_type,
        d2.latency_p99,
        2 AS hops
    FROM enterprise_memory.obs_dependencies d2
    JOIN enterprise_memory.obs_services s2 ON s2.service_id = d2.from_service
    WHERE d2.to_service IN (
        SELECT from_service
        FROM enterprise_memory.obs_dependencies
        WHERE to_service = {target:String}
    )
    """
    print_sql(sql_upstream)

    rows, elapsed = time_query(query_to_dicts, client, sql_upstream, {"target": target_service})
    print_query_time(elapsed, "SQL JOINs -- graph traversal simulation")

    target_sql = """
    SELECT service_id, name, team, criticality, language, region
    FROM enterprise_memory.obs_services
    WHERE service_id = {target:String}
    """
    target_rows = query_to_dicts(client, target_sql, {"target": target_service})

    print_results(rows, title=f"Services Depending on '{target_service}' (Graph Memory)")

    critical_deps = [r for r in rows if r["criticality"] in ("critical", "high")]
    print_insight("Direct dependents", str(len([r for r in rows if r["hops"] == 1])))
    print_insight("Indirect dependents", str(len([r for r in rows if r["hops"] == 2])))
    print_insight("Critical services affected", str(len(critical_deps)))

    timing = {"step": 4, "tier": "GRAPH", "description": "Traverse dependency graph for blast radius",
              "elapsed_ms": elapsed, "rows_returned": len(rows)}
    return {
        "target": target_rows[0] if target_rows else {},
        "dependents": rows,
        "critical_count": len(critical_deps),
    }, timing


# ---------------------------------------------------------------------------
# STEP 5 -- WARM MEMORY: Retrieve runbook / resolution playbook
# ---------------------------------------------------------------------------

def step5_retrieve_runbook(client, similar_incidents: list[dict]) -> tuple[str, dict]:
    print_step(5, 6, "Retrieving resolution playbook from long-term memory", "WARM")

    if not similar_incidents:
        return "No similar incidents found. Escalate to on-call engineer.", {
            "step": 5, "tier": "WARM", "description": "Retrieve resolution playbook",
            "elapsed_ms": 0, "rows_returned": 0}

    top = similar_incidents[0]

    sql = """
    SELECT
        title,
        root_cause,
        resolution,
        duration_min,
        severity,
        affected_services
    FROM enterprise_memory.obs_historical_incidents
    WHERE incident_id = {iid:String}
    """
    rows, elapsed = time_query(query_to_dicts, client, sql, {"iid": str(top["incident_id"])})
    print_query_time(elapsed, "MergeTree -- playbook lookup")

    if not rows:
        rows = [top]

    print_results(rows, title="Resolution Playbook (Warm Memory)")
    playbook = rows[0]["resolution"] if rows else top.get("resolution", "")
    print_insight("Estimated resolution time", f"{top.get('duration_min', '?')} minutes (based on similar incident)")

    timing = {"step": 5, "tier": "WARM", "description": "Retrieve resolution playbook from best match",
              "elapsed_ms": elapsed, "rows_returned": len(rows)}
    return playbook, timing


# ---------------------------------------------------------------------------
# STEP 6 -- RESULT: Synthesise full incident context
# ---------------------------------------------------------------------------

def step6_synthesise_context(
    trigger_event: dict,
    incident_id: str,
    similar_incidents: list[dict],
    blast_radius: dict,
    playbook: str,
) -> dict:
    print_tier_banner("RESULT")
    print_step(6, 6, "Synthesising full incident context for AI agent", "RESULT")

    context = {
        "incident_id": incident_id,
        "triggered_at": datetime.now().isoformat(),
        "trigger": {
            "service": trigger_event.get("service"),
            "error": trigger_event.get("error_code") or trigger_event.get("message", "")[:80],
            "latency_ms": trigger_event.get("latency_ms"),
            "host": trigger_event.get("host"),
        },
        "blast_radius": {
            "direct_dependents": len([d for d in blast_radius["dependents"] if d["hops"] == 1]),
            "indirect_dependents": len([d for d in blast_radius["dependents"] if d["hops"] == 2]),
            "critical_services_affected": blast_radius["critical_count"],
            "affected_teams": list({d["team"] for d in blast_radius["dependents"]}),
        },
        "historical_context": {
            "similar_incidents_found": len(similar_incidents),
            "top_match": similar_incidents[0]["title"] if similar_incidents else None,
            "top_match_root_cause": similar_incidents[0]["root_cause"] if similar_incidents else None,
            "similarity_score": 1 - float(similar_incidents[0]["similarity_distance"]) if similar_incidents else 0,
        },
        "recommended_playbook": playbook[:300] + "..." if len(playbook) > 300 else playbook,
        "memory_sources_used": [
            "HOT: obs_events_stream",
            "HOT: obs_incident_workspace",
            "WARM: obs_historical_incidents (vector search)",
            "GRAPH: obs_dependencies (graph traversal)",
        ],
    }

    console.print(Panel(
        "\n".join([
            f"[bold]Incident ID:[/bold]         {context['incident_id']}",
            f"[bold]Affected Service:[/bold]    {context['trigger']['service']}",
            f"[bold]Error:[/bold]               {context['trigger']['error']}",
            f"[bold]Blast Radius:[/bold]        {context['blast_radius']['direct_dependents']} direct, "
            f"{context['blast_radius']['indirect_dependents']} indirect dependents",
            f"[bold]Critical Services:[/bold]   {context['blast_radius']['critical_services_affected']}",
            f"[bold]Similar Incidents:[/bold]   {context['historical_context']['similar_incidents_found']} found",
            f"[bold]Top Match:[/bold]           {context['historical_context']['top_match']}",
            f"[bold]Similarity Score:[/bold]    {context['historical_context']['similarity_score']:.2%}",
            f"[bold]Recommended Action:[/bold]  {context['recommended_playbook'][:150]}...",
        ]),
        title="[bold cyan]AI SRE Agent -- Incident Context Brief[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))
    return context


# ---------------------------------------------------------------------------
# MAIN COOKBOOK RUNNER
# ---------------------------------------------------------------------------

def run(target_service: str = "svc-payments") -> dict:
    print_header(
        "COOKBOOK 1: Application & Infrastructure Observability",
        "AI SRE Agent -- Multi-Tier Context Retrieval Demo",
    )

    console.print(Rule("[dim]Architecture: Memory engine --> MergeTree+HNSW --> SQL JOINs[/dim]"))
    console.print()

    client = get_ch_client()
    tier_timings = []

    t_start = time.perf_counter()

    trigger, t1 = step1_detect_anomaly(client, target_service)
    tier_timings.append(t1)

    incident_id, t2 = step2_create_workspace(client, trigger)
    tier_timings.append(t2)

    similar, t3 = step3_vector_search_history(client, trigger)
    tier_timings.append(t3)

    blast, t4 = step4_graph_blast_radius(client, target_service)
    tier_timings.append(t4)

    playbook, t5 = step5_retrieve_runbook(client, similar)
    tier_timings.append(t5)

    context = step6_synthesise_context(trigger, incident_id, similar, blast, playbook)

    total_ms = (time.perf_counter() - t_start) * 1000

    # Tier summary
    print_tier_summary(tier_timings)

    console.print(f"[bold green]> Full context retrieved in {total_ms:.0f}ms across all memory tiers[/bold green]\n")

    # Optional LLM synthesis
    if os.getenv("LLM_PROVIDER"):
        print_step(7, 7, "LLM synthesis -- generating incident analysis", "RESULT")
        import json
        messages = [
            {"role": "system", "content": (
                "You are an expert SRE agent. Analyze the incident context below "
                "and provide: (1) root cause assessment, (2) immediate actions, "
                "(3) blast radius summary, (4) prevention recommendations."
            )},
            {"role": "user", "content": json.dumps(context, default=str)},
        ]
        analysis = generate(messages)
        console.print(Panel(analysis, title="[bold cyan]LLM Incident Analysis[/bold cyan]", border_style="cyan"))
        context["llm_analysis"] = analysis

    return context


if __name__ == "__main__":
    run()
