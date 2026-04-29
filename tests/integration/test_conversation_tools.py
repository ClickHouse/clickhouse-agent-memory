"""
tests/integration/test_conversation_tools.py
--------------------------------------------
End-to-end coverage of the three conversation memory tools:

  - list_session_messages     (HOT)
  - get_conversation_history     (WARM, vector)
  - add_memory   (WARM, write)

Includes a round-trip: remember then recall, verifying the newly-written
fact shows up in a subsequent top-k search.
"""

from __future__ import annotations

import uuid

import pytest

# Importing server registers conversation tools against the shared FastMCP
# instance via a side-effect import at the bottom of server.py.
import mcp_server.server  # noqa: F401  side-effect: registers tools
from mcp_server.conversation import (
    list_session_messages,
    get_conversation_history,
    add_memory,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# HOT: window replay
# ---------------------------------------------------------------------------

def test_list_session_messages_returns_chronological_turns(seeded):
    """sess-001 is a primary seeded session; window should return its turns."""
    env = list_session_messages(session_id="sess-001", n=20)
    assert env["tier"] == "HOT"
    assert env["domain"] == "conversation"
    assert env["operation"] == "list_session_messages"
    assert env["row_count"] > 0, "sess-001 has no rows; did seeding run?"
    # The SQL returns newest-first; the tool reverses to chronological.
    turn_ids = [r["turn_id"] for r in env["rows_preview"]]
    assert turn_ids == sorted(turn_ids), (
        f"Expected ascending turn_ids in rows_preview, got {turn_ids}"
    )
    assert env["tier"] == "HOT"


def test_list_session_messages_requires_session_id(seeded):
    with pytest.raises(ValueError):
        list_session_messages(session_id="", n=20)


# ---------------------------------------------------------------------------
# WARM: semantic recall
# ---------------------------------------------------------------------------

def test_get_conversation_history_finds_prior_turns(seeded):
    """u-maruthi has seeded turns on svc-payments pool issues."""
    env = get_conversation_history(
        user_id="u-maruthi",
        query="svc-payments connection pool",
        k=5,
    )
    assert env["tier"] == "WARM"
    assert env["domain"] == "conversation"
    assert env["operation"] == "get_conversation_history"
    assert env["row_count"] >= 1
    assert env["tier"] == "WARM"
    # Every row has a similarity_distance score.
    for row in env["rows_preview"]:
        assert "similarity_distance" in row


def test_get_conversation_history_requires_user_id(seeded):
    with pytest.raises(ValueError):
        get_conversation_history(user_id="", query="anything", k=5)


def test_get_conversation_history_requires_query(seeded):
    with pytest.raises(ValueError):
        get_conversation_history(user_id="u-maruthi", query="", k=5)


# ---------------------------------------------------------------------------
# WARM: distilled write
# ---------------------------------------------------------------------------

def test_add_memory_records_agent_id(seeded):
    """Regression test for the agent_id attribution fix.

    Previously agent_id defaulted to support-copilot regardless of what the
    caller passed. Now it must reflect the explicit agent_id argument.
    """
    env = add_memory(
        user_id="u-test-roundtrip",
        fact="user prefers terse bullet points",
        agent_id="ai-sre-agent",
        kind="semantic",
        importance=0.9,
    )
    assert env["tier"] == "WARM"
    assert env["insights"]["agent_id"] == "ai-sre-agent"
    assert env["insights"]["memory_type"] == "semantic"
    assert env["insights"]["persisted"] is True


def test_add_memory_validates_required_fields(seeded):
    # Empty user_id fails.
    with pytest.raises(ValueError):
        add_memory(user_id="", fact="anything")
    # Empty fact fails.
    with pytest.raises(ValueError):
        add_memory(user_id="x", fact="")
    # Unknown memory_type fails.
    with pytest.raises(ValueError):
        add_memory(
            user_id="x", fact="y", kind="unknown"
        )


# ---------------------------------------------------------------------------
# Round-trip: remember -> recall
# ---------------------------------------------------------------------------

def test_conversation_round_trip(seeded):
    """Write a distinctive fact, then recall it and confirm it is top-k.

    Uses a unique user_id per test invocation so repeated runs do not
    accumulate rows that muddy the top-k ranking.
    """
    user_id = f"u-test-roundtrip-{uuid.uuid4().hex[:8]}"
    fact = "the project uses ClickHouse 24.3 for its agent memory demo"

    write_env = add_memory(
        user_id=user_id,
        fact=fact,
        agent_id="ai-sre-agent",
    )
    assert write_env["insights"]["persisted"] is True

    recall_env = get_conversation_history(
        user_id=user_id,
        query="ClickHouse version",
        k=3,
    )
    assert recall_env["row_count"] >= 1
    contents = [r.get("content", "") for r in recall_env["rows_preview"]]
    assert any(fact in c for c in contents), (
        f"newly-written fact not in top-3 recall. Got: {contents}"
    )
