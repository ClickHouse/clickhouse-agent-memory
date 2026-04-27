-- Tool: replay_session (WARM tier, MergeTree range scan)
-- Powers: Walk a past agent session turn by turn for debugging or learning.
-- Features exercised: MergeTree filter on session_id, ORDER BY turn_id ASC.
SELECT
    turn_id,
    role,
    agent_id,
    content,
    memory_type,
    ts
FROM enterprise_memory.agent_memory_long
WHERE session_id = {session_id:String}
ORDER BY turn_id ASC
