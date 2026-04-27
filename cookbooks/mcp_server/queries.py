"""
mcp_server/queries.py
---------------------
Domain-scoped SQL the MCP tools hand to ClickHouse. Every query has
leading + inline comments so a reader can parse the intent in seconds
without knowing the schema.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# HOT TIER  -- tail the live event / state stream (Memory engine, sub-5ms)
# ---------------------------------------------------------------------------

HOT_SCAN_SQL = {
    "observability": """
        -- HOT tier: live event stream for the observability domain.
        -- obs_events_stream uses ENGINE = Memory, so this read is sub-5ms
        -- and the data is volatile (cleared on container restart).
        SELECT event_id, ts, service, host, level, message,
               latency_ms, error_code, trace_id
        FROM enterprise_memory.obs_events_stream
        -- Narrow by service name if the caller gave one; otherwise match all.
        WHERE (length({service:String}) = 0 OR service = {service:String})
          -- Only errors + criticals; INFO / WARN are not useful for triage.
          AND level IN ('ERROR', 'CRITICAL')
          -- Rolling window: last N minutes of the live stream.
          AND ts >= now() - INTERVAL {minutes:UInt32} MINUTE
        ORDER BY ts DESC
        LIMIT {limit:UInt32}
    """,
    "telco": """
        -- HOT tier: live network element state (Memory engine).
        -- telco_network_state is updated by SNMP / telemetry pollers; each
        -- row is the latest-known state of one network element.
        SELECT element_id, element_type, vendor, region, status,
               cpu_pct, mem_pct, traffic_gbps, error_rate, last_seen
        FROM enterprise_memory.telco_network_state
        -- Narrow by element, by region, or leave open (empty filter).
        WHERE (length({service:String}) = 0
               OR element_id = {service:String}
               OR region = {service:String})
          -- Only unhealthy state: degraded/down, abnormal error rate, or
          -- CPU over 80% -- anything we would want an operator to look at.
          AND (status IN ('degraded', 'down')
               OR error_rate > 0.01
               OR cpu_pct > 80)
        -- Rank down first, then degraded, then sort by error rate.
        ORDER BY
          CASE status WHEN 'down' THEN 1 WHEN 'degraded' THEN 2 ELSE 3 END,
          error_rate DESC
        LIMIT {limit:UInt32}
    """,
    "cybersecurity": """
        -- HOT tier: live security event stream (Memory engine).
        -- sec_events_stream ingests from SIEM / EDR / IAM in real time.
        SELECT event_id, ts, event_type, source_system, user_id,
               asset_id, src_ip, dst_ip, action, outcome, severity
        FROM enterprise_memory.sec_events_stream
        -- Filter is one of user_id / asset_id / src_ip, or empty = all.
        WHERE (length({service:String}) = 0
               OR user_id = {service:String}
               OR asset_id = {service:String}
               OR src_ip = {service:String})
          -- Triage bar: only high + critical severity.
          AND severity IN ('high', 'critical')
          -- Lookback window in minutes.
          AND ts >= now() - INTERVAL {minutes:UInt32} MINUTE
        ORDER BY ts DESC
        LIMIT {limit:UInt32}
    """,
}


# ---------------------------------------------------------------------------
# HOT TIER  -- open an investigation: materialise a per-case scratchpad
# ---------------------------------------------------------------------------

HOT_WORKSPACE_TABLE = {
    "observability": "obs_incident_workspace",
    "telco": "telco_fault_workspace",
    "cybersecurity": "sec_case_workspace",
}

HOT_WORKSPACE_LOAD_SQL = {
    "observability": """
        -- HOT tier: open an investigation workspace for this case_id.
        -- We copy correlated events out of the live stream into a
        -- per-case Memory table so subsequent steps can scope to this
        -- incident without re-filtering the stream.
        INSERT INTO enterprise_memory.obs_incident_workspace
        SELECT
            {case_id:String} AS incident_id,
            event_id, ts, service, host, level, message,
            trace_id, latency_ms, error_code,
            now64() AS added_at
        FROM enterprise_memory.obs_events_stream
        -- Correlate by service (services currently throwing ERROR/CRITICAL)
        -- OR by a specific trace_id when the caller gave one.
        WHERE (
            service IN (
                SELECT DISTINCT service
                FROM enterprise_memory.obs_events_stream
                WHERE level IN ('ERROR', 'CRITICAL')
                  AND ts >= now() - INTERVAL 15 MINUTE
            )
            OR (length({trace_id:String}) > 0 AND trace_id = {trace_id:String})
        )
        -- Keep the correlation window aligned with the recent-errors window.
        AND ts >= now() - INTERVAL 15 MINUTE
    """,
    "telco": """
        -- HOT tier: open a fault workspace for this case_id.
        -- Each row pairs the fault_id with the element + its current
        -- error_rate so the follow-up summary step can group by element.
        INSERT INTO enterprise_memory.telco_fault_workspace
        SELECT
            {case_id:String} AS fault_id,
            element_id,
            last_seen AS ts,
            'composite' AS metric,
            error_rate AS value,
            0.01 AS threshold,
            status AS severity,
            now64() AS added_at
        FROM enterprise_memory.telco_network_state
        -- Unhealthy elements only: degraded/down or above the error threshold.
        WHERE status IN ('degraded', 'down') OR error_rate > 0.01
    """,
    "cybersecurity": """
        -- HOT tier: open a SOC case workspace for this case_id.
        -- We sweep recent high-severity security events into the case
        -- table so the agent can scope to this case going forward.
        INSERT INTO enterprise_memory.sec_case_workspace
        SELECT
            {case_id:String} AS case_id,
            event_id, ts, event_type, user_id, asset_id,
            src_ip, action, outcome, toString(severity) AS severity,
            now64() AS added_at
        FROM enterprise_memory.sec_events_stream
        WHERE severity IN ('high', 'critical')
          -- Wider window than the live scan (30m vs 15m) so we do not lose
          -- the attacker's lead-up events.
          AND ts >= now() - INTERVAL 30 MINUTE
    """,
}

HOT_WORKSPACE_SUMMARY_SQL = {
    "observability": """
        -- Group the workspace by service so we can see the fault picture
        -- at a glance: which services are throwing errors, how often,
        -- and which error codes are dominant.
        SELECT service,
               countIf(level = 'ERROR')    AS errors,
               countIf(level = 'CRITICAL') AS criticals,
               round(avg(latency_ms), 1)   AS avg_latency_ms,
               round(max(latency_ms), 1)   AS max_latency_ms,
               groupArray(DISTINCT error_code) AS error_codes
        FROM enterprise_memory.obs_incident_workspace
        WHERE incident_id = {case_id:String}
        GROUP BY service
        ORDER BY criticals DESC, errors DESC
    """,
    "telco": """
        -- Group the fault workspace by element + severity so one row per
        -- element shows the worst and average error rate the agent saw.
        SELECT element_id, severity,
               count() AS events,
               round(avg(value), 4) AS avg_error_rate,
               round(max(value), 4) AS max_error_rate
        FROM enterprise_memory.telco_fault_workspace
        WHERE fault_id = {case_id:String}
        GROUP BY element_id, severity
        ORDER BY max_error_rate DESC
    """,
    "cybersecurity": """
        -- Group the case workspace by event_type + severity so the agent
        -- can spot patterns ("10 login_failed on one asset") and scope.
        SELECT event_type, severity,
               count() AS events,
               uniqExact(user_id) AS unique_users,
               uniqExact(asset_id) AS unique_assets,
               groupArray(DISTINCT src_ip) AS src_ips
        FROM enterprise_memory.sec_case_workspace
        WHERE case_id = {case_id:String}
        GROUP BY event_type, severity
        ORDER BY events DESC
    """,
}


# ---------------------------------------------------------------------------
# WARM TIER  -- semantic search over historical records (MergeTree + HNSW)
# ---------------------------------------------------------------------------

WARM_VECTOR_SQL = {
    "observability": """
        -- WARM tier: SEMANTIC SEARCH over past observability incidents.
        -- `embedding` is Array(Float32). cosineDistance returns 0 for an
        -- identical vector and 2 for the opposite direction, so LOWER is
        -- more similar. The HNSW index makes the ORDER BY LIMIT k fast.
        --
        -- Filter-first, rank-second: the ts window prunes whole monthly
        -- partitions (PARTITION BY toYYYYMM(ts)) before HNSW sees a single
        -- row. Rule: query-join-filter-before (filter before the expensive
        -- op — here the expensive op is HNSW rank, not a JOIN).
        SELECT incident_id, ts, title, affected_services,
               root_cause, resolution,
               toString(severity) AS severity, duration_min,
               -- Rounded copy for display; the un-rounded form drives the sort.
               round(cosineDistance(embedding, {emb}), 4) AS similarity_distance
        FROM enterprise_memory.obs_historical_incidents
        WHERE ts >= now() - INTERVAL {days:UInt32} DAY
        -- ORDER BY uses the un-rounded distance so the HNSW index kicks in.
        ORDER BY cosineDistance(embedding, {emb}) ASC
        LIMIT {k:UInt32}
    """,
    "telco": """
        -- WARM tier: SEMANTIC SEARCH over past telco network events.
        -- Same pattern: cosineDistance over the event description vector.
        -- Same partition-pruning discipline as observability.
        SELECT event_id, ts, element_id, event_type, description,
               root_cause, resolution, impact_score, customers_aff,
               round(cosineDistance(embedding, {emb}), 4) AS similarity_distance
        FROM enterprise_memory.telco_network_events
        WHERE ts >= now() - INTERVAL {days:UInt32} DAY
        ORDER BY cosineDistance(embedding, {emb}) ASC
        LIMIT {k:UInt32}
    """,
    "cybersecurity": """
        -- WARM tier: SEMANTIC SEARCH over past security incidents.
        -- Same pattern: rank historical incidents by vector similarity
        -- to the current event description.
        SELECT incident_id, ts, incident_type, title, description,
               threat_actor, ttps, root_cause, response, outcome,
               toString(severity) AS severity,
               round(cosineDistance(embedding, {emb}), 4) AS similarity_distance
        FROM enterprise_memory.sec_historical_incidents
        WHERE ts >= now() - INTERVAL {days:UInt32} DAY
        ORDER BY cosineDistance(embedding, {emb}) ASC
        LIMIT {k:UInt32}
    """,
}


# ---------------------------------------------------------------------------
# WARM TIER  -- fetch a specific record (runbook by id, threat intel by text)
# ---------------------------------------------------------------------------

WARM_LOOKUP_SQL = {
    ("cybersecurity", "threat_intel"): """
        -- WARM tier: THREAT INTEL SEMANTIC LOOKUP.
        -- Vector search over sec_threat_intel where the embedded text is
        -- (actor + campaign + description). Used to check whether the
        -- current IoC / TTP / description matches any known threat group.
        SELECT indicator_id, toString(indicator_type) AS indicator_type,
               indicator_val, threat_actor, campaign, ttps,
               confidence, description, first_seen, last_seen,
               round(cosineDistance(embedding, {emb}), 4) AS similarity_distance
        FROM enterprise_memory.sec_threat_intel
        ORDER BY cosineDistance(embedding, {emb}) ASC
        LIMIT {k:UInt32}
    """,
    ("observability", "runbook"): """
        -- WARM tier: FETCH A RUNBOOK BY ID.
        -- Deterministic lookup: no vector math, just the full
        -- historical incident row so the agent can quote the resolution.
        SELECT incident_id, title, root_cause, resolution,
               duration_min, toString(severity) AS severity, affected_services
        FROM enterprise_memory.obs_historical_incidents
        WHERE incident_id = {identifier:String}
    """,
    ("telco", "runbook"): """
        -- WARM tier: FETCH A NETWORK EVENT BY ID.
        -- Deterministic lookup for the resolution of a specific past event.
        SELECT event_id, element_id, event_type, description,
               root_cause, resolution, impact_score, customers_aff
        FROM enterprise_memory.telco_network_events
        WHERE event_id = {identifier:String}
    """,
    ("cybersecurity", "runbook"): """
        -- WARM tier: FETCH A SECURITY INCIDENT BY ID.
        -- Deterministic lookup; returns the full past incident record
        -- including response actions and outcome for the agent to quote.
        SELECT incident_id, incident_type, title, description,
               threat_actor, ttps, root_cause, response, outcome,
               toString(severity) AS severity
        FROM enterprise_memory.sec_historical_incidents
        WHERE incident_id = {identifier:String}
    """,
}


# ---------------------------------------------------------------------------
# GRAPH TIER  -- multi-hop traversal via SQL JOINs on MergeTree tables
# ---------------------------------------------------------------------------

GRAPH_TRAVERSE_SQL = {
    "observability": """
        -- GRAPH tier: multi-hop dependency traversal for observability.
        -- Question: "who depends on <service>?" (upstream blast radius).
        --
        -- Optimisations applied per clickhouse-best-practices:
        --   * obs_dependencies has a bloom_filter skipping index on to_service
        --     because ORDER BY (from_service, to_service) cannot prune on the
        --     second key alone (rule: query-index-skipping-indices).
        --   * JOIN to obs_services is 1:1 (service_id is the primary key of
        --     that table), so LEFT ANY JOIN is used for smaller memory
        --     footprint (rule: query-join-use-any).
        --
        -- Hop 1: direct dependents of the target service.
        SELECT d.from_service AS related, s.criticality, s.team,
               d.dep_type AS edge_type, d.latency_p99, 1 AS hops
        FROM enterprise_memory.obs_dependencies d
        LEFT ANY JOIN enterprise_memory.obs_services s
               ON s.service_id = d.from_service
        WHERE d.to_service = {entity:String}

        UNION ALL

        -- Hop 2: services that depend on services that depend on the target.
        -- Only emitted when max_hops >= 2. The subquery is filtered first
        -- (rule: query-join-filter-before — same principle for IN lists).
        SELECT d2.from_service, s2.criticality, s2.team,
               d2.dep_type, d2.latency_p99, 2 AS hops
        FROM enterprise_memory.obs_dependencies d2
        LEFT ANY JOIN enterprise_memory.obs_services s2
               ON s2.service_id = d2.from_service
        WHERE d2.to_service IN (
            SELECT from_service FROM enterprise_memory.obs_dependencies
            WHERE to_service = {entity:String}
        )
        AND {max_hops:UInt32} >= 2
    """,
    "telco": """
        -- GRAPH tier: multi-hop topology traversal for telco.
        -- Question: "what network elements are downstream of <element>?"
        --
        -- Hop 1: direct downstream elements. LEFT ANY JOIN because
        -- element_id is the primary key of telco_elements (1:1).
        SELECT c.to_element AS related, e.element_type, e.vendor,
               c.link_type AS edge_type, c.capacity_gbps, c.latency_ms, 1 AS hops
        FROM enterprise_memory.telco_connections c
        LEFT ANY JOIN enterprise_memory.telco_elements e
               ON e.element_id = c.to_element
        WHERE c.from_element = {entity:String}

        UNION ALL

        -- Hop 2: downstream-of-downstream (two fiber/link hops out).
        SELECT c2.to_element, e2.element_type, e2.vendor,
               c2.link_type, c2.capacity_gbps, c2.latency_ms, 2 AS hops
        FROM enterprise_memory.telco_connections c2
        LEFT ANY JOIN enterprise_memory.telco_elements e2
               ON e2.element_id = c2.to_element
        WHERE c2.from_element IN (
            SELECT to_element FROM enterprise_memory.telco_connections
            WHERE from_element = {entity:String}
        )
        AND {max_hops:UInt32} >= 2
    """,
    "cybersecurity": """
        -- GRAPH tier: multi-hop access traversal for cybersecurity.
        -- Question: "what assets can <user> reach, and what lateral
        -- movement is possible from those assets?"
        --
        -- Hop 1: assets the user has direct access to. LEFT ANY JOIN because
        -- asset_id is the primary key of sec_assets (1:1).
        SELECT a.asset_id AS related, a.hostname, a.asset_type,
               a.criticality, a.data_class, a.network_zone,
               ac.access_type AS edge_type, 1 AS hops
        FROM enterprise_memory.sec_access ac
        LEFT ANY JOIN enterprise_memory.sec_assets a
               ON a.asset_id = ac.asset_id
        WHERE ac.user_id = {entity:String}

        UNION ALL

        -- Hop 2: OTHER users who share access to any of the same assets
        -- as the target user. These are lateral-movement candidates.
        SELECT u2.user_id, u2.username, 'user' AS asset_type,
               'medium' AS criticality, '' AS data_class, '' AS network_zone,
               'lateral_access' AS edge_type, 2 AS hops
        FROM enterprise_memory.sec_access ac2
        LEFT ANY JOIN enterprise_memory.sec_users u2
               ON u2.user_id = ac2.user_id
        WHERE ac2.asset_id IN (
            SELECT asset_id FROM enterprise_memory.sec_access
            WHERE user_id = {entity:String}
        )
        AND u2.user_id != {entity:String}
        AND {max_hops:UInt32} >= 2
    """,
}
