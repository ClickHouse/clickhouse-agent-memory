-- Tool: recall_memory (HOT tier, Memory engine)
-- Powers: Current-session conversation recall. Sub-5ms, no HNSW, exact cosine.
-- Features exercised: Memory engine, WHERE session_id, ORDER BY ts DESC.
SELECT
    turn_id,
    role,
    content,
    tool_name,
    ts
FROM enterprise_memory.agent_memory_hot
WHERE session_id = {session_id:String}
ORDER BY ts DESC
LIMIT 20
