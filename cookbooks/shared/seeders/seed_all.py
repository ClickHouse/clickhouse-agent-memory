"""
shared/seeders/seed_all.py
──────────────────────────
Seeds all three use cases with realistic synthetic data.
Run once before executing any cookbook demo.

Usage:
    python -m shared.seeders.seed_all
"""

import sys
import os
import uuid
import random
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from shared.client import get_ch_client, embed, console

rng = random.Random(42)


# ─────────────────────────────────────────────────────────────────────────────
# USE CASE 1 — OBSERVABILITY
# ─────────────────────────────────────────────────────────────────────────────

SERVICES = [
    ("svc-api-gateway",   "Platform",  "Go",     "critical"),
    ("svc-auth",          "Security",  "Python", "critical"),
    ("svc-orders",        "Commerce",  "Java",   "high"),
    ("svc-payments",      "Finance",   "Java",   "critical"),
    ("svc-inventory",     "Commerce",  "Python", "high"),
    ("svc-notifications", "Platform",  "Node",   "medium"),
    ("svc-analytics",     "Data",      "Python", "medium"),
    ("svc-search",        "Commerce",  "Rust",   "high"),
    ("svc-users",         "Platform",  "Go",     "high"),
    ("svc-recommendations","Data",     "Python", "medium"),
]

DEPS = [
    ("svc-api-gateway", "svc-auth",          "sync",  12.0),
    ("svc-api-gateway", "svc-orders",        "sync",  45.0),
    ("svc-api-gateway", "svc-search",        "sync",  30.0),
    ("svc-api-gateway", "svc-users",         "sync",  18.0),
    ("svc-orders",      "svc-payments",      "sync",  80.0),
    ("svc-orders",      "svc-inventory",     "sync",  25.0),
    ("svc-orders",      "svc-notifications", "async", 5.0),
    ("svc-payments",    "svc-auth",          "sync",  10.0),
    ("svc-search",      "svc-recommendations","async", 15.0),
    ("svc-analytics",   "svc-orders",        "async", 8.0),
    ("svc-analytics",   "svc-users",         "async", 6.0),
]

OBS_INCIDENTS = [
    {
        "title": "Payment service database connection pool exhaustion",
        "description": "svc-payments experienced cascading failures due to database connection pool exhaustion under high load",
        "affected_services": ["svc-payments", "svc-orders"],
        "root_cause": "Database connection pool limit set too low; connection leak in payment retry logic",
        "resolution": "Increased pool size from 50 to 200, fixed connection leak, added circuit breaker",
        "severity": "P1", "duration_min": 45,
    },
    {
        "title": "Auth service JWT validation latency spike",
        "description": "svc-auth JWT validation latency spiked to 2000ms causing timeouts across all services",
        "affected_services": ["svc-auth", "svc-api-gateway", "svc-orders"],
        "root_cause": "Redis cache for public keys expired simultaneously, causing mass key fetches",
        "resolution": "Staggered cache TTL, added local in-process key cache with 5 min TTL",
        "severity": "P1", "duration_min": 22,
    },
    {
        "title": "Search service memory leak causing OOM restarts",
        "description": "svc-search pods restarting every 2 hours due to memory leak in Elasticsearch client",
        "affected_services": ["svc-search", "svc-api-gateway"],
        "root_cause": "Elasticsearch scroll API results not being cleared, accumulating in heap",
        "resolution": "Fixed scroll cleanup, added memory limit alerts at 80% threshold",
        "severity": "P2", "duration_min": 180,
    },
    {
        "title": "Inventory service deadlock during flash sale",
        "description": "svc-inventory deadlocked under concurrent write load during flash sale event",
        "affected_services": ["svc-inventory", "svc-orders"],
        "root_cause": "Optimistic locking not implemented; concurrent stock updates caused deadlock",
        "resolution": "Implemented optimistic locking with retry, added Redis-based distributed lock",
        "severity": "P2", "duration_min": 35,
    },
    {
        "title": "Notification service Kafka consumer lag spike",
        "description": "svc-notifications Kafka consumer lag grew to 500k messages causing delayed order confirmations",
        "affected_services": ["svc-notifications"],
        "root_cause": "Consumer group rebalance triggered by deployment, combined with slow SMTP provider",
        "resolution": "Increased consumer replicas, added SMTP provider failover, tuned rebalance timeout",
        "severity": "P3", "duration_min": 90,
    },
    {
        "title": "API gateway rate limiter misconfiguration",
        "description": "svc-api-gateway rate limiter applied global limit per IP instead of per user, blocking legitimate traffic",
        "affected_services": ["svc-api-gateway"],
        "root_cause": "Config change deployed without review; rate limit key set to IP instead of user_id",
        "resolution": "Rolled back config, added config validation tests, implemented canary deployment",
        "severity": "P2", "duration_min": 15,
    },
    {
        "title": "Analytics service Spark job OOM during ETL",
        "description": "svc-analytics Spark ETL job failed with OOM error processing large daily batch",
        "affected_services": ["svc-analytics"],
        "root_cause": "Data volume grew 3x without adjusting Spark executor memory settings",
        "resolution": "Increased executor memory, added dynamic allocation, partitioned large datasets",
        "severity": "P3", "duration_min": 120,
    },
    {
        "title": "Payment service TLS certificate expiry causing 503s",
        "description": "svc-payments TLS certificate expired causing all payment requests to fail with SSL errors",
        "affected_services": ["svc-payments", "svc-orders"],
        "root_cause": "Certificate renewal automation failed silently 30 days prior; no alerting on expiry",
        "resolution": "Renewed certificate, implemented cert-manager with 30-day pre-expiry alerts",
        "severity": "P1", "duration_min": 8,
    },
]

