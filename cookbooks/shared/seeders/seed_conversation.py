"""
shared/seeders/seed_conversation.py
-----------------------------------
Seed synthetic past agent-user conversation turns, distilled semantic
memories, and generic knowledge-base articles for the conversation
memory layer (agent_memory_hot, agent_memory_long, knowledge_base).

Run standalone with:
    python -m shared.seeders.seed_conversation

Or let seed_all.py call seed_conversation() as part of the full seed.
"""

from __future__ import annotations

import os
import sys
import uuid
import random
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from shared.client import get_ch_client, embed, console


rng = random.Random(1337)


# ---------------------------------------------------------------------------
# Synthetic users and sessions
# ---------------------------------------------------------------------------

USERS = [
    {
        "user_id": "u-maruthi",
        "agent_id": "support-copilot",
        "persona": "platform engineer, cares about svc-payments and Postgres tuning",
        "preferences": [
            "user prefers Gemini 2.0 Flash for day-to-day reasoning",
            "user is on-call for svc-payments and svc-orders weekdays",
            "user accepts an SLO of 99.9% for svc-payments checkout path",
            "user has asked to be paged on CRITICAL only, not ERROR",
        ],
    },
    {
        "user_id": "u-nicholas",
        "agent_id": "support-copilot",
        "persona": "SRE lead, focused on blast radius and dependency health",
        "preferences": [
            "user prefers concise bullet-pointed summaries over prose",
            "user owns the svc-auth and svc-api-gateway services",
            "user opted out of verbose SQL echoes in tool output",
        ],
    },
    {
        "user_id": "u-priya",
        "agent_id": "support-copilot",
        "persona": "data platform engineer, Postgres + ClickHouse tuning",
        "preferences": [
            "user prefers ClickHouse query plans in EXPLAIN PIPELINE form",
            "user tolerates up to 500ms p99 latency on analytics queries",
            "user dislikes being asked to rephrase, just proceed",
        ],
    },
]


# 10 primary sessions across the last 30 days, one topic per session.
# Each topic averages ~7 turns, so primary sessions give ~73 HOT rows.
# We also replay each topic a second time for a different user with a
# different session id and offset days, which lands us around ~200
# total HOT turns. This keeps the content coherent while bulking out
# volume so cosineDistance ranking has something to chew on.
SESSIONS = [
    # (session_id, user_id, days_ago, topic_key)
    ("sess-001", "u-maruthi",  28, "svc_payments_conn_pool"),
    ("sess-002", "u-maruthi",  24, "payments_tls_cert"),
    ("sess-003", "u-nicholas", 22, "auth_jwt_latency"),
    ("sess-004", "u-priya",    19, "ch_query_tuning"),
    ("sess-005", "u-maruthi",  15, "followup_conn_pool"),
    ("sess-006", "u-nicholas", 13, "blast_radius_search"),
    ("sess-007", "u-priya",    10, "pg_vacuum_question"),
    ("sess-008", "u-maruthi",   6, "slo_discussion"),
    ("sess-009", "u-nicholas",  4, "followup_auth_cache"),
    ("sess-010", "u-priya",     1, "followup_vacuum"),
]

# Replay sessions: same topic, different user and session id, so the
# WARM vector search has cross-user corroboration and HOT volume hits
# the ~200 turn target.
REPLAY_SESSIONS = [
    ("sess-101", "u-nicholas", 27, "svc_payments_conn_pool"),
    ("sess-102", "u-priya",    25, "payments_tls_cert"),
    ("sess-103", "u-maruthi",  21, "auth_jwt_latency"),
    ("sess-104", "u-nicholas", 18, "ch_query_tuning"),
    ("sess-105", "u-priya",    14, "followup_conn_pool"),
    ("sess-106", "u-maruthi",  12, "blast_radius_search"),
    ("sess-107", "u-nicholas",  9, "pg_vacuum_question"),
    ("sess-108", "u-priya",     5, "slo_discussion"),
    ("sess-109", "u-maruthi",   3, "followup_auth_cache"),
    ("sess-110", "u-nicholas",  2, "followup_vacuum"),
    # A few extra to get HOT volume near ~200 turns
    ("sess-201", "u-priya",    17, "svc_payments_conn_pool"),
    ("sess-202", "u-maruthi",  11, "auth_jwt_latency"),
    ("sess-203", "u-nicholas",  7, "ch_query_tuning"),
    ("sess-204", "u-priya",     3, "blast_radius_search"),
    ("sess-205", "u-maruthi",   1, "slo_discussion"),
]


