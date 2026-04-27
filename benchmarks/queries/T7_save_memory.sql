-- Tool: save_memory (WARM tier, MergeTree INSERT)
-- Powers: Agent writes a durable memory. Embeddings supplied pre-computed.
-- Features exercised: MergeTree INSERT, HNSW maintained incrementally.
-- Note: INSERT is measured separately; read_rows is 0 by definition,
-- the meaningful metric here is wall-clock latency.
INSERT INTO enterprise_memory.benchmark_writes
    (user_id, agent_id, session_id, turn_id, role, content, content_embedding, memory_type, importance)
SELECT
    {user_id:String}            AS user_id,
    {agent_id:String}           AS agent_id,
    {session_id:String}         AS session_id,
    {turn_id:UInt32}            AS turn_id,
    'assistant'                 AS role,
    {content:String}            AS content,
    {embedding:Array(Float32)}  AS content_embedding,
    'episodic'                  AS memory_type,
    0.5                         AS importance
