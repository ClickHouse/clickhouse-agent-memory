"""
COOKBOOK 3: AI-Powered Autonomous Security Operations (SOC)
Context Retrieval Across Three Memory Tiers

Scenario: A suspicious login to a critical PII database from a high-risk IP.
The AI SOC Agent must:
  1. [HOT]   Detect and triage the suspicious event in real-time
  2. [HOT]   Create a case investigation workspace with correlated events
  3. [GRAPH] Query the user-asset graph for access context and risk profile
  4. [WARM]  Match the suspicious IP against the threat intelligence database
  5. [WARM]  Vector search historical incidents for similar attack patterns
  6. [HOT]   Compute user behaviour anomaly score (baseline vs. current)
  7. [RESULT] Synthesise a complete threat context and automated response plan
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
# STEP 1 -- HOT MEMORY: Detect and triage suspicious event
# ---------------------------------------------------------------------------

def step1_detect_triage(client) -> tuple[dict, dict]:
    print_tier_banner("HOT")
    print_step(1, 7, "Scanning live security event stream for high-severity alerts", "HOT")

    sql = """
    SELECT
        event_id,
        ts,
        event_type,
        user_id,
        asset_id,
        src_ip,
        action,
        outcome,
        severity,
        raw_log,
        multiIf(
            severity = 'critical' AND outcome = 'success', 10,
            severity = 'critical' AND outcome = 'failure', 7,
            severity = 'high'    AND outcome = 'success', 8,
            severity = 'high'    AND outcome = 'failure', 5,
            severity = 'medium',                          3,
            1
        ) AS triage_score
    FROM enterprise_memory.sec_events_stream
    WHERE severity IN ('critical', 'high')
      AND ts >= now() - INTERVAL 15 MINUTE
    ORDER BY triage_score DESC, ts DESC
    LIMIT 10
    """
    print_sql(sql)

    rows, elapsed = time_query(query_to_dicts, client, sql)
    print_query_time(elapsed, "Memory Engine -- real-time triage scoring")

    if not rows:
        rows = [{
            "event_id": str(uuid.uuid4()),
            "ts": datetime.now().isoformat(),
            "event_type": "login_success",
            "user_id": "user-006",
            "asset_id": "asset-001",
            "src_ip": "185.220.101.47",
            "action": "login",
            "outcome": "success",
            "severity": "critical",
            "raw_log": '{"event":"login_success","user":"user-006","asset":"prod-db-pii-01","ip":"185.220.101.47"}',
            "triage_score": 10,
        }]

    print_results(rows[:5], title="High-Severity Events -- Triage Queue (Hot Memory)")
    top = rows[0]
    print_insight("Top alert", f"[{top['severity'].upper()}] {top['event_type']} by {top['user_id']} on {top['asset_id']}")
    print_insight("Source IP", top["src_ip"])
    print_insight("Triage score", f"{top['triage_score']}/10")

    timing = {"step": 1, "tier": "HOT", "description": "Detect and triage suspicious events",
              "elapsed_ms": elapsed, "rows_returned": len(rows)}
    return top, timing


# ---------------------------------------------------------------------------
# STEP 2 -- HOT MEMORY: Create case investigation workspace
# ---------------------------------------------------------------------------

def step2_create_case_workspace(client, alert: dict) -> tuple[str, dict]:
    case_id = f"CASE-{int(time.time())}"
    print_step(2, 7, f"Creating case investigation workspace [{case_id}]", "HOT")

    client.command("TRUNCATE TABLE IF EXISTS enterprise_memory.sec_case_workspace")

    sql_load = """
    INSERT INTO enterprise_memory.sec_case_workspace
    SELECT
        {case_id:String} AS case_id,
        event_id, ts, event_type, user_id, asset_id,
        src_ip, action, outcome, severity,
        now64() AS added_at
    FROM enterprise_memory.sec_events_stream
    WHERE (
        user_id  = {user_id:String}
        OR asset_id = {asset_id:String}
        OR src_ip   = {src_ip:String}
    )
    AND ts >= now() - INTERVAL 60 MINUTE
    """
    print_sql(sql_load)
    client.command(sql_load, parameters={
        "case_id": case_id,
        "user_id": alert["user_id"],
        "asset_id": alert["asset_id"],
        "src_ip": alert["src_ip"],
    })

    sql_summary = """
    SELECT
        user_id,
        asset_id,
        src_ip,
        count()                           AS total_events,
        countIf(severity = 'critical')    AS critical_events,
        countIf(outcome = 'success')      AS successful_actions,
        groupArray(DISTINCT event_type)   AS event_types,
        min(ts)                           AS first_seen,
        max(ts)                           AS last_seen
    FROM enterprise_memory.sec_case_workspace
    WHERE case_id = {case_id:String}
    GROUP BY user_id, asset_id, src_ip
    ORDER BY critical_events DESC
    """
    rows, elapsed = time_query(query_to_dicts, client, sql_summary, {"case_id": case_id})
    print_query_time(elapsed, "Memory Engine -- case correlation")

    print_results(rows, title=f"Case Workspace [{case_id}] -- Correlated Events")
    total = sum(r["total_events"] for r in rows) if rows else 0
    print_insight("Case created", f"{case_id} -- {total} events correlated")

    timing = {"step": 2, "tier": "HOT", "description": "Create case workspace + correlate events",
              "elapsed_ms": elapsed, "rows_returned": len(rows)}
    return case_id, timing


# ---------------------------------------------------------------------------
# STEP 3 -- GRAPH MEMORY: Query user-asset access graph
# ---------------------------------------------------------------------------

def step3_graph_access_context(client, alert: dict) -> tuple[dict, dict]:
    print_tier_banner("GRAPH")
    print_step(3, 7, f"Querying user-asset access graph for '{alert['user_id']}'", "GRAPH")

    console.print("    [dim]ClickHouse SQL JOINs on MergeTree access + assets tables[/dim]")

    sql_user = """
    SELECT
        user_id, username, department, role,
        round(risk_score, 2) AS risk_score,
        mfa_enabled
    FROM enterprise_memory.sec_users
    WHERE user_id = {uid:String}
    """
    print_sql(sql_user)
    user_rows, elapsed_user = time_query(query_to_dicts, client, sql_user, {"uid": alert["user_id"]})
    print_results(user_rows, title="User Profile (Graph Memory)")

    sql_access = """
    SELECT
        a.user_id,
        a.access_type,
        ast.asset_id,
        ast.hostname,
        ast.asset_type,
        ast.criticality,
        ast.data_class,
        ast.network_zone,
        a.granted_date,
        CASE WHEN ast.asset_id = {target_asset:String} THEN 'YES' ELSE 'no' END AS is_target_asset
    FROM enterprise_memory.sec_access a
    JOIN enterprise_memory.sec_assets ast ON ast.asset_id = a.asset_id
    WHERE a.user_id = {uid:String}
    ORDER BY is_target_asset DESC, ast.criticality DESC
    """
    print_sql(sql_access)
    access_rows, elapsed_access = time_query(query_to_dicts, client, sql_access, {
        "uid": alert["user_id"],
        "target_asset": alert["asset_id"],
    })
    print_results(access_rows, title="User Access Rights (Graph Memory)")

    sql_asset = """
    SELECT
        asset_id, hostname, asset_type, criticality,
        data_class, network_zone, os, owner_team
    FROM enterprise_memory.sec_assets
    WHERE asset_id = {aid:String}
    """
    asset_rows = query_to_dicts(client, sql_asset, {"aid": alert["asset_id"]})
    print_results(asset_rows, title="Target Asset Profile (Graph Memory)")

    total_elapsed = elapsed_user + elapsed_access
    print_query_time(total_elapsed, "SQL JOINs -- user-asset access graph")

    user = user_rows[0] if user_rows else {}
    asset = asset_rows[0] if asset_rows else {}
    has_access = any(r.get("asset_id") == alert["asset_id"] or r.get("is_target_asset") == "YES" for r in access_rows)

    print_insight("User role", f"{user.get('role')} in {user.get('department')}")
    print_insight("User risk score", str(user.get("risk_score")))
    print_insight("MFA enabled", "Yes" if user.get("mfa_enabled") else "NO -- HIGH RISK")
    print_insight("Asset criticality", asset.get("criticality", "unknown").upper())
    print_insight("Data classification", asset.get("data_class", "unknown"))
    print_insight("Has legitimate access", "Yes" if has_access else "NO -- SUSPICIOUS")

    timing = {"step": 3, "tier": "GRAPH", "description": "Query user-asset access graph (3 JOINs)",
              "elapsed_ms": total_elapsed, "rows_returned": len(access_rows)}
    return {
        "user": user,
        "asset": asset,
        "access_rights": access_rows,
        "has_legitimate_access": has_access,
    }, timing


# ---------------------------------------------------------------------------
# STEP 4 -- WARM MEMORY: Threat intelligence lookup
# ---------------------------------------------------------------------------

def step4_threat_intel_lookup(client, alert: dict) -> tuple[dict, dict]:
    print_tier_banner("WARM")
    print_step(4, 7, f"Checking IP '{alert['src_ip']}' against threat intelligence", "WARM")

    sql_exact = """
    SELECT
        indicator_id,
        indicator_type,
        indicator_val,
        threat_actor,
        campaign,
        ttps,
        round(confidence, 2) AS confidence,
        description,
        first_seen,
        last_seen
    FROM enterprise_memory.sec_threat_intel
    WHERE indicator_val = {ip:String}
      AND indicator_type = 'ip'
    """
    print_sql(sql_exact)
    exact_rows, elapsed_exact = time_query(query_to_dicts, client, sql_exact, {"ip": alert["src_ip"]})
    print_query_time(elapsed_exact, "MergeTree -- exact IoC lookup")
    print_results(exact_rows, title="Exact Threat Intel Match (Warm Memory)")

    query_text = f"IP address {alert['src_ip']} login attack credential theft financial sector"
    query_emb = embed(query_text)
    emb_str = "[" + ",".join(f"{v:.6f}" for v in query_emb) + "]"

    sql_vector = f"""
    SELECT
        threat_actor,
        campaign,
        ttps,
        round(confidence, 2) AS confidence,
        description,
        round(cosineDistance(embedding, {emb_str}), 4) AS similarity_distance
    FROM enterprise_memory.sec_threat_intel
    ORDER BY similarity_distance ASC
    LIMIT 3
    """
    print_sql(sql_vector[:400] + "\n  -- [embedding vector truncated]")
    vector_rows, elapsed_vec = time_query(query_to_dicts, client, sql_vector)
    print_query_time(elapsed_vec, "MergeTree + cosineDistance vector search")
    print_results(vector_rows, title="Similar Threat Profiles -- Vector Search (Warm Memory)")

    total_elapsed = elapsed_exact + elapsed_vec

    matched = exact_rows[0] if exact_rows else None
    if matched:
        print_insight("THREAT MATCH", f"IP matched to {matched['threat_actor']} -- {matched['campaign']}")
        print_insight("Confidence", f"{matched['confidence']:.0%}")
        print_insight("Known TTPs", ", ".join(matched["ttps"][:3]))
    else:
        print_insight("Exact match", "No exact IoC match -- checking similar profiles")

    timing = {"step": 4, "tier": "WARM", "description": "Threat intelligence lookup (exact + vector search)",
              "elapsed_ms": total_elapsed, "rows_returned": len(exact_rows) + len(vector_rows)}
    return {
        "exact_match": matched,
        "similar_profiles": vector_rows,
        "is_known_threat": matched is not None,
        "threat_actor": matched["threat_actor"] if matched else vector_rows[0]["threat_actor"] if vector_rows else "Unknown",
    }, timing


# ---------------------------------------------------------------------------
# STEP 5 -- WARM MEMORY: Vector search for similar historical incidents
# ---------------------------------------------------------------------------

def step5_historical_incident_search(client, alert: dict, threat_intel: dict) -> tuple[list[dict], dict]:
    print_step(5, 7, "Searching historical incidents for similar attack patterns", "WARM")

    query_text = (
        f"event_type={alert['event_type']} "
        f"user={alert['user_id']} asset={alert['asset_id']} "
        f"ip={alert['src_ip']} "
        f"threat_actor={threat_intel.get('threat_actor', 'unknown')} "
        f"outcome={alert['outcome']}"
    )
    console.print(f"    [dim]Embedding query: \"{query_text}\"[/dim]")

    query_emb = embed(query_text)
    emb_str = "[" + ",".join(f"{v:.6f}" for v in query_emb) + "]"

    sql = f"""
    SELECT
        incident_id,
        ts,
        incident_type,
        title,
        threat_actor,
        ttps,
        root_cause,
        response,
        outcome,
        severity,
        round(cosineDistance(embedding, {emb_str}), 4) AS similarity_distance
    FROM enterprise_memory.sec_historical_incidents
    ORDER BY similarity_distance ASC
    LIMIT 3
    """
    print_sql(sql[:500] + "\n  -- [embedding vector truncated]")

    rows, elapsed = time_query(query_to_dicts, client, sql)
    print_query_time(elapsed, "MergeTree + cosineDistance vector search")

    print_results(rows, title="Similar Historical Incidents (Warm Memory)")
    if rows:
        print_insight("Top match", rows[0]["title"])
        print_insight("Root cause", rows[0]["root_cause"][:80])
        print_insight("Past response", rows[0]["response"][:80])

    timing = {"step": 5, "tier": "WARM", "description": "Vector search for similar historical incidents",
              "elapsed_ms": elapsed, "rows_returned": len(rows)}
    return rows, timing


# ---------------------------------------------------------------------------
# STEP 6 -- HOT MEMORY: User behaviour anomaly scoring
# ---------------------------------------------------------------------------

def step6_behaviour_anomaly_score(client, alert: dict) -> tuple[dict, dict]:
    print_tier_banner("HOT")
    print_step(6, 7, f"Computing behaviour anomaly score for user '{alert['user_id']}'", "HOT")

    sql = """
    SELECT
        user_id,
        count()                                    AS total_events,
        countIf(outcome = 'success')               AS successful_actions,
        countIf(severity IN ('high','critical'))   AS high_sev_events,
        countIf(event_type = 'login_success')      AS logins,
        countIf(event_type = 'login_failed')       AS failed_logins,
        countIf(event_type = 'data_exfil')         AS exfil_attempts,
        countIf(event_type = 'privilege_esc')      AS priv_esc_attempts,
        groupArray(DISTINCT src_ip)                AS source_ips,
        groupArray(DISTINCT asset_id)              AS accessed_assets,
        round(
            (countIf(severity IN ('high','critical')) * 0.4 +
             countIf(outcome = 'success' AND severity IN ('high','critical')) * 0.3 +
             countIf(event_type IN ('data_exfil','privilege_esc','malware_detect')) * 0.3)
            / greatest(count(), 1),
            3
        ) AS behaviour_risk_score
    FROM enterprise_memory.sec_events_stream
    WHERE user_id = {uid:String}
       OR src_ip  = {ip:String}
    GROUP BY user_id
    """
    print_sql(sql)

    rows, elapsed = time_query(query_to_dicts, client, sql, {
        "uid": alert["user_id"],
        "ip": alert["src_ip"],
    })
    print_query_time(elapsed, "Memory Engine -- behaviour analytics")

    if not rows:
        rows = [{
            "user_id": alert["user_id"], "total_events": 1,
            "successful_actions": 1, "high_sev_events": 1,
            "logins": 1, "failed_logins": 0, "exfil_attempts": 0,
            "priv_esc_attempts": 0, "source_ips": [alert["src_ip"]],
            "accessed_assets": [alert["asset_id"]], "behaviour_risk_score": 0.7,
        }]

    print_results(rows, title="User Behaviour Profile (Hot Memory)")
    score = float(rows[0]["behaviour_risk_score"]) if rows else 0.0
    risk_label = "HIGH RISK" if score > 0.5 else "MEDIUM" if score > 0.2 else "LOW"
    print_insight("Behaviour risk score", f"{score:.2f}/1.0 ({risk_label})")
    print_insight("Unique source IPs", str(len(rows[0]["source_ips"])) if rows else "0")

    timing = {"step": 6, "tier": "HOT", "description": "Compute user behaviour anomaly score",
              "elapsed_ms": elapsed, "rows_returned": len(rows)}
    return rows[0] if rows else {}, timing


# ---------------------------------------------------------------------------
# STEP 7 -- RESULT: Synthesise threat context and response plan
# ---------------------------------------------------------------------------

def step7_synthesise_threat_context(
    alert: dict,
    case_id: str,
    access_context: dict,
    threat_intel: dict,
    similar_incidents: list[dict],
    behaviour: dict,
) -> dict:
    print_tier_banner("RESULT")
    print_step(7, 7, "Synthesising threat context and generating response plan", "RESULT")

    confidence_factors = {
        "known_threat_actor": 0.35 if threat_intel["is_known_threat"] else 0.0,
        "critical_asset": 0.20 if access_context["asset"].get("criticality") == "critical" else 0.10,
        "sensitive_data": 0.15 if access_context["asset"].get("data_class") in ("PII", "Financial") else 0.05,
        "no_mfa": 0.15 if not access_context["user"].get("mfa_enabled") else 0.0,
        "behaviour_anomaly": float(behaviour.get("behaviour_risk_score", 0)) * 0.15,
    }
    threat_confidence = min(sum(confidence_factors.values()), 1.0)

    response_actions = []
    if threat_confidence > 0.7:
        response_actions = [
            "IMMEDIATE: Suspend user session and revoke active tokens",
            "IMMEDIATE: Isolate target asset from network",
            "IMMEDIATE: Block source IP at perimeter firewall",
            "URGENT: Force password reset for affected user",
            "URGENT: Notify CISO and legal team (potential data breach)",
            "FOLLOW-UP: Forensic analysis of accessed data",
            "FOLLOW-UP: Review all access by this user in last 90 days",
        ]
    elif threat_confidence > 0.4:
        response_actions = [
            "HIGH: Require step-up MFA for user session",
            "HIGH: Alert SOC analyst for manual review",
            "MEDIUM: Increase monitoring on user and asset",
            "MEDIUM: Check for similar patterns from same IP range",
        ]
    else:
        response_actions = [
            "LOW: Log and monitor -- no immediate action required",
            "LOW: Add to watchlist for 24h monitoring",
        ]

    top_incident = similar_incidents[0] if similar_incidents else {}

    context = {
        "case_id": case_id,
        "detected_at": datetime.now().isoformat(),
        "alert": {
            "type": alert["event_type"],
            "user": alert["user_id"],
            "asset": alert["asset_id"],
            "src_ip": alert["src_ip"],
            "severity": alert["severity"],
        },
        "threat_assessment": {
            "confidence": round(threat_confidence, 2),
            "confidence_pct": f"{threat_confidence:.0%}",
            "threat_actor": threat_intel.get("threat_actor"),
            "is_known_threat": threat_intel["is_known_threat"],
            "confidence_breakdown": confidence_factors,
        },
        "asset_risk": {
            "criticality": access_context["asset"].get("criticality"),
            "data_class": access_context["asset"].get("data_class"),
            "hostname": access_context["asset"].get("hostname"),
        },
        "user_risk": {
            "role": access_context["user"].get("role"),
            "mfa_enabled": bool(access_context["user"].get("mfa_enabled")),
            "risk_score": access_context["user"].get("risk_score"),
            "behaviour_score": behaviour.get("behaviour_risk_score"),
        },
        "historical_precedent": {
            "similar_incidents": len(similar_incidents),
            "top_match": top_incident.get("title"),
            "recommended_response": top_incident.get("response", "")[:200],
        },
        "automated_response_plan": response_actions,
        "memory_sources_used": [
            "HOT: sec_events_stream (real-time triage)",
            "HOT: sec_case_workspace (correlated events)",
            "HOT: sec_events_stream (behaviour analytics)",
            "WARM: sec_threat_intel (IoC lookup + vector search)",
            "WARM: sec_historical_incidents (vector search)",
            "GRAPH: sec_users + sec_assets + sec_access (access graph)",
        ],
    }

    severity_colour = "red" if threat_confidence > 0.7 else "yellow" if threat_confidence > 0.4 else "green"

    console.print(Panel(
        "\n".join([
            f"[bold]Case ID:[/bold]              {context['case_id']}",
            f"[bold]Alert:[/bold]                [{alert['severity'].upper()}] {alert['event_type']} by {alert['user_id']}",
            f"[bold]Source IP:[/bold]            {alert['src_ip']}",
            f"[bold]Target Asset:[/bold]         {access_context['asset'].get('hostname')} ({access_context['asset'].get('criticality', '?').upper()} / {access_context['asset'].get('data_class', '?')})",
            f"[bold]Threat Actor:[/bold]         {context['threat_assessment']['threat_actor']}",
            f"[bold][{severity_colour}]Threat Confidence:[/bold][/{severity_colour}]   {context['threat_assessment']['confidence_pct']}",
            f"[bold]MFA Enabled:[/bold]          {'Yes' if context['user_risk']['mfa_enabled'] else 'NO -- CRITICAL GAP'}",
            f"[bold]Behaviour Score:[/bold]      {context['user_risk']['behaviour_score']}",
            f"[bold]Similar Incidents:[/bold]    {context['historical_precedent']['similar_incidents']} found",
            "",
            f"[bold]AUTOMATED RESPONSE PLAN:[/bold]",
            *[f"  - {action}" for action in response_actions[:4]],
        ]),
        title=f"[bold {severity_colour}]AI SOC Agent -- Threat Context Brief[/bold {severity_colour}]",
        border_style=severity_colour,
        padding=(1, 2),
    ))
    return context


# ---------------------------------------------------------------------------
# MAIN COOKBOOK RUNNER
# ---------------------------------------------------------------------------

def run() -> dict:
    print_header(
        "COOKBOOK 3: AI-Powered Autonomous Security Operations (SOC)",
        "AI SOC Agent -- Multi-Tier Context Retrieval Demo",
    )
    console.print(Rule("[dim]Architecture: Memory engine --> MergeTree+HNSW --> SQL JOINs[/dim]"))
    console.print()

    client = get_ch_client()
    tier_timings = []

    t_start = time.perf_counter()

    alert, t1 = step1_detect_triage(client)
    tier_timings.append(t1)

    case_id, t2 = step2_create_case_workspace(client, alert)
    tier_timings.append(t2)

    access_ctx, t3 = step3_graph_access_context(client, alert)
    tier_timings.append(t3)

    threat_intel, t4 = step4_threat_intel_lookup(client, alert)
    tier_timings.append(t4)

    similar, t5 = step5_historical_incident_search(client, alert, threat_intel)
    tier_timings.append(t5)

    behaviour, t6 = step6_behaviour_anomaly_score(client, alert)
    tier_timings.append(t6)

    context = step7_synthesise_threat_context(
        alert, case_id, access_ctx, threat_intel, similar, behaviour
    )

    total_ms = (time.perf_counter() - t_start) * 1000

    # Tier summary
    print_tier_summary(tier_timings)

    console.print(f"[bold green]> Full threat context retrieved in {total_ms:.0f}ms across all memory tiers[/bold green]\n")

    # Optional LLM synthesis
    if os.getenv("LLM_PROVIDER"):
        print_step(8, 8, "LLM synthesis -- generating threat brief", "RESULT")
        import json
        messages = [
            {"role": "system", "content": (
                "You are an expert SOC analyst. Analyze the threat context below "
                "and provide: (1) threat assessment, (2) confidence justification, "
                "(3) immediate containment actions, (4) investigation next steps."
            )},
            {"role": "user", "content": json.dumps(context, default=str)},
        ]
        analysis = generate(messages)
        console.print(Panel(analysis, title="[bold red]LLM Threat Brief[/bold red]", border_style="red"))
        context["llm_analysis"] = analysis

    return context


if __name__ == "__main__":
    run()