LOG_MESSAGES = {
    "ERROR": [
        "Connection refused to downstream service after 3 retries",
        "Database query timeout after 5000ms",
        "JWT token validation failed: signature mismatch",
        "Kafka consumer group rebalance timeout",
        "Memory limit exceeded: OOMKilled",
        "TLS handshake failed: certificate expired",
        "Circuit breaker OPEN: too many failures",
        "Deadlock detected in transaction",
    ],
    "WARN": [
        "High latency detected: p99=850ms exceeds SLO of 500ms",
        "Connection pool utilisation at 85%",
        "Cache hit rate dropped below 70%",
        "Retry attempt 2/3 for downstream call",
        "Slow query detected: 2300ms",
    ],
    "INFO": [
        "Request processed successfully",
        "Cache refreshed successfully",
        "Health check passed",
        "Deployment completed",
    ],
}


def seed_observability(client):
    console.print("[cyan]Seeding Observability data...[/cyan]")

    # Truncate first so re-seeds do not duplicate rows in MergeTree tables.
    for t in ("obs_services", "obs_dependencies", "obs_historical_incidents",
              "obs_events_stream", "obs_incident_workspace"):
        client.command(f"TRUNCATE TABLE IF EXISTS enterprise_memory.{t}")

    # Services
    svc_rows = [(s[0], s[0], s[1], s[2], s[3], "us-east-1") for s in SERVICES]
    client.insert("enterprise_memory.obs_services",
                  svc_rows,
                  column_names=["service_id","name","team","language","criticality","region"])

    # Dependencies
    dep_rows = list(DEPS)
    client.insert("enterprise_memory.obs_dependencies",
                  dep_rows,
                  column_names=["from_service","to_service","dep_type","latency_p99"])

    # Historical incidents with embeddings
    inc_rows = []
    base_ts = datetime.now() - timedelta(days=180)
    for i, inc in enumerate(OBS_INCIDENTS):
        text = f"{inc['title']} {inc['description']} {inc['root_cause']}"
        emb = embed(text)
        ts = base_ts + timedelta(days=i * 22, hours=rng.randint(0, 23))
        inc_rows.append((
            str(uuid.uuid4()), ts, inc["title"], inc["description"],
            inc["affected_services"], inc["root_cause"], inc["resolution"],
            inc["severity"], inc["duration_min"], emb,
        ))
    client.insert("enterprise_memory.obs_historical_incidents",
                  inc_rows,
                  column_names=["incident_id","ts","title","description",
                                "affected_services","root_cause","resolution",
                                "severity","duration_min","embedding"])

    # Live stream events (hot memory)
    stream_rows = []
    services_list = [s[0] for s in SERVICES]
    for _ in range(200):
        svc = rng.choice(services_list)
        level = rng.choices(["INFO","WARN","ERROR","CRITICAL"],
                            weights=[70, 20, 8, 2])[0]
        msg = rng.choice(LOG_MESSAGES.get(level, LOG_MESSAGES["INFO"]))
        stream_rows.append((
            str(uuid.uuid4()),
            datetime.now() - timedelta(seconds=rng.randint(0, 300)),
            svc, f"{svc}-pod-{rng.randint(1,5)}", level, msg,
            f"trace-{rng.randint(10000,99999)}",
            f"span-{rng.randint(10000,99999)}",
            rng.uniform(5, 2000),
            rng.choice([None, "DB_TIMEOUT", "CONN_REFUSED", "OOM", "TLS_ERR"])
                if level in ("ERROR","CRITICAL") else None,
            "us-east-1", "production",
        ))
    client.insert("enterprise_memory.obs_events_stream",
                  stream_rows,
                  column_names=["event_id","ts","service","host","level","message",
                                "trace_id","span_id","latency_ms","error_code",
                                "region","env"])
    console.print(f"  [green]>[/green] Observability: {len(svc_rows)} services, "
                  f"{len(dep_rows)} deps, {len(inc_rows)} incidents, "
                  f"{len(stream_rows)} live events")