# Topic templates: each one is a list of (role, content) turns.
# Keep them realistic but short; the embedder handles deterministic
# fallback so the semantic relationships come out even without an API.
TOPICS = {
    "svc_payments_conn_pool": [
        ("user",      "I am seeing svc-payments throw DB_TIMEOUT across the board, started about 10 minutes ago."),
        ("assistant", "Pulling live errors from the HOT stream for svc-payments and checking for pool exhaustion patterns."),
        ("tool",      "search_events returned 34 ERROR rows on svc-payments, mostly DB_TIMEOUT from pod svc-payments-pod-3."),
        ("assistant", "This looks like a connection pool exhaustion: similar incidents resolved by raising pool size from 50 to 200."),
        ("user",      "Can you confirm with the historical incidents? I do not want to just copy a runbook blindly."),
        ("assistant", "Running a WARM vector search over obs_historical_incidents for 'svc-payments database connection pool exhaustion'."),
        ("tool",      "Top match: 'Payment service database connection pool exhaustion' (P1, 45 min, raised pool to 200)."),
        ("assistant", "Recommended: raise max_connections to 200, add a circuit breaker, fix the retry-loop connection leak."),
        ("user",      "Good. Let us make that change but please also flag it to nicholas."),
    ],
    "payments_tls_cert": [
        ("user",      "svc-payments returning 503s again, clients reporting SSL handshake failures."),
        ("assistant", "Checking HOT stream for TLS_ERR rows on svc-payments."),
        ("tool",      "search_events returned 12 CRITICAL events, all error_code=TLS_ERR."),
        ("assistant", "Most likely cause is certificate expiry. The past incident on cert renewal matches this signature almost exactly."),
        ("user",      "Fine, rotate the cert. Also please add a reminder so we do not hit this again."),
        ("assistant", "Adding a 30-day pre-expiry alert via cert-manager. Documenting the fix in the knowledge base."),
        ("user",      "Thanks. Close the case, I will follow up tomorrow."),
    ],
    "auth_jwt_latency": [
        ("user",      "svc-auth p99 jumped to 2 seconds. api-gateway is timing out cascading into orders."),
        ("assistant", "Scanning HOT for WARN and ERROR on svc-auth in the last 15 minutes."),
        ("tool",      "search_events shows elevated JWT validation latency, cache hit rate dropped below 50%."),
        ("assistant", "WARM vector search suggests the Redis public-key cache expired simultaneously and every pod is re-fetching."),
        ("user",      "Yeah this is the mass key-fetch pattern we hit before. What did we do last time?"),
        ("assistant", "Staggered cache TTL and added an in-process L1 key cache with 5 minute TTL. I can draft the config change."),
        ("user",      "Please do. And remember I own svc-auth, so page me directly for anything CRITICAL there."),
        ("assistant", "Noted. I am recording that preference against your user id for future sessions."),
    ],
    "ch_query_tuning": [
        ("user",      "A ClickHouse query I run daily has regressed from 120ms to 900ms. Can you take a look?"),
        ("assistant", "Happy to help. Can you share the query and the table schema, or just the table name?"),
        ("user",      "It is the analytics rollup over obs_historical_incidents grouped by service."),
        ("assistant", "Common causes for a 10x regression: primary key mismatch, missing projection, or a stale granule skip index."),
        ("tool",      "EXPLAIN PIPELINE shows full-table scan without the affected_services primary-key prefix."),
        ("assistant", "The query is not using the ORDER BY key. I suggest adding a projection or switching the GROUP BY order."),
        ("user",      "EXPLAIN PIPELINE is exactly the format I wanted. Keep using that format for me going forward."),
        ("assistant", "Got it: EXPLAIN PIPELINE preference recorded for your user id."),
        ("user",      "Also please remember my latency tolerance is 500ms p99 on these analytics queries."),
    ],
    "followup_conn_pool": [
        ("user",      "Following up on the svc-payments pool issue from a couple weeks ago. Has it recurred?"),
        ("assistant", "Let me check HOT for recent DB_TIMEOUT on svc-payments and recall our prior session."),
        ("tool",      "get_conversation_history found your sess-001 transcript: raised pool from 50 to 200, added circuit breaker."),
        ("assistant", "No recurrences in the last 30 days. The pool fix held. Circuit breaker has tripped twice, both times harmless."),
        ("user",      "Good, thanks. Please keep that SLO of 99.9 in mind, not 99.95."),
        ("assistant", "Confirming SLO 99.9 for svc-payments, no change from your previous instruction."),
    ],
    "blast_radius_search": [
        ("user",      "If svc-auth goes down what else breaks? I want the dependency picture before our DR drill."),
        ("assistant", "Running GRAPH traversal on obs_dependencies for svc-auth at 2 hops."),
        ("tool",      "find_related_entities: direct dependents svc-api-gateway and svc-payments, indirect svc-orders and svc-search."),
        ("assistant", "Four services in blast radius, three are critical. Your api-gateway and orders are both in the path."),
        ("user",      "That matches my mental model. Can you store that as a cached blast radius summary?"),
        ("assistant", "Recorded: svc-auth blast radius = api-gateway, payments, orders, search. Tagged for fast recall."),
        ("user",      "Perfect. Remember I prefer bullet points, not paragraphs."),
    ],
    "pg_vacuum_question": [
        ("user",      "Postgres autovacuum seems slow on our payments_txn table. Any ideas?"),
        ("assistant", "Is the table append-heavy or also update heavy? Autovacuum behaviour is very different by workload."),
        ("user",      "Append-heavy, we keep 90 days then TTL out."),
        ("assistant", "For append-heavy, lower autovacuum_vacuum_insert_scale_factor and bump workers. Consider partitioning by month."),
        ("tool",      "get_conversation_history found no prior Postgres context for this user, starting fresh."),
        ("user",      "Partitioning by month is on my roadmap already. Focus on the autovacuum tuning for now."),
        ("assistant", "Proposed: autovacuum_vacuum_insert_scale_factor 0.02, autovacuum_max_workers 6, autovacuum_naptime 30s."),
        ("user",      "Good, I will apply that in staging tomorrow."),
    ],
    "slo_discussion": [
        ("user",      "We are negotiating an SLO for svc-payments with the business side. What have we historically hit?"),
        ("assistant", "Pulling historical incidents for svc-payments and converting duration_min to an availability window."),
        ("tool",      "semantic_search returned three P1 incidents totalling 53 minutes over the last 9 months."),
        ("assistant", "That is roughly 99.988% availability actual, so 99.9% is safe and 99.95% is achievable with the pool fix holding."),
        ("user",      "Let us commit to 99.9 for now. I do not want to overpromise."),
        ("assistant", "Recorded: target SLO 99.9 for svc-payments checkout path, effective today."),
    ],
    "followup_auth_cache": [
        ("user",      "Did the svc-auth L1 cache change make it into prod?"),
        ("assistant", "Pulling the prior session and checking deployment status on the recent changes."),
        ("tool",      "get_conversation_history: sess-003 covered staggered TTL plus 5-minute in-process cache."),
        ("assistant", "Deploy landed 8 days ago. p99 now at 180ms steady, cache hit rate 94%. The issue has not recurred."),
        ("user",      "Excellent. Anything else you saw drifting in svc-auth?"),
        ("assistant", "One warning: the Redis primary has CPU at 68% average. Not urgent but worth a capacity review next sprint."),
        ("user",      "Queue that as a follow-up item but do not page me about it."),
    ],
    "followup_vacuum": [
        ("user",      "Autovacuum tuning went in last week. Table bloat looks better but not gone."),
        ("assistant", "How much bloat reduction are we seeing, and how often is autovacuum firing now?"),
        ("user",      "About 30% reduction in dead tuples, autovacuum fires roughly twice a day."),
        ("assistant", "That is the right shape but not aggressive enough. You can drop insert_scale_factor to 0.01 on that specific table."),
        ("tool",      "get_conversation_history found sess-007 where we set 0.02, so this is a direct tightening."),
        ("assistant", "Recommended change: ALTER TABLE payments_txn SET (autovacuum_vacuum_insert_scale_factor = 0.01)."),
    ],
}


