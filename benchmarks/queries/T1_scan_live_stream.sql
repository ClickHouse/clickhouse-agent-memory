-- Tool: scan_live_stream (HOT tier, Memory engine)
-- Powers: "Right now" questions. Last N events for one service.
-- Features exercised: Memory engine, WHERE + ORDER BY ts DESC + LIMIT.
SELECT
    ts,
    service,
    host,
    level,
    message
FROM enterprise_memory.obs_events_stream
WHERE service = {service:String}
ORDER BY ts DESC
LIMIT 50
