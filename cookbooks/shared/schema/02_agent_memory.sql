-- ============================================================
-- Enterprise Agent Memory -- Generic conversation memory layer
-- ============================================================
-- Three tables that make the "memory layer for AI agents" story
-- concrete: a HOT per-session scratchpad, a WARM persistent
-- semantically-searchable transcript, and a durable knowledge base.
--
-- These live alongside the domain tables defined in 01_schema.sql
-- inside the enterprise_memory database. They are the generic
-- agent-memory surface referenced by memory_conversation_window,
-- memory_conversation_recall, and memory_conversation_remember.
-- ============================================================

CREATE DATABASE IF NOT EXISTS enterprise_memory;

-- ------------------------------------------------------------
-- HOT TIER: live session scratchpad (volatile, sub-5ms)
-- ------------------------------------------------------------
-- Every user / assistant / tool turn in the current chat lands here
-- first. Memory engine so the write is sub-millisecond and the last
-- N turns are cheap to replay as working memory for the LLM.
CREATE TABLE IF NOT EXISTS enterprise_memory.agent_memory_hot
(
    session_id    String,
    turn_id       UInt32,
    role          LowCardinality(String),
    content       String,
    tool_name     LowCardinality(String) DEFAULT '',
    metadata      String                 DEFAULT '',
    ts            DateTime64(3)          DEFAULT now64()
) ENGINE = Memory;

-- ------------------------------------------------------------
-- WARM TIER: persistent, semantically searchable conversation memory
-- ------------------------------------------------------------
-- Distilled / flushed rows from agent_memory_hot, plus deliberate
-- "remember this" writes from the agent. Embeddings enable cross
-- session recall via cosineDistance. HNSW vector_similarity index
-- keeps recall in the 50-500ms band as volume grows.
--
-- Requires: allow_experimental_vector_similarity_index=1 (set by the
-- seeder for every statement in this file) on ClickHouse 24.8-26.x.
-- On 26.3+ the signature is vector_similarity(method, distance, dim).
CREATE TABLE IF NOT EXISTS enterprise_memory.agent_memory_long
(
    memory_id          UUID                   DEFAULT generateUUIDv4(),
    user_id            String,
    agent_id           LowCardinality(String) DEFAULT '',
    session_id         String,
    turn_id            UInt32                 DEFAULT 0,
    role               LowCardinality(String) DEFAULT 'assistant',
    content            String,
    content_embedding  Array(Float32),
    memory_type        LowCardinality(String) DEFAULT 'episodic',
    importance         Float32                DEFAULT 0.5,
    ts                 DateTime64(3)          DEFAULT now64(),
    INDEX embedding_idx content_embedding
        TYPE vector_similarity('hnsw', 'cosineDistance', 768)
        GRANULARITY 1000
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(ts)
ORDER BY (user_id, ts, session_id);

-- ------------------------------------------------------------
-- WARM TIER: durable reference knowledge
-- ------------------------------------------------------------
-- Generic knowledge-base articles the agent can retrieve with
-- semantic search. Distinct from the domain historical_* tables:
-- those are past-incident records, this is curated reference
-- material (how-tos, policy notes, playbook fragments).
CREATE TABLE IF NOT EXISTS enterprise_memory.knowledge_base
(
    article_id         UUID                   DEFAULT generateUUIDv4(),
    title              String,
    content            String,
    content_embedding  Array(Float32),
    category           LowCardinality(String) DEFAULT 'general',
    tags               Array(String)          DEFAULT [],
    created_at         DateTime64(3)          DEFAULT now64(),
    updated_at         DateTime64(3)          DEFAULT now64(),
    access_count       UInt32                 DEFAULT 0,
    INDEX embedding_idx content_embedding
        TYPE vector_similarity('hnsw', 'cosineDistance', 768)
        GRANULARITY 1000
) ENGINE = MergeTree()
ORDER BY (category, article_id);