# ---------------------------------------------------------------------------
# Knowledge base articles
# ---------------------------------------------------------------------------

KB_ARTICLES = [
    ("runbook", "Handling svc-payments DB connection pool exhaustion",
     "When svc-payments reports DB_TIMEOUT on more than 10% of requests within 5 minutes, suspect connection pool exhaustion. Raise max_connections to 200, enable the circuit breaker on payment retries, and verify no connection leaks in the retry loop."),
    ("runbook", "svc-auth JWT validation latency spike playbook",
     "If svc-auth p99 latency exceeds 1 second, check Redis cache hit rate for JWT public keys. If hit rate is below 80%, stagger cache TTLs and enable the 5-minute in-process L1 key cache."),
    ("runbook", "TLS certificate renewal automation",
     "All production services must use cert-manager with a 30-day pre-expiry alert. Weekly CI validates that no certificate expires within 45 days."),
    ("runbook", "Kafka consumer lag response",
     "When Kafka consumer lag exceeds 100k messages, scale consumer replicas, inspect downstream provider health, and tune rebalance.timeout.ms upward in steps of 30 seconds."),
    ("runbook", "Inventory deadlock during flash sales",
     "Use optimistic locking on stock updates and a Redis distributed lock as a second line of defense. Expect <1% retry rate at peak."),
    ("policy", "On-call paging preferences",
     "Users configure paging thresholds per service. The default is ERROR, but users may opt to receive only CRITICAL. Store preferences in agent_memory_long with memory_type='semantic'."),
    ("policy", "SLO definitions and rollout rules",
     "SLOs are expressed at three nines or four nines. Commit cautiously: it is easier to tighten later than to loosen a public commitment."),
    ("policy", "Incident severity matrix",
     "P1 is customer-facing outage. P2 is partial degradation. P3 is internal issue only. P4 is a backlog item."),
    ("policy", "Change management for database parameter changes",
     "Database parameter changes require a 24-hour bake period in staging, a rollback plan, and a recorded peer review."),
    ("clickhouse", "ClickHouse: when to add a projection",
     "Projections accelerate GROUP BY queries whose key does not match the table ORDER BY. Add a projection when a daily query takes over 500ms and is not using the primary key."),
    ("clickhouse", "ClickHouse: HNSW index basics",
     "HNSW indexes on Array(Float32) columns accelerate approximate nearest neighbour search. Build with metric='cosine' for text embeddings. Requires allow_experimental_vector_similarity_index=1."),
    ("clickhouse", "ClickHouse: Memory engine best practices",
     "Memory engine tables are ideal for per-session scratchpads and live telemetry. Data is lost on restart, so pair with a MergeTree persistence path for anything that must survive."),
    ("clickhouse", "ClickHouse: MergeTree partitioning by month",
     "PARTITION BY toYYYYMM(ts) is the default choice for time-series MergeTree tables. Avoid over-partitioning: one partition per day is almost always too many."),
    ("clickhouse", "ClickHouse: TTL to cold storage",
     "Use TTL with TO VOLUME 'cold' to move rows older than 90 days to object storage. This is the single biggest cost reduction lever in ClickHouse Cloud."),
    ("postgres", "Postgres autovacuum tuning for append-heavy tables",
     "Lower autovacuum_vacuum_insert_scale_factor to 0.02 or 0.01 on append-heavy tables. Bump autovacuum_max_workers and consider per-table overrides via ALTER TABLE."),
    ("postgres", "Postgres vacuuming during high write volume",
     "Expect a steady-state dead tuple reduction of 30-60% after tuning. If you see no reduction, check for long-running transactions blocking vacuum."),
    ("postgres", "Postgres connection pool sizing",
     "Use pgbouncer in transaction mode. Size the pool to roughly 2x the number of application workers, then tune with real load data."),
    ("k8s", "Kubernetes OOMKilled triage",
     "Check memory requests vs limits first. OOMKilled with requests == limits often indicates a memory leak rather than under-provisioning."),
    ("k8s", "Kubernetes pod restart loops",
     "Restart loops in production are almost always configuration drift. Diff the deployment against the last known-good revision before deeper debugging."),
    ("k8s", "Kubernetes horizontal pod autoscaler",
     "Set the HPA target to 60-70% CPU for services with bursty load. Avoid scaling on memory unless the workload is predictable."),
    ("security", "Credential stuffing response",
     "Suspend the compromised account, rotate downstream credentials, enforce MFA, and review access logs for lateral movement."),
    ("security", "Ransomware initial containment",
     "Isolate affected hosts at the network layer before anything else. Do not wipe immediately: image first for forensics, then restore from last-known-clean backup."),
    ("security", "Spear phishing and BEC defence",
     "Deploy DMARC and DKIM on all mail-sending domains. Train executives specifically on CEO-impersonation wire-transfer patterns."),
    ("observability", "What makes a good incident post-mortem",
     "A post-mortem covers trigger, blast radius, timeline, root cause, resolution, and prevention items. Prevention items must have owners and target dates."),
    ("observability", "SLI to SLO mapping",
     "Pick at most three SLIs per service: latency, availability, and correctness. Everything else is a diagnostic metric, not an SLO driver."),
    ("observability", "Golden signals vs RED vs USE",
     "Golden signals (Google), RED (Weave), and USE (Brendan Gregg) all converge on roughly the same set. Pick one naming convention per org and stick to it."),
    ("telco", "Base station capacity breach playbook",
     "When a base station exceeds 90% capacity for 15 minutes, activate dynamic spectrum sharing and dispatch a temporary small cell if the event is predictable."),
    ("telco", "Fiber cut recovery",
     "Trigger path-reroute within 5 minutes of detection, dispatch a splice team, and communicate ETA to enterprise customers within 15 minutes."),
    ("telco", "BGP flap diagnostics",
     "BGP flaps after upstream maintenance are usually MTU mismatches. Confirm MTU on both sides before escalating to the upstream provider."),
    ("process", "Handing work between sessions",
     "Every agent should call get_conversation_history at session start, then add_memory on any new preference or decision."),
]


