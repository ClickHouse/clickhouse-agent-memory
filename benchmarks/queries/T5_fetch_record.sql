-- Tool: fetch_record (WARM tier, MergeTree point lookup)
-- Powers: Hydrate one full incident after ranking narrowed it to an id.
-- Features exercised: MergeTree primary-key filter, return single row.
SELECT
    incident_id,
    ts,
    title,
    description,
    affected_services,
    root_cause,
    resolution,
    severity,
    duration_min
FROM enterprise_memory.obs_historical_incidents
WHERE incident_id = {incident_id:UUID}