# ─────────────────────────────────────────────────────────────────────────────
# USE CASE 2 — TELCO NETWORK
# ─────────────────────────────────────────────────────────────────────────────

ELEMENTS = [
    ("core-router-01",   "core",         "Cisco",    "ASR9000", "us-east",  "NYC-DC1",  "critical"),
    ("core-router-02",   "core",         "Juniper",  "MX960",   "us-west",  "LAX-DC1",  "critical"),
    ("edge-router-01",   "router",       "Cisco",    "ASR1001", "us-east",  "NYC-POP1", "high"),
    ("edge-router-02",   "router",       "Cisco",    "ASR1001", "us-east",  "BOS-POP1", "high"),
    ("edge-router-03",   "router",       "Juniper",  "MX204",   "us-west",  "LAX-POP1", "high"),
    ("dist-switch-01",   "switch",       "Arista",   "7050CX3", "us-east",  "NYC-DC1",  "high"),
    ("dist-switch-02",   "switch",       "Arista",   "7050CX3", "us-west",  "LAX-DC1",  "high"),
    ("bs-manhattan-01",  "base_station", "Ericsson", "AIR6449", "us-east",  "Manhattan","medium"),
    ("bs-manhattan-02",  "base_station", "Nokia",    "AEQD",    "us-east",  "Manhattan","medium"),
    ("bs-brooklyn-01",   "base_station", "Ericsson", "AIR6449", "us-east",  "Brooklyn", "medium"),
    ("fiber-nyc-lax-01", "fiber_link",   "Corning",  "SMF-28",  "national", "Backbone", "critical"),
    ("fiber-nyc-bos-01", "fiber_link",   "Corning",  "SMF-28",  "us-east",  "Backbone", "high"),
]

CONNECTIONS = [
    ("core-router-01",  "core-router-02",  "backbone",  400.0, 45.0),
    ("core-router-01",  "edge-router-01",  "peering",   100.0, 2.0),
    ("core-router-01",  "edge-router-02",  "peering",   100.0, 3.0),
    ("core-router-02",  "edge-router-03",  "peering",   100.0, 2.5),
    ("edge-router-01",  "dist-switch-01",  "access",    40.0,  0.5),
    ("edge-router-03",  "dist-switch-02",  "access",    40.0,  0.5),
    ("dist-switch-01",  "bs-manhattan-01", "backhaul",  10.0,  1.0),
    ("dist-switch-01",  "bs-manhattan-02", "backhaul",  10.0,  1.0),
    ("dist-switch-01",  "bs-brooklyn-01",  "backhaul",  10.0,  1.2),
    ("core-router-01",  "fiber-nyc-lax-01","backbone",  400.0, 45.0),
    ("core-router-01",  "fiber-nyc-bos-01","backbone",  100.0, 8.0),
]

