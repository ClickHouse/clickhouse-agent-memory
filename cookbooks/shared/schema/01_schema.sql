-- ============================================================
-- Enterprise Memory System — ClickHouse Schema
-- Three Use Cases: Observability, Telco, Cybersecurity
-- ============================================================

CREATE DATABASE IF NOT EXISTS enterprise_memory;

-- ============================================================
-- USE CASE 1: APPLICATION / INFRASTRUCTURE OBSERVABILITY
-- ============================================================

-- HOT TIER: Real-time log and metric stream (Memory Engine)
CREATE TABLE IF NOT EXISTS enterprise_memory.obs_events_stream
(
    event_id     UUID          DEFAULT generateUUIDv4(),
    ts           DateTime64(3) DEFAULT now64(),
    service      String,
    host         String,
    level        Enum8('DEBUG'=1,'INFO'=2,'WARN'=3,'ERROR'=4,'CRITICAL'=5),
    message      String,
    trace_id     String,
    span_id      String,
    latency_ms   Float32,
    error_code   Nullable(String),
    region       String,
    env          String
) ENGINE = Memory;

-- HOT TIER: Active incident investigation workspace (Memory Engine)
CREATE TABLE IF NOT EXISTS enterprise_memory.obs_incident_workspace
(
    incident_id  String,
    event_id     UUID,
    ts           DateTime64(3),
    service      String,
    host         String,
    level        String,
    message      String,
    trace_id     String,
    latency_ms   Float32,
    error_code   Nullable(String),
    added_at     DateTime64(3) DEFAULT now64()
) ENGINE = Memory;

