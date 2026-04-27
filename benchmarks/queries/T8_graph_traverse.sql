-- Tool: graph_traverse (GRAPH tier, recursive CTE over MergeTree edges)
-- Powers: "What breaks if X fails?" — 2-hop blast radius on obs_dependencies.
-- Features exercised: recursive CTE, JOIN on edge table, filter during walk.
WITH RECURSIVE walk AS
(
    SELECT from_service, to_service, 1 AS hop
    FROM enterprise_memory.obs_dependencies
    WHERE from_service = {service:String}

    UNION ALL

    SELECT e.from_service, e.to_service, w.hop + 1
    FROM enterprise_memory.obs_dependencies AS e
    INNER JOIN walk AS w ON e.from_service = w.to_service
    WHERE w.hop < 2
)
SELECT DISTINCT to_service, hop
FROM walk
ORDER BY hop, to_service
