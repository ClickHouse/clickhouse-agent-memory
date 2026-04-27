"""
tests/integration/conftest.py
-----------------------------
Fixtures for the integration test layer. All integration tests assume a
ClickHouse docker-compose stack is already running and has been seeded
with `make seed`.

Two fixtures are exposed:

  ch_client   -- session-scoped live clickhouse_connect client. Skips the
                 entire integration run if ClickHouse is not reachable.
  seeded      -- session-scoped guard that verifies a handful of seeded
                 tables actually have rows. Skips if they do not.
"""

from __future__ import annotations

import os

import pytest


# Every integration test picks up this marker automatically.
pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Live ClickHouse fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def ch_client():
    """Return a live ClickHouse client or skip the whole integration module.

    We intentionally skip (rather than error) so the suite is still green on
    laptops where the docker-compose stack is not running. Unit tests remain
    fully independent of this fixture.
    """
    try:
        from shared.client import get_ch_client
        client = get_ch_client()
        # Probe the connection with a cheap query.
        client.query("SELECT 1")
    except Exception as exc:
        host = os.getenv("CLICKHOUSE_HOST", "localhost")
        port = os.getenv("CLICKHOUSE_PORT", "8123")
        pytest.skip(
            f"ClickHouse not reachable at {host}:{port} ({exc}). "
            f"Bring up the cookbooks stack: cd cookbooks && make start && make seed."
        )
    yield client
    try:
        client.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Seed-data guard
# ---------------------------------------------------------------------------

REQUIRED_SEEDED_TABLES = (
    "obs_events_stream",
    "obs_historical_incidents",
    "agent_memory_hot",
    "agent_memory_long",
)


@pytest.fixture(scope="session")
def seeded(ch_client):
    """Verify the four tables touched by the suite have rows.

    If any are empty we skip -- the contract is: tests do NOT reseed, they
    expect `make seed` to have been run.
    """
    empties = []
    for table in REQUIRED_SEEDED_TABLES:
        try:
            result = ch_client.query(
                f"SELECT count() FROM enterprise_memory.{table}"
            )
            count = result.result_rows[0][0] if result.result_rows else 0
        except Exception as exc:
            empties.append(f"{table} (query failed: {exc})")
            continue
        if count == 0:
            empties.append(f"{table} (0 rows)")
    if empties:
        pytest.skip(
            "Run make seed first. Empty seeded tables: " + ", ".join(empties)
        )
    return ch_client


@pytest.fixture(scope="session")
def seeded_fresh(seeded):
    """Like `seeded`, but also verifies HOT-tier events are fresh.

    Tests that assert on `now() - INTERVAL ... MINUTE` windows (e.g. the
    comparison agent's live-incident scenario) should depend on this
    fixture so a stale seed produces a clear skip message instead of an
    opaque in-test failure.

    Defines "fresh" as max(ts) within 24h. The comparison test widens its
    own HOT-window to 24h via COMPARE_HOT_WINDOW_MINUTES; anything beyond
    that is a genuinely stale seed and should be re-seeded, not tested.
    """
    result = seeded.query(
        "SELECT dateDiff('minute', max(ts), now()) AS lag_minutes "
        "FROM enterprise_memory.obs_events_stream"
    )
    lag = result.result_rows[0][0] if result.result_rows else None
    if lag is None:
        pytest.skip("obs_events_stream is empty; run `make seed`.")
    if lag > 1440:  # > 24h old
        pytest.skip(
            f"obs_events_stream is {lag} minutes stale (> 24h). "
            f"Run `make seed` to refresh Memory-engine HOT tables."
        )
    return seeded