TELCO_EVENTS = [
    {
        "element_id": "core-router-01",
        "event_type": "hardware_failure",
        "description": "Core router line card failure causing 30% traffic loss on backbone",
        "root_cause": "Faulty ASIC on line card LC-7 due to manufacturing defect",
        "resolution": "Hot-swapped line card, traffic rerouted via backup path during replacement",
        "impact_score": 9.2, "customers_aff": 45000,
    },
    {
        "element_id": "bs-manhattan-01",
        "event_type": "capacity_breach",
        "description": "Base station capacity exceeded 95% during peak hours causing call drops",
        "root_cause": "Unexpected traffic surge from large event at Madison Square Garden",
        "resolution": "Activated dynamic spectrum sharing, added temporary small cells",
        "impact_score": 6.5, "customers_aff": 8000,
    },
    {
        "element_id": "fiber-nyc-lax-01",
        "event_type": "fiber_cut",
        "description": "Backbone fiber cut by construction crew causing major outage",
        "root_cause": "Third-party construction without proper dig-safe notification",
        "resolution": "Traffic rerouted via alternate path, fiber spliced within 4 hours",
        "impact_score": 9.8, "customers_aff": 120000,
    },
    {
        "element_id": "edge-router-02",
        "event_type": "bgp_flap",
        "description": "BGP session flapping causing route instability in Boston PoP",
        "root_cause": "MTU mismatch after upstream provider maintenance window",
        "resolution": "Corrected MTU settings on both sides, BGP session stabilised",
        "impact_score": 5.0, "customers_aff": 3000,
    },
    {
        "element_id": "dist-switch-01",
        "event_type": "spanning_tree_loop",
        "description": "Spanning tree loop detected in NYC DC causing broadcast storm",
        "root_cause": "Misconfigured port added during network expansion",
        "resolution": "Identified and disabled rogue port, implemented BPDU guard",
        "impact_score": 7.5, "customers_aff": 25000,
    },
    {
        "element_id": "bs-brooklyn-01",
        "event_type": "power_failure",
        "description": "Base station power failure due to UPS battery depletion during grid outage",
        "root_cause": "UPS batteries not replaced per maintenance schedule (3 years overdue)",
        "resolution": "Restored via generator, replaced UPS batteries, updated maintenance schedule",
        "impact_score": 5.8, "customers_aff": 5500,
    },
    {
        "element_id": "core-router-02",
        "event_type": "software_bug",
        "description": "Memory leak in routing daemon causing gradual performance degradation",
        "root_cause": "Known bug in JunOS 21.2R1 with IS-IS route reflector",
        "resolution": "Upgraded to JunOS 21.4R2, memory leak resolved",
        "impact_score": 4.5, "customers_aff": 15000,
    },
]