# ---------------------------------------------------------------------------
# Seeder entrypoint
# ---------------------------------------------------------------------------

def _truncate(client, table: str) -> None:
    client.command(f"TRUNCATE TABLE IF EXISTS enterprise_memory.{table}")


def seed_conversation(client) -> None:
    """Idempotently seed agent_memory_hot, agent_memory_long, and knowledge_base."""
    console.print("[cyan]Seeding Agent Conversation Memory...[/cyan]")

    # Reset tables so reruns are clean.
    _truncate(client, "agent_memory_hot")
    _truncate(client, "agent_memory_long")
    _truncate(client, "knowledge_base")

    now = datetime.now()

    # -- Build all turn rows from the topic templates --------------------
    hot_rows: list[tuple] = []
    long_rows: list[tuple] = []

    episodic_count = 0
    all_sessions = list(SESSIONS) + list(REPLAY_SESSIONS)
    for session_id, user_id, days_ago, topic_key in all_sessions:
        topic = TOPICS[topic_key]
        agent_id = next(u["agent_id"] for u in USERS if u["user_id"] == user_id)
        session_base = now - timedelta(days=days_ago)
        for turn_id, (role, content) in enumerate(topic, start=1):
            ts = session_base + timedelta(seconds=turn_id * 30)
            tool_name = "memory_lookup" if role == "tool" else ""
            metadata = f'{{"topic":"{topic_key}"}}'

            hot_rows.append((session_id, turn_id, role, content, tool_name, metadata, ts))

            # Persist all turns (episodic) to WARM with embeddings.
            emb = embed(content)
            long_rows.append((
                str(uuid.uuid4()), user_id, agent_id, session_id, turn_id,
                role, content, emb, "episodic", 0.4, ts,
            ))
            episodic_count += 1

    # -- Add distilled "semantic" preferences per user -------------------
    semantic_count = 0
    for user in USERS:
        for pref in user["preferences"]:
            ts = now - timedelta(days=rng.randint(2, 25), hours=rng.randint(0, 23))
            emb = embed(pref)
            long_rows.append((
                str(uuid.uuid4()), user["user_id"], user["agent_id"],
                "",  # no specific session, it is a standing preference
                0,
                "assistant",
                pref,
                emb,
                "semantic",
                0.9,
                ts,
            ))
            semantic_count += 1

    # -- Insert into HOT and LONG ---------------------------------------
    client.insert(
        "enterprise_memory.agent_memory_hot",
        hot_rows,
        column_names=["session_id", "turn_id", "role", "content", "tool_name", "metadata", "ts"],
    )
    client.insert(
        "enterprise_memory.agent_memory_long",
        long_rows,
        column_names=[
            "memory_id", "user_id", "agent_id", "session_id", "turn_id",
            "role", "content", "content_embedding", "memory_type", "importance", "ts",
        ],
    )

    # -- Seed knowledge base --------------------------------------------
    kb_rows: list[tuple] = []
    for category, title, content in KB_ARTICLES:
        emb = embed(f"{title}. {content}")
        created = now - timedelta(days=rng.randint(30, 400))
        updated = created + timedelta(days=rng.randint(0, 20))
        tags = [category, title.split()[0].lower()]
        kb_rows.append((
            str(uuid.uuid4()), title, content, emb,
            category, tags, created, updated, rng.randint(0, 120),
        ))
    client.insert(
        "enterprise_memory.knowledge_base",
        kb_rows,
        column_names=[
            "article_id", "title", "content", "content_embedding",
            "category", "tags", "created_at", "updated_at", "access_count",
        ],
    )

    total_sessions = len(SESSIONS) + len(REPLAY_SESSIONS)
    console.print(
        f"  [green]>[/green] Conversation memory: "
        f"{len(hot_rows)} HOT turns across {total_sessions} sessions, "
        f"{episodic_count} episodic + {semantic_count} semantic WARM rows "
        f"for {len(USERS)} users, {len(kb_rows)} knowledge base articles."
    )


def main():
    console.print("\n[bold cyan]=== Agent Conversation Memory Seeder ===[/bold cyan]\n")
    client = get_ch_client()
    seed_conversation(client)
    console.print("\n[bold green]> Conversation memory seeded.[/bold green]\n")


if __name__ == "__main__":
    main()
