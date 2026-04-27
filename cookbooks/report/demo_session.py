"""
cookbooks/report/demo_session.py
--------------------------------
Run a canonical investigation for each preset (AI SRE, AI NetOps,
AI SOC, Support Copilot) against the live ClickHouse stack, capture
every envelope + the agent's reasoning / insight lines, and write a
session JSON + a rendered HTML report.

Usage:

    # from the host (requires google-genai + clickhouse-connect installed)
    set -a && source cookbooks/.env && set +a
    CLICKHOUSE_HOST=localhost CLICKHOUSE_PORT=18123 \
        python3 cookbooks/report/demo_session.py --preset sre

    # or run all four at once
    CLICKHOUSE_HOST=localhost CLICKHOUSE_PORT=18123 \
        python3 cookbooks/report/demo_session.py --preset all

The reasoning + insight strings are hand-authored. They are the
analyst voice-over an LLM-driven agent would normally produce. We
hard-code them here so every report is reproducible.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import uuid
from datetime import datetime, timezone

# Make the cookbooks tree importable whether we run inside the demo-app
# container (where /app == cookbooks/) or from the project root.
_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))          # cookbooks/
sys.path.insert(0, str(_HERE.parent.parent.parent))   # project_final/

from mcp_server import server as srv_mod  # noqa: F401 (registers tools)
from mcp_server.server import (
    scan_live_stream,
    open_investigation,
    semantic_search,
    fetch_record,
    graph_traverse,
)
from mcp_server.conversation import (
    replay_session,
    recall_memory,
    save_memory,
)
from report.template import render


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# AI SRE Agent -- canonical svc-payments investigation
# ---------------------------------------------------------------------------

def run_sre_session(target_service: str = "svc-payments") -> dict:
    case_id = f"INC-{int(time.time())}"
    steps = []

    # Step 1: HOT scan
    e1 = scan_live_stream(domain="observability", filter=target_service, minutes=15, limit=20)
    top_err = (e1.get("insights") or {}).get("top_error") or "(no errors in window)"
    top_svc = (e1.get("insights") or {}).get("top_service") or target_service
    if e1.get("row_count", 0) > 0:
        insight1 = (
            f"{e1['row_count']} ERROR/CRITICAL events on {top_svc} in the last "
            f"15 minutes. Dominant signal: {top_err}."
        )
    else:
        insight1 = (
            "No live ERROR/CRITICAL in the 15-minute window. Agent records "
            "the absence honestly and falls back to historical context."
        )
    steps.append({
        "operation_label": "Live event stream",
        "reasoning": (
            f"Picked scan_live_stream because the user asked about '{target_service}' "
            "right now. HOT-tier tail of obs_events_stream is the cheapest first "
            "pass: sub-5ms read, no vector math, no joins. If this returns zero "
            "rows we acknowledge and lean on WARM history instead of guessing."
        ),
        "insight_text": insight1,
        "envelope": e1,
    })

    # Step 2: HOT workspace
    e2 = open_investigation(domain="observability", case_id=case_id)
    groups = (e2.get("insights") or {}).get("groups_summarised", 0)
    loaded = (e2.get("insights") or {}).get("events_loaded", 0)
    steps.append({
        "operation_label": "Investigation workspace",
        "reasoning": (
            "Picked open_investigation because we now have a case to track and "
            "several services may be implicated. Materialising a per-case Memory "
            "table scopes the rest of the sequence cleanly rather than "
            "re-filtering the stream on every step."
        ),
        "insight_text": (
            f"Workspace {case_id} materialised: {loaded} correlated events across "
            f"{groups} services. The investigation is now scoped to one case id."
        ),
        "envelope": e2,
    })

    # Step 3: WARM semantic search
    q = f"{target_service} database connection pool timeout errors"
    e3 = semantic_search(domain="observability", query=q, k=3)
    top = (e3.get("insights") or {}).get("top_match") or "(no match)"
    sim = (e3.get("insights") or {}).get("similarity_distance")
    root = (e3.get("insights") or {}).get("top_root_cause") or ""
    if e3.get("row_count", 0) > 0:
        insight3 = (
            f"Top historical match: \"{top}\" at cosineDistance {sim}. "
            f"Root cause on record: {root[:180]}."
        )
    else:
        insight3 = "No historical match. Agent will not guess; brief draws on live + graph only."
    steps.append({
        "operation_label": "Semantic search",
        "reasoning": (
            "Picked semantic_search because the user wants 'have we seen this "
            "before'. Vector similarity over obs_historical_incidents with "
            "HNSW + cosineDistance ranks the closest match. Keyword search would "
            "miss paraphrased titles."
        ),
        "insight_text": insight3,
        "envelope": e3,
    })

    # Step 4: WARM fetch_record
    top_id = None
    for row in (e3.get("rows_preview") or []):
        if row.get("incident_id"):
            top_id = row["incident_id"]
            break
    if top_id:
        e4 = fetch_record(domain="observability", kind="runbook", identifier=str(top_id))
        resolution = (e4.get("insights") or {}).get("resolution") or ""
        steps.append({
            "operation_label": "Record fetch by id",
            "reasoning": (
                "Picked fetch_record(kind='runbook') because we have a high-similarity "
                "match and need the concrete past resolution, not another vector "
                "ranking. Single-row PK lookup on incident_id."
            ),
            "insight_text": (
                f"Runbook for incident {top_id}. Past resolution: "
                f"{resolution[:200]}. Becomes the basis for the Recommended block."
            ),
            "envelope": e4,
        })

    # Step 5: GRAPH traverse
    e5 = graph_traverse(domain="observability", entity=target_service, max_hops=2)
    d = (e5.get("insights") or {}).get("direct_neighbours", 0)
    i = (e5.get("insights") or {}).get("indirect_neighbours", 0)
    crit = (e5.get("insights") or {}).get("critical_services_affected", 0)
    steps.append({
        "operation_label": "Graph traversal",
        "reasoning": (
            "Picked graph_traverse because we now need the blast radius to size "
            "the fix. obs_dependencies JOIN obs_services as a PK-joined graph is "
            "the right shape: two indexed joins + UNION ALL for 1-hop and 2-hop."
        ),
        "insight_text": (
            f"Blast radius: {d} direct + {i} indirect dependents, {crit} of them "
            "critical. Scope is real but contained."
        ),
        "envelope": e5,
    })

    return {
        "agent_preset": "ai-sre-agent",
        "agent_label": "AI SRE Agent (Observability)",
        "user_id": os.getenv("REPORT_USER", "u-maruthi"),
        "question": f"{target_service} is failing, walk me through it",
        "started_at": _now_iso(),
        "brute_force_baseline_bytes": 2_000_000_000,
        "brute_force_baseline_latency_s": 40,
        "steps": steps,
        "final_brief": {
            "fields": {
                "Trigger": f"{target_service} / {top_err}",
                "Blast radius": f"{d} direct / {i} indirect / {crit} critical",
                "Top historical match": f"{top} (cosineDistance {sim})",
                "Case id": case_id,
            },
            "recommended": (
                "Apply the resolution from the top historical match in the "
                "fetch_record step. Watch the direct dependents in Step 5 for "
                "cascading timeouts. Re-run scan_live_stream in ten minutes to "
                "confirm the fix."
            ),
        },
    }


# ---------------------------------------------------------------------------
# AI NetOps Agent -- canonical fault investigation on core-router-01
# ---------------------------------------------------------------------------

def run_netops_session(target_element: str = "core-router-01") -> dict:
    case_id = f"FAULT-{int(time.time())}"
    steps = []

    e1 = scan_live_stream(domain="telco", filter=target_element, minutes=15, limit=20)
    worst = (e1.get("insights") or {}).get("worst_element") or target_element
    status = (e1.get("insights") or {}).get("status") or "(unknown)"
    if e1.get("row_count", 0) > 0:
        insight1 = (
            f"{e1['row_count']} unhealthy element(s). Worst: {worst} "
            f"({status}). This is the element to investigate."
        )
    else:
        insight1 = "No unhealthy elements right now. Agent stops rather than fabricating a fault."
    steps.append({
        "operation_label": "Live element state",
        "reasoning": (
            f"Picked scan_live_stream because the user asked about '{target_element}' "
            "current state. telco_network_state is a Memory-engine live mirror of "
            "SNMP/telemetry pollers, sub-5ms to read, and we filter by element + "
            "health predicate so the scan stays tight."
        ),
        "insight_text": insight1,
        "envelope": e1,
    })

    e2 = open_investigation(domain="telco", case_id=case_id)
    loaded = (e2.get("insights") or {}).get("events_loaded", 0)
    groups = (e2.get("insights") or {}).get("groups_summarised", 0)
    steps.append({
        "operation_label": "Fault workspace",
        "reasoning": (
            "Picked open_investigation because several elements may be correlated "
            "and we want a single case_id to scope the rest. telco_fault_workspace "
            "gives us a grouped-by-element view in one query."
        ),
        "insight_text": (
            f"Fault workspace {case_id} materialised: {loaded} event(s) across "
            f"{groups} element grouping(s)."
        ),
        "envelope": e2,
    })

    q = f"{target_element} packet loss hardware failure degraded"
    e3 = semantic_search(domain="telco", query=q, k=3)
    top = (e3.get("insights") or {}).get("top_match") or "(no match)"
    sim = (e3.get("insights") or {}).get("similarity_distance")
    root = (e3.get("insights") or {}).get("top_root_cause") or ""
    if e3.get("row_count", 0) > 0:
        insight3 = (
            f"Top historical match: \"{top}\" at cosineDistance {sim}. "
            f"Root cause: {root[:180]}."
        )
    else:
        insight3 = "No historical match. Recommendation will stay narrowly scoped to live signals."
    steps.append({
        "operation_label": "Semantic search",
        "reasoning": (
            "Picked semantic_search because the user wants 'have we seen this fault "
            "shape before'. telco_network_events has a vector index so ranking by "
            "cosineDistance finds past faults with similar symptom text."
        ),
        "insight_text": insight3,
        "envelope": e3,
    })

    top_id = None
    for row in (e3.get("rows_preview") or []):
        if row.get("event_id"):
            top_id = row["event_id"]
            break
    if top_id:
        e4 = fetch_record(domain="telco", kind="runbook", identifier=str(top_id))
        resolution = (e4.get("insights") or {}).get("resolution") or ""
        steps.append({
            "operation_label": "Event fetch by id",
            "reasoning": (
                "Picked fetch_record(kind='runbook') to pull the full past-event "
                "row including resolution and customer-impact numbers. PK lookup "
                "on event_id; one row."
            ),
            "insight_text": (
                f"Past event fetched. Resolution: {resolution[:200]}. "
                "These are the concrete steps we would reapply."
            ),
            "envelope": e4,
        })

    e5 = graph_traverse(domain="telco", entity=target_element, max_hops=2)
    d = (e5.get("insights") or {}).get("direct_neighbours", 0)
    i = (e5.get("insights") or {}).get("indirect_neighbours", 0)
    steps.append({
        "operation_label": "Topology traversal",
        "reasoning": (
            "Picked graph_traverse to map downstream topology impact. "
            "telco_connections JOIN telco_elements walks fiber / backhaul links "
            "one and two hops out from the fault element."
        ),
        "insight_text": (
            f"Downstream topology: {d} direct + {i} indirect elements. "
            "This is what loses service if the fault escalates."
        ),
        "envelope": e5,
    })

    return {
        "agent_preset": "ai-netops-agent",
        "agent_label": "AI NetOps Agent (Telco)",
        "user_id": os.getenv("REPORT_USER", "u-maruthi"),
        "question": f"{target_element} is degraded, assess the impact and find a past fix",
        "started_at": _now_iso(),
        "brute_force_baseline_bytes": 1_500_000_000,
        "brute_force_baseline_latency_s": 25,
        "steps": steps,
        "final_brief": {
            "fields": {
                "Affected element": f"{worst} (status: {status})",
                "Topology impact": f"{d} direct + {i} indirect downstream",
                "Top historical match": f"{top} (cosineDistance {sim})",
                "Case id": case_id,
            },
            "recommended": (
                "Reapply the past resolution from the fetch_record step. If the "
                "fault cannot be hot-fixed, pre-reroute traffic away from the "
                "direct downstream elements in Step 5 before customer impact. "
                "Re-scan in 5 minutes to confirm health restored."
            ),
        },
    }


# ---------------------------------------------------------------------------
# AI SOC Agent -- canonical user-006 triage
# ---------------------------------------------------------------------------

def run_soc_session(target_user: str = "user-006") -> dict:
    case_id = f"CASE-{int(time.time())}"
    steps = []

    e1 = scan_live_stream(domain="cybersecurity", filter=target_user, minutes=60, limit=20)
    top_event = (e1.get("insights") or {}).get("top_event_type") or "(no events)"
    top_u = (e1.get("insights") or {}).get("top_user") or target_user
    if e1.get("row_count", 0) > 0:
        insight1 = (
            f"{e1['row_count']} high-severity event(s). Top: {top_event} on "
            f"{top_u}. This is the activity the agent will triage."
        )
    else:
        insight1 = "No high-severity activity on this user in the last hour. Agent stops rather than escalating nothing."
    steps.append({
        "operation_label": "Live security events",
        "reasoning": (
            f"Picked scan_live_stream because the user asked about '{target_user}' "
            "current behaviour. sec_events_stream is a Memory-engine live feed "
            "from SIEM/EDR/IAM; we filter by user_id + severity so the scan is "
            "tight."
        ),
        "insight_text": insight1,
        "envelope": e1,
    })

    e2 = open_investigation(domain="cybersecurity", case_id=case_id)
    loaded = (e2.get("insights") or {}).get("events_loaded", 0)
    steps.append({
        "operation_label": "Case workspace",
        "reasoning": (
            "Picked open_investigation because SOC triage needs a case_id that "
            "stays consistent across threat intel + historical + graph steps. "
            "sec_case_workspace gives us that scope."
        ),
        "insight_text": (
            f"Case {case_id} materialised: {loaded} correlated event(s)."
        ),
        "envelope": e2,
    })

    # Threat intel semantic lookup
    e3 = fetch_record(
        domain="cybersecurity", kind="threat_intel",
        query="suspicious login from tor exit node FIN6 credential stuffing", k=3,
    )
    top_intel = None
    if e3.get("rows_preview"):
        top_intel = e3["rows_preview"][0]
    steps.append({
        "operation_label": "Threat intel lookup",
        "reasoning": (
            "Picked fetch_record(kind='threat_intel') because before we chase "
            "history we want to know if the IoC or attack shape is already a "
            "known threat actor. Vector search over sec_threat_intel ranks by "
            "similarity to the current situation."
        ),
        "insight_text": (
            f"Top threat intel match: {top_intel.get('threat_actor') if top_intel else '(none)'} / "
            f"{top_intel.get('campaign') if top_intel else ''}, confidence "
            f"{top_intel.get('confidence') if top_intel else '-'}. Attribution is load-bearing."
            if top_intel else
            "No known threat intel match. Agent proceeds without attribution."
        ),
        "envelope": e3,
    })

    # Historical incidents
    q = "admin account compromised credential stuffing database access"
    e4 = semantic_search(domain="cybersecurity", query=q, k=3)
    top = (e4.get("insights") or {}).get("top_match") or "(no match)"
    sim = (e4.get("insights") or {}).get("similarity_distance")
    root = (e4.get("insights") or {}).get("top_root_cause") or ""
    steps.append({
        "operation_label": "Semantic search",
        "reasoning": (
            "Picked semantic_search because we want 'has this attack shape "
            "happened here before'. sec_historical_incidents is the persistent "
            "record; HNSW + cosineDistance ranks by symptom similarity."
        ),
        "insight_text": (
            f"Top past incident: \"{top}\" at cosineDistance {sim}. "
            f"Root cause: {root[:180]}."
        ),
        "envelope": e4,
    })

    e5 = graph_traverse(domain="cybersecurity", entity=target_user, max_hops=2)
    d = (e5.get("insights") or {}).get("direct_neighbours", 0)
    i = (e5.get("insights") or {}).get("indirect_neighbours", 0)
    crit = (e5.get("insights") or {}).get("critical_assets_reachable", 0)
    steps.append({
        "operation_label": "User-asset graph",
        "reasoning": (
            "Picked graph_traverse because we need the lateral-movement picture "
            "before we can recommend containment. sec_access JOIN sec_assets "
            "lists assets this user can reach; hop-2 surfaces other users who "
            "share those assets (pivot candidates)."
        ),
        "insight_text": (
            f"{d} asset(s) directly reachable by {target_user}, {crit} of them "
            f"critical. {i} other user(s) share access to those assets (lateral "
            "pivot risk)."
        ),
        "envelope": e5,
    })

    actor = (top_intel or {}).get("threat_actor") if top_intel else None
    campaign = (top_intel or {}).get("campaign") if top_intel else None
    return {
        "agent_preset": "ai-soc-agent",
        "agent_label": "AI SOC Agent (Cybersecurity)",
        "user_id": os.getenv("REPORT_USER", "u-maruthi"),
        "question": f"what is happening on {target_user}, full triage",
        "started_at": _now_iso(),
        "brute_force_baseline_bytes": 3_000_000_000,
        "brute_force_baseline_latency_s": 60,
        "steps": steps,
        "final_brief": {
            "fields": {
                "Attacker context": (
                    f"{actor or 'unknown actor'} / "
                    f"{campaign or 'no campaign match'}"
                ),
                "Triggering event": f"{top_event} on {top_u}",
                "Lateral movement risk": f"{d} direct assets / {crit} critical / {i} other users share access",
                "Top past incident": f"{top} (cosineDistance {sim})",
                "Case id": case_id,
            },
            "recommended": (
                "Contain: suspend the user session and rotate credentials. "
                "Respond: isolate the critical assets in Step 5 from the user "
                "until the investigation closes. Follow the resolution from the "
                "top past incident. Add the IP/IoC from the threat-intel step "
                "to the block list."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Support Copilot -- cross-session continuity
# ---------------------------------------------------------------------------

def run_support_copilot_session(user_id: str = "u-maruthi") -> dict:
    steps = []

    # Step 1: recall prior memory
    e1 = recall_memory(user_id=user_id, query="what have we discussed before", k=5)
    matches = (e1.get("insights") or {}).get("matches", 0)
    top_content = (e1.get("insights") or {}).get("top_content") or ""
    steps.append({
        "operation_label": "Semantic recall",
        "reasoning": (
            f"Picked recall_memory because the user asked for continuity with "
            f"'{user_id}'. agent_memory_long is the WARM store of every past "
            "turn + distilled fact for this user; HNSW cosineDistance ranks by "
            "semantic similarity to the query."
        ),
        "insight_text": (
            f"{matches} past turn(s) / fact(s) found. Top content: "
            f"{top_content[:200]}."
            if matches else
            "No prior memory for this user. Agent offers to persist going forward."
        ),
        "envelope": e1,
    })

    # Step 2: save a new preference
    fact = "user prefers ClickHouse EXPLAIN PIPELINE format for query plans"
    e2 = save_memory(
        user_id=user_id, fact=fact, agent_id="support-copilot",
        kind="semantic", importance=0.9,
    )
    steps.append({
        "operation_label": "Save to memory",
        "reasoning": (
            "Picked save_memory because the user stated a preference. We embed "
            "the distilled sentence, tag memory_type='semantic' with importance "
            "0.9, and write a single MergeTree row so future sessions can recall "
            "it."
        ),
        "insight_text": (
            f"Persisted: \"{fact}\" as semantic memory with importance 0.9. "
            "Will surface in any future recall for this user."
        ),
        "envelope": e2,
    })

    # Step 3: verify via recall
    e3 = recall_memory(user_id=user_id, query="EXPLAIN PIPELINE preference", k=3)
    top = None
    if e3.get("rows_preview"):
        top = e3["rows_preview"][0]
    verified = top and "EXPLAIN PIPELINE" in (top.get("content") or "")
    steps.append({
        "operation_label": "Semantic recall (verify)",
        "reasoning": (
            "Picked recall_memory a second time to close the loop: we just "
            "wrote a fact, we now verify it is retrievable via vector ranking. "
            "This proves the memory round-trip works, not just the write."
        ),
        "insight_text": (
            f"Verified. Top match: \"{(top or {}).get('content','')[:180]}\" "
            f"at cosineDistance {(top or {}).get('similarity_distance')}."
            if verified else
            "Write happened but recall did not surface it on top. Anomaly worth flagging."
        ),
        "envelope": e3,
    })

    return {
        "agent_preset": "support-copilot",
        "agent_label": "Support Copilot (Conversational Memory)",
        "user_id": user_id,
        "question": f"hi, I am {user_id}. What do you remember, and remember my EXPLAIN PIPELINE preference.",
        "started_at": _now_iso(),
        "brute_force_baseline_bytes": 800_000_000,
        "brute_force_baseline_latency_s": 15,
        "steps": steps,
        "final_brief": {
            "fields": {
                "User": user_id,
                "Prior memory loaded": f"{matches} past turn(s) matched",
                "New fact persisted": fact,
                "Round-trip verified": "yes" if verified else "no (flag)",
            },
            "recommended": (
                "Call recall_memory at the start of every new turn for this "
                "user so the agent appears continuous. Use save_memory whenever "
                "the user states a preference or decision. The WARM tier "
                "survives container restarts; HOT session scratch does not."
            ),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

PRESETS = {
    "sre":     (run_sre_session, "sre"),
    "netops":  (run_netops_session, "netops"),
    "soc":     (run_soc_session, "soc"),
    "support": (run_support_copilot_session, "support"),
}


def write(session: dict, slug: str, proj_root: pathlib.Path) -> None:
    out_json = proj_root / "docs" / "report" / f"example-{slug}-session.json"
    out_html = proj_root / "docs" / "report" / f"example-{slug}-report.html"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(session, indent=2, default=str))
    out_html.write_text(render(session))
    print(f"  {slug}: json={out_json.relative_to(proj_root)}  html={out_html.relative_to(proj_root)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run a canonical session for one or all presets.")
    ap.add_argument(
        "--preset",
        default="all",
        choices=list(PRESETS.keys()) + ["all"],
        help="Which preset to run. 'all' runs sre, netops, soc, support in sequence.",
    )
    args = ap.parse_args()

    proj_root = pathlib.Path(__file__).resolve().parent.parent.parent

    presets = list(PRESETS.keys()) if args.preset == "all" else [args.preset]
    print(f"Generating report(s) for: {', '.join(presets)}")

    for p in presets:
        runner, slug = PRESETS[p]
        print(f"\n[{p}] running session ...")
        session = runner()
        write(session, slug, proj_root)

    # Also write a legacy example-session.json / example-report.html that
    # always points at the SRE session, so older links keep working.
    if "sre" in presets:
        sre_json = proj_root / "docs" / "report" / "example-sre-session.json"
        sre_html = proj_root / "docs" / "report" / "example-sre-report.html"
        (proj_root / "docs" / "report" / "example-session.json").write_text(sre_json.read_text())
        (proj_root / "docs" / "report" / "example-report.html").write_text(sre_html.read_text())

    print("\ndone.")


if __name__ == "__main__":
    main()