def seed_telco(client):
    console.print("[cyan]Seeding Telco Network data...[/cyan]")

    for t in ("telco_elements", "telco_connections", "telco_network_events",
              "telco_network_state", "telco_fault_workspace"):
        client.command(f"TRUNCATE TABLE IF EXISTS enterprise_memory.{t}")

    # Elements
    elem_rows = [(e[0], e[1], e[2], e[3], e[4], e[5],
                  (datetime.now() - timedelta(days=rng.randint(365, 1825))).date(),
                  e[6]) for e in ELEMENTS]
    client.insert("enterprise_memory.telco_elements",
                  elem_rows,
                  column_names=["element_id","element_type","vendor","model",
                                "region","site","install_date","criticality"])

    # Connections
    client.insert("enterprise_memory.telco_connections",
                  list(CONNECTIONS),
                  column_names=["from_element","to_element","link_type",
                                "capacity_gbps","latency_ms"])

    # Historical events with embeddings
    ev_rows = []
    base_ts = datetime.now() - timedelta(days=365)
    for i, ev in enumerate(TELCO_EVENTS):
        text = f"{ev['event_type']} {ev['description']} {ev['root_cause']}"
        emb = embed(text)
        ts = base_ts + timedelta(days=i * 50, hours=rng.randint(0, 23))
        ev_rows.append((
            str(uuid.uuid4()), ts, ev["element_id"], ev["event_type"],
            ev["description"], ev["root_cause"], ev["resolution"],
            ev["impact_score"], ev["customers_aff"], emb,
        ))
    client.insert("enterprise_memory.telco_network_events",
                  ev_rows,
                  column_names=["event_id","ts","element_id","event_type",
                                "description","root_cause","resolution",
                                "impact_score","customers_aff","embedding"])

    # Live network state (hot memory)
    state_rows = []
    for elem in ELEMENTS:
        eid, etype = elem[0], elem[1]
        status = rng.choices(["up","degraded","down","maintenance"],
                             weights=[80, 12, 5, 3])[0]
        state_rows.append((
            eid, etype, elem[2], elem[4], status,
            rng.uniform(10, 95), rng.uniform(20, 90),
            rng.uniform(0.5, 380), rng.uniform(0.0, 2.5),
            datetime.now() - timedelta(seconds=rng.randint(0, 60)),
        ))
    client.insert("enterprise_memory.telco_network_state",
                  state_rows,
                  column_names=["element_id","element_type","vendor","region",
                                "status","cpu_pct","mem_pct","traffic_gbps",
                                "error_rate","last_seen"])
    console.print(f"  [green]>[/green] Telco: {len(elem_rows)} elements, "
                  f"{len(CONNECTIONS)} connections, {len(ev_rows)} events, "
                  f"{len(state_rows)} live states")


# ─────────────────────────────────────────────────────────────────────────────
# USE CASE 3 — CYBERSECURITY
# ─────────────────────────────────────────────────────────────────────────────

ASSETS = [
    ("asset-001", "prod-db-pii-01",    "database",   "critical", "Data Engineering", "PII",          "prod-internal",  "Ubuntu 22.04"),
    ("asset-002", "prod-db-finance-01","database",   "critical", "Finance",          "Financial",    "prod-internal",  "RHEL 8"),
    ("asset-003", "prod-api-gw-01",    "server",     "high",     "Platform",         "Internal",     "prod-dmz",       "Ubuntu 22.04"),
    ("asset-004", "dev-bastion-01",    "bastion",    "high",     "DevOps",           "Internal",     "dev-dmz",        "Ubuntu 20.04"),
    ("asset-005", "corp-ldap-01",      "directory",  "critical", "IT",               "Confidential", "corp-internal",  "Windows Server 2019"),
    ("asset-006", "workstation-cfo-01","workstation","high",     "Finance",          "Financial",    "corp-internal",  "Windows 11"),
    ("asset-007", "prod-k8s-master-01","server",     "critical", "Platform",         "Internal",     "prod-internal",  "Ubuntu 22.04"),
    ("asset-008", "backup-nas-01",     "storage",    "critical", "IT",               "Confidential", "prod-internal",  "FreeNAS"),
]

USERS = [
    ("user-001", "alice.chen",    "Data Engineering", "Data Engineer",    0.1, 1),
    ("user-002", "bob.smith",     "Finance",          "CFO",              0.2, 1),
    ("user-003", "carol.jones",   "DevOps",           "SRE Lead",         0.15, 1),
    ("user-004", "dave.wilson",   "IT",               "Sysadmin",         0.3, 1),
    ("user-005", "eve.martinez",  "Security",         "SOC Analyst",      0.1, 1),
    ("user-006", "frank.lee",     "Data Engineering", "DB Admin",         0.25, 1),
    ("user-007", "grace.kim",     "Platform",         "DevOps Engineer",  0.2, 1),
    ("user-008", "henry.brown",   "Finance",          "Finance Analyst",  0.35, 0),
]

