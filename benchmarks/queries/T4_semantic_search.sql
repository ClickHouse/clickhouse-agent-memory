-- Tool: semantic_search (WARM tier, MergeTree + HNSW)
-- Powers: "Find things like this" — tenant-filtered semantic search.
-- Features exercised: primary index (user_id, ts, session_id) for filter prune,
-- then HNSW vector_similarity('hnsw', 'cosineDistance', 768) for rank.
-- The query vector is a real embedding pulled from the same table at seed time.
SELECT
    memory_id,
    user_id,
    agent_id,
    content,
    memory_type,
    ts,
    cosineDistance(content_embedding, {query_vec:Array(Float32)}) AS distance
FROM enterprise_memory.agent_memory_long
WHERE user_id = {user_id:String}
ORDER BY distance ASC
LIMIT 5
