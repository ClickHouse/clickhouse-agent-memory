-- Tool: open_investigation (HOT tier, Memory engine)
-- Powers: Per-session scratchpad — accumulate findings during a session.
-- Features exercised: Memory engine, WHERE on incident_id, return all rows sorted.
SELECT
    incident_id,
    ts,
    service,
    level,
    message,
    added_at
FROM enterprise_memory.obs_incident_workspace
WHERE incident_id = {incident_id:String}
ORDER BY added_at DESC