from datetime import date as _date
ACCESS = [
    ("user-001", "asset-001", "read-write", _date(2023, 1, 15)),
    ("user-002", "asset-002", "read",       _date(2022, 6, 1)),
    ("user-002", "asset-006", "admin",      _date(2022, 6, 1)),
    ("user-003", "asset-004", "admin",      _date(2023, 3, 10)),
    ("user-003", "asset-007", "admin",      _date(2023, 3, 10)),
    ("user-004", "asset-005", "admin",      _date(2022, 1, 1)),
    ("user-004", "asset-008", "admin",      _date(2022, 1, 1)),
    ("user-005", "asset-003", "read",       _date(2023, 6, 1)),
    ("user-006", "asset-001", "admin",      _date(2022, 9, 15)),
    ("user-006", "asset-002", "admin",      _date(2022, 9, 15)),
    ("user-007", "asset-007", "admin",      _date(2023, 1, 20)),
    ("user-008", "asset-002", "read",       _date(2023, 7, 1)),
]

THREAT_INTEL = [
    {
        "type": "ip", "value": "185.220.101.47",
        "actor": "FIN6", "campaign": "Operation FinFisher",
        "ttps": ["T1566.001","T1059.001","T1078"],
        "confidence": 0.95,
        "desc": "Known FIN6 C2 server used in financial sector attacks via phishing and credential theft",
    },
    {
        "type": "ip", "value": "45.142.212.100",
        "actor": "APT29", "campaign": "SolarWinds Supply Chain",
        "ttps": ["T1195.002","T1078","T1021.001"],
        "confidence": 0.88,
        "desc": "APT29 infrastructure used in supply chain compromise and lateral movement",
    },
    {
        "type": "domain", "value": "update-service.net",
        "actor": "Lazarus", "campaign": "Operation DreamJob",
        "ttps": ["T1566.002","T1204.001","T1105"],
        "confidence": 0.82,
        "desc": "Lazarus Group phishing domain masquerading as software update service",
    },
    {
        "type": "hash", "value": "d41d8cd98f00b204e9800998ecf8427e",
        "actor": "REvil", "campaign": "Kaseya VSA Attack",
        "ttps": ["T1486","T1490","T1489"],
        "confidence": 0.97,
        "desc": "REvil ransomware payload hash used in Kaseya VSA supply chain attack",
    },
    {
        "type": "ip", "value": "91.108.4.0",
        "actor": "Sandworm", "campaign": "NotPetya",
        "ttps": ["T1561.002","T1485","T1486"],
        "confidence": 0.91,
        "desc": "Sandworm destructive malware C2 infrastructure targeting critical infrastructure",
    },
]

SEC_INCIDENTS = [
    {
        "type": "credential_compromise",
        "title": "Admin account compromised via credential stuffing",
        "desc": "Database admin account compromised using credentials from third-party breach, attacker accessed PII database",
        "user": "user-006", "asset": "asset-001", "ip": "185.220.101.47",
        "actor": "FIN6", "ttps": ["T1078","T1530"],
        "root_cause": "Password reuse from compromised third-party service; no MFA on DB admin account",
        "response": "Suspended account, rotated all DB credentials, enabled MFA, reviewed access logs",
        "outcome": "Contained; 50k PII records potentially exposed; notified DPA",
        "severity": "critical",
    },
    {
        "type": "ransomware",
        "title": "Ransomware deployment via compromised VPN credentials",
        "desc": "Attacker used stolen VPN credentials to access corporate network and deploy ransomware on file servers",
        "user": "user-004", "asset": "asset-008", "ip": "45.142.212.100",
        "actor": "REvil", "ttps": ["T1078","T1486","T1490"],
        "root_cause": "VPN credentials phished via spear-phishing email; no MFA on VPN",
        "response": "Isolated affected systems, restored from backup, implemented MFA on VPN",
        "outcome": "Contained; 2TB backup data encrypted; restored within 8 hours from clean backup",
        "severity": "critical",
    },
    {
        "type": "insider_threat",
        "title": "Finance analyst exfiltrating data to personal cloud storage",
        "desc": "Finance analyst uploading sensitive financial reports to personal Dropbox account",
        "user": "user-008", "asset": "asset-002", "ip": "10.0.5.42",
        "actor": "Insider", "ttps": ["T1567.002","T1052"],
        "root_cause": "No DLP controls on cloud storage uploads; excessive data access permissions",
        "response": "Suspended user account, blocked cloud storage uploads, reviewed all access",
        "outcome": "Contained; disciplinary action taken; DLP solution implemented",
        "severity": "high",
    },
    {
        "type": "phishing",
        "title": "CFO targeted by business email compromise (BEC)",
        "desc": "CFO received spear-phishing email impersonating CEO, nearly authorised fraudulent wire transfer",
        "user": "user-002", "asset": "asset-006", "ip": "91.108.4.0",
        "actor": "Scattered Spider", "ttps": ["T1566.001","T1534"],
        "root_cause": "No email authentication (DMARC/DKIM) on executive email domain",
        "response": "Blocked fraudulent transfer, implemented DMARC/DKIM, trained executives on BEC",
        "outcome": "Prevented; $2.3M wire transfer blocked; no financial loss",
        "severity": "high",
    },
    {
        "type": "lateral_movement",
        "title": "Attacker lateral movement via compromised bastion host",
        "desc": "Attacker gained access to bastion host and used it to pivot to production Kubernetes cluster",
        "user": "user-003", "asset": "asset-004", "ip": "45.142.212.100",
        "actor": "APT29", "ttps": ["T1021.001","T1078","T1550.001"],
        "root_cause": "Bastion host SSH keys not rotated; overly permissive IAM roles on bastion",
        "response": "Rotated all SSH keys, restricted bastion IAM roles, implemented PAM solution",
        "outcome": "Contained; attacker had read access to 3 K8s namespaces for 2 hours",
        "severity": "critical",
    },
]