-- WARM TIER: Historical incidents with vector embeddings (MergeTree)
-- NOTE: vector_similarity index requires allow_experimental_vector_similarity_index=1
CREATE TABLE IF NOT EXISTS enterprise_memory.obs_historical_incidents
(
    incident_id   UUID          DEFAULT generateUUIDv4(),
    ts            DateTime64(3),
    title         String,
    description   String,
    affected_services Array(String),
    root_cause    String,
    resolution    String,
    severity      Enum8('P1'=1,'P2'=2,'P3'=3,'P4'=4),
    duration_min  UInt32,
    embedding     Array(Float32),

    -- Skip index: get_record does exact lookups by UUID. incident_id is not in
    -- ORDER BY (severity, ts), so without this the query scans every granule.
    -- UUIDs are high-cardinality exact matches, bloom_filter is the right choice.
    -- Rule: query-index-skipping-indices (clickhouse-best-practices).
    INDEX idx_incident_id incident_id TYPE bloom_filter(0.01) GRANULARITY 1
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(ts)
ORDER BY (severity, ts)
;

-- GRAPH TIER: Service dependency map (walked with SQL self-JOIN + UNION ALL)
CREATE TABLE IF NOT EXISTS enterprise_memory.obs_services
(
    service_id   String,
    name         String,
    team         String,
    language     String,
    criticality  Enum8('low'=1,'medium'=2,'high'=3,'critical'=4),
    region       String
) ENGINE = MergeTree()
ORDER BY service_id;

CREATE TABLE IF NOT EXISTS enterprise_memory.obs_dependencies
(
    from_service String,
    to_service   String,
    dep_type     String,
    latency_p99  Float32,

    -- Skip index: blast radius walks filter on to_service (who depends on X),
    -- but ORDER BY starts with from_service. Without this, the primary index
    -- cannot prune and the engine scans every granule. bloom_filter is the
    -- right shape for equality on a high-cardinality string.
    -- Rule: query-index-skipping-indices (clickhouse-best-practices).
    INDEX idx_to_service to_service TYPE bloom_filter(0.01) GRANULARITY 1
) ENGINE = MergeTree()
ORDER BY (from_service, to_service);

-- ============================================================
-- USE CASE 2: TELCO NETWORK INVENTORY & MONITORING
-- ============================================================

-- HOT TIER: Live network element state (Memory Engine)
CREATE TABLE IF NOT EXISTS enterprise_memory.telco_network_state
(
    element_id   String,
    element_type Enum8('router'=1,'switch'=2,'base_station'=3,'fiber_link'=4,'core'=5),
    vendor       String,
    region       String,
    status       Enum8('up'=1,'degraded'=2,'down'=3,'maintenance'=4),
    cpu_pct      Float32,
    mem_pct      Float32,
    traffic_gbps Float32,
    error_rate   Float32,
    last_seen    DateTime64(3) DEFAULT now64()
) ENGINE = Memory;

-- HOT TIER: Active fault investigation workspace (Memory Engine)
CREATE TABLE IF NOT EXISTS enterprise_memory.telco_fault_workspace
(
    fault_id     String,
    element_id   String,
    ts           DateTime64(3),
    metric       String,
    value        Float32,
    threshold    Float32,
    severity     String,
    added_at     DateTime64(3) DEFAULT now64()
) ENGINE = Memory;

-- WARM TIER: Historical network events with vector embeddings (MergeTree)
CREATE TABLE IF NOT EXISTS enterprise_memory.telco_network_events
(
    event_id      UUID          DEFAULT generateUUIDv4(),
    ts            DateTime64(3),
    element_id    String,
    event_type    String,
    description   String,
    root_cause    String,
    resolution    String,
    impact_score  Float32,
    customers_aff UInt32,
    embedding     Array(Float32)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(ts)
ORDER BY (element_id, ts)
;

-- WARM TIER: Network inventory (for PuppyGraph)
CREATE TABLE IF NOT EXISTS enterprise_memory.telco_elements
(
    element_id   String,
    element_type String,
    vendor       String,
    model        String,
    region       String,
    site         String,
    install_date Date,
    criticality  String
) ENGINE = MergeTree()
ORDER BY element_id;

CREATE TABLE IF NOT EXISTS enterprise_memory.telco_connections
(
    from_element String,
    to_element   String,
    link_type    String,
    capacity_gbps Float32,
    latency_ms   Float32
) ENGINE = MergeTree()
ORDER BY (from_element, to_element);

-- ============================================================
-- USE CASE 3: CYBERSECURITY SOC
-- ============================================================

-- HOT TIER: Real-time security event stream (Memory Engine)
CREATE TABLE IF NOT EXISTS enterprise_memory.sec_events_stream
(
    event_id     UUID          DEFAULT generateUUIDv4(),
    ts           DateTime64(3) DEFAULT now64(),
    event_type   String,
    source_system String,
    user_id      String,
    asset_id     String,
    src_ip       String,
    dst_ip       Nullable(String),
    action       String,
    outcome      String,
    severity     Enum8('low'=1,'medium'=2,'high'=3,'critical'=4),
    raw_log      String
) ENGINE = Memory;

-- HOT TIER: Active case investigation workspace (Memory Engine)
CREATE TABLE IF NOT EXISTS enterprise_memory.sec_case_workspace
(
    case_id      String,
    event_id     UUID,
    ts           DateTime64(3),
    event_type   String,
    user_id      String,
    asset_id     String,
    src_ip       String,
    action       String,
    outcome      String,
    severity     String,
    added_at     DateTime64(3) DEFAULT now64()
) ENGINE = Memory;

-- WARM TIER: Threat intelligence with vector embeddings (MergeTree)
CREATE TABLE IF NOT EXISTS enterprise_memory.sec_threat_intel
(
    indicator_id   UUID          DEFAULT generateUUIDv4(),
    indicator_type Enum8('ip'=1,'domain'=2,'hash'=3,'url'=4,'email'=5),
    indicator_val  String,
    threat_actor   String,
    campaign       String,
    ttps           Array(String),
    confidence     Float32,
    description    String,
    embedding      Array(Float32),
    first_seen     DateTime64(3),
    last_seen      DateTime64(3)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(first_seen)
ORDER BY (indicator_type, confidence)
;

-- WARM TIER: Historical security incidents with vector embeddings (MergeTree)
CREATE TABLE IF NOT EXISTS enterprise_memory.sec_historical_incidents
(
    incident_id   UUID          DEFAULT generateUUIDv4(),
    ts            DateTime64(3),
    incident_type String,
    title         String,
    description   String,
    affected_user String,
    affected_asset String,
    attacker_ip   String,
    threat_actor  String,
    ttps          Array(String),
    root_cause    String,
    response      String,
    outcome       String,
    severity      Enum8('low'=1,'medium'=2,'high'=3,'critical'=4),
    embedding     Array(Float32)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(ts)
ORDER BY (severity, ts)
;

-- WARM TIER: Asset inventory (for PuppyGraph)
CREATE TABLE IF NOT EXISTS enterprise_memory.sec_assets
(
    asset_id     String,
    hostname     String,
    asset_type   String,
    criticality  String,
    owner_team   String,
    data_class   String,
    network_zone String,
    os           String
) ENGINE = MergeTree()
ORDER BY asset_id;

-- WARM TIER: User directory (for PuppyGraph)
CREATE TABLE IF NOT EXISTS enterprise_memory.sec_users
(
    user_id      String,
    username     String,
    department   String,
    role         String,
    risk_score   Float32,
    mfa_enabled  UInt8
) ENGINE = MergeTree()
ORDER BY user_id;

-- WARM TIER: User-Asset access relationships (for PuppyGraph)
CREATE TABLE IF NOT EXISTS enterprise_memory.sec_access
(
    user_id      String,
    asset_id     String,
    access_type  String,
    granted_date Date
) ENGINE = MergeTree()
ORDER BY (user_id, asset_id);
