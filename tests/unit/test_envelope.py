"""
tests/unit/test_envelope.py
---------------------------
Pure-function tests for the thin envelope returned by mcp_server.tiers.

The envelope is intentionally minimal. Only the fields the agent needs
to write a good narrative should be present. Redundant chrome fields
(banner_markdown, tier_banner, tier_engine, tier_latency_profile,
truncated, next_tool_hint) were dropped because LibreChat already
renders tool cards with its own visual chrome.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from mcp_server.tiers import envelope, _serialise_rows


pytestmark = pytest.mark.unit


class TestEnvelopeShape:
    """envelope() returns a fixed, thin key set every time."""

    EXPECTED_KEYS = {
        "tier",
        "domain",
        "operation",
        "latency_ms",
        "row_count",
        "insights",
        "precision",
        "rows_preview",
        "sql",
    }

    def test_contains_exactly_the_documented_keys(self):
        env = envelope(
            tier="HOT",
            domain="observability",
            operation="search_events",
            sql="SELECT 1",
            latency_ms=0.8,
            rows=[],
        )
        assert set(env.keys()) == self.EXPECTED_KEYS

    def test_sql_is_dedented_and_stripped(self):
        # SQL is quoted in a ```sql code fence in the agent's reply, so
        # it must be readable, not prefixed with Python indentation.
        sql = """
            SELECT event_id, ts
            FROM obs_events_stream
            WHERE level = 'ERROR'
        """
        env = envelope("HOT", "obs", "op", sql, 1.0, [])
        # Dedent strips the common leading whitespace, strip() removes
        # the trailing + leading blank lines.
        assert env["sql"].startswith("SELECT event_id, ts")
        assert "    SELECT" not in env["sql"]
        assert not env["sql"].endswith("\n")

    def test_latency_ms_rounded_to_two_decimals(self):
        env = envelope("HOT", "obs", "op", "SELECT 1", 1.23456, [])
        assert env["latency_ms"] == 1.23

    def test_insights_defaults_to_empty_dict(self):
        env = envelope("HOT", "obs", "op", "SELECT 1", 1.0, [])
        assert env["insights"] == {}

    def test_insights_passthrough(self):
        payload = {"case_id": "INC-42", "events_loaded": 7}
        env = envelope("HOT", "obs", "op", "SELECT 1", 1.0, [], insights=payload)
        assert env["insights"] == payload

    def test_tier_domain_operation_passthrough(self):
        env = envelope(
            tier="WARM",
            domain="telco",
            operation="semantic_search",
            sql="SELECT 1",
            latency_ms=134.7,
            rows=[],
        )
        assert env["tier"] == "WARM"
        assert env["domain"] == "telco"
        assert env["operation"] == "semantic_search"


class TestEnvelopePrecision:
    """The precision block carries filters_applied + scan stats."""

    def test_precision_defaults_present_when_nothing_passed(self):
        env = envelope("HOT", "obs", "op", "SELECT 1", 1.0, [])
        p = env["precision"]
        assert p["filters_applied"] == []
        assert p["index_hint"] == "unknown"
        assert p["embedding_dim"] is None
        assert p["rows_read"] is None
        assert p["bytes_read"] is None
        assert p["rows_returned"] == 0
        assert p["selectivity"] == "no rows scanned"

    def test_precision_filters_and_index_hint_passthrough(self):
        env = envelope(
            "WARM", "obs", "semantic_search", "SELECT 1", 45.0, [],
            precision={
                "filters_applied": ["ORDER BY cosineDistance ASC", "LIMIT 3"],
                "index_hint": "HNSW",
                "embedding_dim": 768,
            },
        )
        p = env["precision"]
        assert "LIMIT 3" in p["filters_applied"]
        assert p["index_hint"] == "HNSW"
        assert p["embedding_dim"] == 768

    def test_scan_stats_populate_rows_and_bytes_read(self):
        env = envelope(
            "HOT", "obs", "search_events", "SELECT 1", 1.0,
            [{"a": 1}, {"a": 2}],
            scan_stats={"read_rows": 47, "read_bytes": 2187},
        )
        p = env["precision"]
        assert p["rows_read"] == 47
        assert p["bytes_read"] == 2187
        assert p["rows_returned"] == 2
        # 2 / 47 -> 4.26% selectivity (returned is a small slice of scanned).
        assert "%" in p["selectivity"]

    def test_write_path_selectivity_is_not_a_percentage(self):
        env = envelope(
            "WARM", "conversation", "add_memory", "INSERT ...", 20.0,
            [{"user_id": "u1"}],
            scan_stats={"read_rows": 0, "read_bytes": 0, "written_rows": 1},
        )
        p = env["precision"]
        # Write: no read_rows, so selectivity falls back to "n/a".
        assert p["selectivity"].startswith("n/a")
        assert p["written_rows"] == 1


class TestEnvelopeRows:
    """row_count and rows_preview behaviour (preview cap is 3)."""

    def test_empty_rows(self):
        env = envelope("HOT", "obs", "op", "SELECT 1", 1.0, [])
        assert env["row_count"] == 0
        assert env["rows_preview"] == []

    def test_under_preview_cap(self):
        # 2 rows fit under the 3-row preview cap.
        rows = [{"idx": i} for i in range(2)]
        env = envelope("HOT", "obs", "op", "SELECT 1", 1.0, rows)
        assert env["row_count"] == 2
        assert len(env["rows_preview"]) == 2

    def test_exactly_three_rows(self):
        rows = [{"idx": i} for i in range(3)]
        env = envelope("HOT", "obs", "op", "SELECT 1", 1.0, rows)
        assert env["row_count"] == 3
        assert len(env["rows_preview"]) == 3

    def test_more_than_three_rows_previewed_at_three(self):
        rows = [{"idx": i} for i in range(25)]
        env = envelope("HOT", "obs", "op", "SELECT 1", 1.0, rows)
        assert env["row_count"] == 25
        assert len(env["rows_preview"]) == 3


class TestSerialiseRows:
    """_serialise_rows handles embeddings, datetimes, UUIDs, scalars."""

    def test_embedding_list_becomes_placeholder_string(self):
        rows = [{"content": "hi", "embedding": [0.1, 0.2, 0.3, 0.4]}]
        out = _serialise_rows(rows)
        assert out[0]["embedding"] == "<vector[4]>"
        assert out[0]["content"] == "hi"

    def test_content_embedding_also_hidden(self):
        # agent_memory_long uses `content_embedding` as the vector column.
        rows = [{"content_embedding": [0.5, 0.5]}]
        out = _serialise_rows(rows)
        assert out[0]["content_embedding"] == "<vector[2]>"

    def test_embedding_tuple_becomes_placeholder_string(self):
        rows = [{"embedding": (0.0, 0.0)}]
        out = _serialise_rows(rows)
        assert out[0]["embedding"] == "<vector[2]>"

    def test_datetime_converted_to_isoformat(self):
        ts = datetime(2026, 4, 18, 12, 30, 45)
        rows = [{"ts": ts, "event": "login"}]
        out = _serialise_rows(rows)
        assert out[0]["ts"] == ts.isoformat()

    def test_uuid_converted_via_str(self):
        u = uuid.uuid4()
        rows = [{"event_id": u}]
        out = _serialise_rows(rows)
        assert out[0]["event_id"] == str(u)

    def test_scalars_and_none_pass_through(self):
        rows = [{"ok": True, "count": 5, "score": 0.5, "note": None, "name": "svc"}]
        out = _serialise_rows(rows)
        assert out[0] == {"ok": True, "count": 5, "score": 0.5, "note": None, "name": "svc"}

    def test_list_and_tuple_values_become_lists(self):
        rows = [{"ttps": ("T1566", "T1078"), "services": ["a", "b"]}]
        out = _serialise_rows(rows)
        assert out[0]["ttps"] == ["T1566", "T1078"]
        assert out[0]["services"] == ["a", "b"]

    def test_preview_cap_enforced_via_envelope(self):
        # Preview cap is 3 rows; each row goes through _serialise_rows.
        rows = [{"idx": i, "embedding": [0.1, 0.2]} for i in range(15)]
        env = envelope("HOT", "obs", "op", "SELECT 1", 1.0, rows)
        assert len(env["rows_preview"]) == 3
        for r in env["rows_preview"]:
            assert r["embedding"] == "<vector[2]>"