SEC_EVENTS = [
    ("login_success",  "user-006", "asset-001", "185.220.101.47", "login",   "success", "critical"),
    ("login_failed",   "user-008", "asset-002", "10.0.5.42",      "login",   "failure", "medium"),
    ("file_access",    "user-002", "asset-006", "10.0.1.15",      "read",    "success", "low"),
    ("privilege_esc",  "user-004", "asset-005", "10.0.2.30",      "sudo",    "success", "high"),
    ("data_exfil",     "user-008", "asset-002", "10.0.5.42",      "upload",  "success", "critical"),
    ("port_scan",      "unknown",  "asset-003", "91.108.4.0",     "scan",    "blocked", "high"),
    ("login_success",  "user-003", "asset-004", "10.0.3.20",      "ssh",     "success", "low"),
    ("malware_detect", "unknown",  "asset-008", "45.142.212.100", "execute", "blocked", "critical"),
]


def seed_cybersecurity(client):
    console.print("[cyan]Seeding Cybersecurity data...[/cyan]")

    for t in ("sec_assets", "sec_users", "sec_access", "sec_threat_intel",
              "sec_historical_incidents", "sec_events_stream", "sec_case_workspace"):
        client.command(f"TRUNCATE TABLE IF EXISTS enterprise_memory.{t}")

    # Assets
    client.insert("enterprise_memory.sec_assets",
                  [list(a) for a in ASSETS],
                  column_names=["asset_id","hostname","asset_type","criticality",
                                "owner_team","data_class","network_zone","os"])

    # Users
    client.insert("enterprise_memory.sec_users",
                  [list(u) for u in USERS],
                  column_names=["user_id","username","department","role",
                                "risk_score","mfa_enabled"])

    # Access relationships
    client.insert("enterprise_memory.sec_access",
                  [list(a) for a in ACCESS],
                  column_names=["user_id","asset_id","access_type","granted_date"])

    # Threat intelligence with embeddings
    ti_rows = []
    type_map = {"ip": "ip", "domain": "domain", "hash": "hash", "url": "url"}
    base_ts = datetime.now() - timedelta(days=365)
    for i, ti in enumerate(THREAT_INTEL):
        emb = embed(f"{ti['actor']} {ti['campaign']} {ti['desc']}")
        ts = base_ts + timedelta(days=i * 60)
        ti_rows.append((
            str(uuid.uuid4()), ti["type"], ti["value"],
            ti["actor"], ti["campaign"], ti["ttps"],
            ti["confidence"], ti["desc"], emb, ts, datetime.now(),
        ))
    client.insert("enterprise_memory.sec_threat_intel",
                  ti_rows,
                  column_names=["indicator_id","indicator_type","indicator_val",
                                "threat_actor","campaign","ttps","confidence",
                                "description","embedding","first_seen","last_seen"])

    # Historical incidents with embeddings
    inc_rows = []
    base_ts = datetime.now() - timedelta(days=400)
    for i, inc in enumerate(SEC_INCIDENTS):
        text = f"{inc['type']} {inc['title']} {inc['desc']} {inc['root_cause']}"
        emb = embed(text)
        ts = base_ts + timedelta(days=i * 75, hours=rng.randint(0, 23))
        inc_rows.append((
            str(uuid.uuid4()), ts, inc["type"], inc["title"], inc["desc"],
            inc["user"], inc["asset"], inc["ip"], inc["actor"], inc["ttps"],
            inc["root_cause"], inc["response"], inc["outcome"], inc["severity"], emb,
        ))
    client.insert("enterprise_memory.sec_historical_incidents",
                  inc_rows,
                  column_names=["incident_id","ts","incident_type","title","description",
                                "affected_user","affected_asset","attacker_ip",
                                "threat_actor","ttps","root_cause","response",
                                "outcome","severity","embedding"])

    # Live security events (hot memory)
    ev_rows = []
    for ev in SEC_EVENTS:
        ev_rows.append((
            str(uuid.uuid4()),
            datetime.now() - timedelta(seconds=rng.randint(0, 600)),
            ev[0], "SIEM", ev[1], ev[2], ev[3],
            rng.choice(["10.0.0.1", "10.0.0.2"]),
            ev[4], ev[5], ev[6],
            f'{{"event_type":"{ev[0]}","user":"{ev[1]}","asset":"{ev[2]}"}}',
        ))
    client.insert("enterprise_memory.sec_events_stream",
                  ev_rows,
                  column_names=["event_id","ts","event_type","source_system",
                                "user_id","asset_id","src_ip","dst_ip",
                                "action","outcome","severity","raw_log"])
    console.print(f"  [green]>[/green] Cybersecurity: {len(ASSETS)} assets, "
                  f"{len(USERS)} users, {len(ACCESS)} access rules, "
                  f"{len(ti_rows)} threat intel, {len(inc_rows)} incidents, "
                  f"{len(ev_rows)} live events")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    console.print("\n[bold cyan]=== Enterprise Agent Memory -- Data Seeder ===[/bold cyan]\n")

    # Print active embedding config
    provider = os.getenv("EMBEDDING_PROVIDER", "(none -- using deterministic fallback)")
    model = os.getenv("EMBEDDING_MODEL", "(none)")
    dim = os.getenv("EMBED_DIM", "768")
    console.print(f"  Embedding provider: [bold]{provider}[/bold]")
    console.print(f"  Embedding model:    [bold]{model}[/bold]")
    console.print(f"  Embedding dim:      [bold]{dim}[/bold]")
    console.print()

    client = get_ch_client()

    # Run schema first (domain tables + generic agent memory tables).
    # 02_agent_memory.sql uses the experimental vector_similarity index,
    # so we pass allow_experimental_vector_similarity_index=1 unconditionally
    # when applying that file.
    schema_dir = os.path.join(os.path.dirname(__file__), "../schema")
    for schema_name in ("01_schema.sql", "02_agent_memory.sql"):
        schema_path = os.path.join(schema_dir, schema_name)
        if not os.path.exists(schema_path):
            continue
        needs_vector_flag = "agent_memory" in schema_name
        settings = (
            {"allow_experimental_vector_similarity_index": 1}
            if needs_vector_flag else None
        )
        with open(schema_path) as f:
            for stmt in f.read().split(";"):
                stmt = stmt.strip()
                if not stmt:
                    continue
                try:
                    if settings:
                        client.command(stmt, settings=settings)
                    else:
                        client.command(stmt)
                except Exception as e:
                    msg = str(e)
                    if "already exists" in msg:
                        continue
                    console.print(f"[yellow]Schema warning: {e}[/yellow]")

    seed_observability(client)
    seed_telco(client)
    seed_cybersecurity(client)

    # Import lazily so the domain seeders run even if this module has issues.
    from shared.seeders.seed_conversation import seed_conversation
    seed_conversation(client)

    console.print("\n[bold green]> All data seeded successfully![/bold green]\n")


if __name__ == "__main__":
    main()
