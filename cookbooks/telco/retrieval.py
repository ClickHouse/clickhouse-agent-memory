"""
COOKBOOK 2: Telco Network Inventory & Monitoring
Context Retrieval Across Three Memory Tiers

Scenario: core-router-01 is showing high CPU and error rate. The AI NetOps
Agent must:
  1. [HOT]   Scan live network state for degraded/at-risk elements
  2. [HOT]   Create a fault investigation workspace for the element
  3. [WARM]  Vector search for similar past network events and resolutions
  4. [GRAPH] Traverse network topology to assess downstream impact
  5. [HOT]   Perform real-time anomaly scoring across all live elements
  6. [RESULT] Synthesise a complete fault context and remediation plan
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
# STEP 1 -- HOT MEMORY: Scan live network state for anomalies
# ---------------------------------------------------------------------------

def step1_scan_network_state(client, target_element: str = "core-router-01") -> tuple[dict, dict]:
    print_tier_banner("HOT")
    print_step(1, 6, "Scanning live network state for anomalies", "HOT")

    sql = """
    SELECT
        element_id,
        element_type,
        vendor,
        region,
        status,
        round(cpu_pct, 1)      AS cpu_pct,
        round(mem_pct, 1)      AS mem_pct,
        round(traffic_gbps, 2) AS traffic_gbps,
        round(error_rate, 4)   AS error_rate,
        last_seen,
        multiIf(
            status = 'down',        'CRITICAL',
            status = 'degraded',    'HIGH',
            cpu_pct > 85,           'HIGH',
            error_rate > 1.0,       'HIGH',
            cpu_pct > 70,           'MEDIUM',
            'NORMAL'
        ) AS alert_level
    FROM enterprise_memory.telco_network_state
    ORDER BY
        multiIf(alert_level='CRITICAL',0, alert_level='HIGH',1,
                alert_level='MEDIUM',2, 3),
        cpu_pct DESC
    """
    print_sql(sql)

    rows, elapsed = time_query(query_to_dicts, client, sql)
    print_query_time(elapsed, "Memory Engine -- live network state")

    print_results(rows, title="Live Network Element State (Hot Memory)")

    target = next((r for r in rows if r["element_id"] == target_element), None)
    if not target:
        target = {
            "element_id": target_element, "element_type": "core",
            "vendor": "Cisco", "region": "us-east", "status": "degraded",
            "cpu_pct": 91.5, "mem_pct": 78.2, "traffic_gbps": 385.0,
            "error_rate": 1.8, "last_seen": datetime.now().isoformat(),
            "alert_level": "HIGH",
        }

    print_insight("Target element status", f"{target['status'].upper()} -- CPU: {target['cpu_pct']}%, Error Rate: {float(target['error_rate']):.4f}")
    critical = [r for r in rows if r["alert_level"] in ("CRITICAL", "HIGH")]
    print_insight("Elements at risk", f"{len(critical)} of {len(rows)} elements need attention")

    timing = {"step": 1, "tier": "HOT", "description": "Scan live network state for anomalies",
              "elapsed_ms": elapsed, "rows_returned": len(rows)}
    return target, timing


# ---------------------------------------------------------------------------
# STEP 2 -- HOT MEMORY: Create fault investigation workspace
# ---------------------------------------------------------------------------

def step2_create_fault_workspace(client, target: dict) -> tuple[str, dict]:
    fault_id = f"FAULT-{int(time.time())}"
    print_step(2, 6, f"Creating fault investigation workspace [{fault_id}]", "HOT")

    client.command("TRUNCATE TABLE IF EXISTS enterprise_memory.telco_fault_workspace")

    metrics = [
        (fault_id, target["element_id"], datetime.now(), "cpu_pct",      float(target["cpu_pct"]),      85.0, "HIGH"),
        (fault_id, target["element_id"], datetime.now(), "mem_pct",      float(target["mem_pct"]),      90.0, "MEDIUM"),
        (fault_id, target["element_id"], datetime.now(), "traffic_gbps", float(target["traffic_gbps"]), 400.0, "MEDIUM"),
        (fault_id, target["element_id"], datetime.now(), "error_rate",   float(target["error_rate"]),   1.0,  "HIGH"),
    ]
    client.insert(
        "enterprise_memory.telco_fault_workspace",
        metrics,
        column_names=["fault_id", "element_id", "ts", "metric", "value", "threshold", "severity"],
    )

    sql_summary = """
    SELECT
        element_id,
        metric,
        round(value, 2)     AS current_value,
        round(threshold, 2) AS threshold,
        severity,
        round((value / threshold - 1) * 100, 1) AS pct_over_threshold
    FROM enterprise_memory.telco_fault_workspace
    WHERE fault_id = {fault_id:String}
    ORDER BY pct_over_threshold DESC
    """
    print_sql(sql_summary)
    rows, elapsed = time_query(query_to_dicts, client, sql_summary, {"fault_id": fault_id})
    print_query_time(elapsed, "Memory Engine -- workspace threshold analysis")

    print_results(rows, title=f"Fault Workspace [{fault_id}] -- Threshold Breaches")
    print_insight("Workspace created", f"{fault_id} -- {len(rows)} metrics loaded")

    timing = {"step": 2, "tier": "HOT", "description": "Create fault workspace + threshold breach analysis",
              "elapsed_ms": elapsed, "rows_returned": len(rows)}
    return fault_id, timing


# ---------------------------------------------------------------------------
# STEP 3 -- WARM MEMORY: Vector search for similar past network events
# ---------------------------------------------------------------------------

def step3_vector_search_events(client, target: dict) -> tuple[list[dict], dict]:
    print_tier_banner("WARM")
    print_step(3, 6, "Searching historical network events via vector similarity", "WARM")

    query_text = (
        f"element_type={target['element_type']} vendor={target['vendor']} "
        f"status={target['status']} cpu={target['cpu_pct']}% "
        f"error_rate={target['error_rate']} region={target['region']}"
    )
    console.print(f"    [dim]Embedding query: \"{query_text}\"[/dim]")

    query_emb = embed(query_text)
    emb_str = "[" + ",".join(f"{v:.6f}" for v in query_emb) + "]"

    sql = f"""
    SELECT
        event_id,
        ts,
        element_id,
        event_type,
        description,
        root_cause,
        resolution,
        round(impact_score, 2)  AS impact_score,
        customers_aff,
        round(cosineDistance(embedding, {emb_str}), 4) AS similarity_distance
    FROM enterprise_memory.telco_network_events
    ORDER BY similarity_distance ASC
    LIMIT 3
    """
    print_sql(sql[:500] + "\n  -- [embedding vector truncated for display]")

    rows, elapsed = time_query(query_to_dicts, client, sql)
    print_query_time(elapsed, "MergeTree + cosineDistance vector search")

    print_results(rows, title="Similar Historical Network Events (Warm Memory)")
    if rows:
        print_insight("Top match", f"{rows[0]['event_type']} on {rows[0]['element_id']}")
        print_insight("Similarity distance", str(rows[0]["similarity_distance"]))
        print_insight("Customers affected (historical)", str(rows[0]["customers_aff"]))

    timing = {"step": 3, "tier": "WARM", "description": "Vector search for similar past network events",
              "elapsed_ms": elapsed, "rows_returned": len(rows)}
    return rows, timing


# ---------------------------------------------------------------------------
# STEP 4 -- GRAPH MEMORY: Traverse network topology for impact assessment
# ---------------------------------------------------------------------------

def step4_graph_topology_impact(client, target_element: str = "core-router-01") -> tuple[dict, dict]:
    print_tier_banner("GRAPH")
    print_step(4, 6, f"Traversing network topology for downstream impact of '{target_element}'", "GRAPH")

    console.print("    [dim]ClickHouse SQL JOINs on telco_elements + telco_connections[/dim]")

    sql_direct = """
    SELECT
        c.to_element         AS downstream_element,
        e.element_type,
        e.vendor,
        e.region,
        e.criticality,
        c.link_type,
        c.capacity_gbps,
        c.latency_ms,
        1                    AS hops
    FROM enterprise_memory.telco_connections c
    JOIN enterprise_memory.telco_elements e ON e.element_id = c.to_element
    WHERE c.from_element = {target:String}

    UNION ALL

    SELECT
        c2.to_element,
        e2.element_type,
        e2.vendor,
        e2.region,
        e2.criticality,
        c2.link_type,
        c2.capacity_gbps,
        c2.latency_ms,
        2 AS hops
    FROM enterprise_memory.telco_connections c2
    JOIN enterprise_memory.telco_elements e2 ON e2.element_id = c2.to_element
    WHERE c2.from_element IN (
        SELECT to_element FROM enterprise_memory.telco_connections
        WHERE from_element = {target:String}
    )
    """
    print_sql(sql_direct)

    rows, elapsed = time_query(query_to_dicts, client, sql_direct, {"target": target_element})
    print_query_time(elapsed, "SQL JOINs -- network topology traversal")

    sql_redundancy = """
    SELECT
        to_element,
        count(DISTINCT from_element) AS path_count,
        groupArray(from_element)     AS via_elements
    FROM enterprise_memory.telco_connections
    WHERE to_element IN (
        SELECT to_element FROM enterprise_memory.telco_connections
        WHERE from_element = {target:String}
    )
    GROUP BY to_element
    HAVING path_count > 1
    """
    redundant = query_to_dicts(client, sql_redundancy, {"target": target_element})

    print_results(rows, title=f"Downstream Impact from '{target_element}' (Graph Memory)")

    base_stations = [r for r in rows if r["element_type"] == "base_station"]
    print_insight("Downstream elements affected", str(len(rows)))
    print_insight("Base stations at risk", str(len(base_stations)))
    print_insight("Elements with redundant paths", str(len(redundant)))

    timing = {"step": 4, "tier": "GRAPH", "description": "Traverse topology for downstream impact",
              "elapsed_ms": elapsed, "rows_returned": len(rows)}
    return {
        "downstream": rows,
        "redundant_paths": redundant,
        "base_stations_at_risk": len(base_stations),
        "total_downstream": len(rows),
    }, timing


# ---------------------------------------------------------------------------
# STEP 5 -- HOT MEMORY: Real-time anomaly scoring across all elements
# ---------------------------------------------------------------------------

def step5_anomaly_scoring(client) -> tuple[list[dict], dict]:
    print_tier_banner("HOT")
    print_step(5, 6, "Computing real-time anomaly scores across all network elements", "HOT")

    sql = """
    SELECT
        element_id,
        element_type,
        vendor,
        status,
        round(cpu_pct, 1)      AS cpu_pct,
        round(error_rate, 4)   AS error_rate,
        round(traffic_gbps, 2) AS traffic_gbps,
        round(
            (cpu_pct / 100.0) * 0.35 +
            (mem_pct / 100.0) * 0.20 +
            least(error_rate / 5.0, 1.0) * 0.30 +
            (traffic_gbps / 400.0) * 0.15,
            3
        ) AS anomaly_score,
        multiIf(
            status = 'down',     'CRITICAL',
            status = 'degraded', 'HIGH',
            (cpu_pct / 100.0) * 0.35 +
            (mem_pct / 100.0) * 0.20 +
            least(error_rate / 5.0, 1.0) * 0.30 +
            (traffic_gbps / 400.0) * 0.15 > 0.7, 'HIGH',
            (cpu_pct / 100.0) * 0.35 +
            (mem_pct / 100.0) * 0.20 +
            least(error_rate / 5.0, 1.0) * 0.30 +
            (traffic_gbps / 400.0) * 0.15 > 0.5, 'MEDIUM',
            'NORMAL'
        ) AS risk_level
    FROM enterprise_memory.telco_network_state
    ORDER BY anomaly_score DESC
    """
    print_sql(sql)

    rows, elapsed = time_query(query_to_dicts, client, sql)
    print_query_time(elapsed, "Memory Engine -- real-time multi-metric scoring")

    print_results(rows, title="Real-Time Anomaly Scores -- All Elements (Hot Memory)")
    high_risk = [r for r in rows if r["risk_level"] in ("CRITICAL", "HIGH")]
    print_insight("High/Critical risk elements", f"{len(high_risk)} of {len(rows)}")

    timing = {"step": 5, "tier": "HOT", "description": "Compute real-time anomaly scores across all elements",
              "elapsed_ms": elapsed, "rows_returned": len(rows)}
    return rows, timing


# ---------------------------------------------------------------------------
# STEP 6 -- RESULT: Synthesise network fault context
# ---------------------------------------------------------------------------

def step6_synthesise_context(
    target: dict,
    fault_id: str,
    similar_events: list[dict],
    topology_impact: dict,
    anomaly_scores: list[dict],
) -> dict:
    print_tier_banner("RESULT")
    print_step(6, 6, "Synthesising full network fault context for AI agent", "RESULT")

    top_event = similar_events[0] if similar_events else {}

    context = {
        "fault_id": fault_id,
        "detected_at": datetime.now().isoformat(),
        "element": {
            "id": target["element_id"],
            "type": target["element_type"],
            "vendor": target["vendor"],
            "status": target["status"],
            "cpu_pct": target["cpu_pct"],
            "error_rate": target["error_rate"],
        },
        "impact": {
            "downstream_elements": topology_impact["total_downstream"],
            "base_stations_at_risk": topology_impact["base_stations_at_risk"],
            "redundant_paths_available": len(topology_impact["redundant_paths"]),
        },
        "historical_context": {
            "similar_events_found": len(similar_events),
            "top_match_type": top_event.get("event_type"),
            "top_match_root_cause": top_event.get("root_cause"),
            "top_match_resolution": top_event.get("resolution"),
            "historical_customers_affected": top_event.get("customers_aff", 0),
        },
        "network_health": {
            "total_elements": len(anomaly_scores),
            "high_risk_elements": len([r for r in anomaly_scores if r["risk_level"] in ("CRITICAL", "HIGH")]),
        },
        "memory_sources_used": [
            "HOT: telco_network_state (live element state)",
            "HOT: telco_fault_workspace (threshold breach analysis)",
            "WARM: telco_network_events (vector search -- similar faults)",
            "GRAPH: telco_connections (topology impact traversal)",
        ],
    }

    console.print(Panel(
        "\n".join([
            f"[bold]Fault ID:[/bold]              {context['fault_id']}",
            f"[bold]Element:[/bold]               {context['element']['id']} ({context['element']['type']} / {context['element']['vendor']})",
            f"[bold]Status:[/bold]                {context['element']['status'].upper()} -- CPU: {context['element']['cpu_pct']}%, Error Rate: {float(context['element']['error_rate']):.4f}",
            f"[bold]Downstream Impact:[/bold]     {context['impact']['downstream_elements']} elements, {context['impact']['base_stations_at_risk']} base stations",
            f"[bold]Redundant Paths:[/bold]       {context['impact']['redundant_paths_available']} alternate routes available",
            f"[bold]Similar Past Event:[/bold]    {context['historical_context']['top_match_type']} -- {str(context['historical_context'].get('top_match_root_cause', ''))[:80]}",
            f"[bold]Recommended Action:[/bold]    {str(context['historical_context']['top_match_resolution'])[:150]}",
        ]),
        title="[bold cyan]AI NetOps Agent -- Network Fault Context Brief[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))
    return context


# ---------------------------------------------------------------------------
# MAIN COOKBOOK RUNNER
# ---------------------------------------------------------------------------

def run(target_element: str = "core-router-01") -> dict:
    print_header(
        "COOKBOOK 2: Telco Network Inventory & Monitoring",
        "AI NetOps Agent -- Multi-Tier Context Retrieval Demo",
    )
    console.print(Rule("[dim]Architecture: Memory engine --> MergeTree+HNSW --> SQL JOINs[/dim]"))
    console.print()

    client = get_ch_client()
    tier_timings = []

    t_start = time.perf_counter()

    target, t1 = step1_scan_network_state(client, target_element)
    tier_timings.append(t1)

    fault_id, t2 = step2_create_fault_workspace(client, target)
    tier_timings.append(t2)

    similar, t3 = step3_vector_search_events(client, target)
    tier_timings.append(t3)

    topology, t4 = step4_graph_topology_impact(client, target_element)
    tier_timings.append(t4)

    scores, t5 = step5_anomaly_scoring(client)
    tier_timings.append(t5)

    context = step6_synthesise_context(target, fault_id, similar, topology, scores)

    total_ms = (time.perf_counter() - t_start) * 1000

    # Tier summary
    print_tier_summary(tier_timings)

    console.print(f"[bold green]> Full network context retrieved in {total_ms:.0f}ms across all memory tiers[/bold green]\n")

    # Optional LLM synthesis
    if os.getenv("LLM_PROVIDER"):
        print_step(7, 7, "LLM synthesis -- generating fault assessment", "RESULT")
        import json
        messages = [
            {"role": "system", "content": (
                "You are an expert network operations agent. Analyze the fault context "
                "below and provide: (1) root cause assessment, (2) immediate remediation, "
                "(3) downstream impact summary, (4) preventive measures."
            )},
            {"role": "user", "content": json.dumps(context, default=str)},
        ]
        analysis = generate(messages)
        console.print(Panel(analysis, title="[bold cyan]LLM Fault Assessment[/bold cyan]", border_style="cyan"))
        context["llm_analysis"] = analysis

    return context


if __name__ == "__main__":
    run()
