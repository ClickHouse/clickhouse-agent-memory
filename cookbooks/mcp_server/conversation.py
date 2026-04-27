"""
mcp_server/conversation.py
--------------------------
Cross-session conversation-memory tools. Tool names use AI-engineer
vocabulary so a reader recognises the retrieval pattern immediately:

  list_session_messages  -- HOT tier: last N turns of the current chat
  get_conversation_history   -- WARM tier: semantic recall across a user's past chats
  add_memory     -- WARM tier: persist a distilled fact / preference

Both this module and server.py pull `mcp` from mcp_server.app so there
is no circular import.
"""

from __future__ import annotations

from typing import Any

from shared.client import get_ch_client, embed, query_to_dicts, query_with_stats

from mcp_server.app import mcp
from mcp_server.tiers import envelope, timed


# ---------------------------------------------------------------------------
# SQL (kept local to this module; every query has leading + inline comments
# so the agent's SQL fence reads instantly)
# ---------------------------------------------------------------------------

REPLAY_SESSION_SQL = """
    -- HOT tier: replay the last N turns of this chat session.
    -- agent_memory_hot is ENGINE = Memory, so reads are sub-5ms and the
    -- rows vanish on container restart (by design: HOT = working memory).
    SELECT session_id, turn_id, role, content, tool_name, metadata, ts
    FROM enterprise_memory.agent_memory_hot
    WHERE session_id = {session_id:String}
    ORDER BY turn_id DESC
    LIMIT {n:UInt32}
"""


RECALL_MEMORY_SQL = """
    -- WARM tier: SEMANTIC RECALL across every past session for this user.
    -- agent_memory_long is MergeTree + HNSW on content_embedding, so the
    -- ORDER BY cosineDistance LIMIT k is handled by the vector index.
    -- cosineDistance: 0 = identical, 2 = opposite. Lower = more similar.
    SELECT memory_id, session_id, turn_id, role, content,
           memory_type, importance, ts,
           -- Rounded copy for display; the un-rounded form drives the sort.
           round(cosineDistance(content_embedding, {emb}), 4) AS similarity_distance
    FROM enterprise_memory.agent_memory_long
    -- Scope to the calling user's memory only.
    WHERE user_id = {user_id:String}
    -- ORDER BY uses the un-rounded distance so HNSW kicks in.
    ORDER BY cosineDistance(content_embedding, {emb}) ASC
    LIMIT {k:UInt32}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _embed_literal(text: str) -> str:
    vec = embed(text)
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def _insert_memory(
    client,
    user_id: str,
    agent_id: str,
    session_id: str,
    content: str,
    memory_type: str,
    importance: float,
    turn_id: int = 0,
) -> list[float]:
    """Embed + insert a single distilled row into agent_memory_long."""
    emb = embed(content)
    client.insert(
        "enterprise_memory.agent_memory_long",
        [(
            user_id,
            agent_id,
            session_id,
            int(turn_id),
            "assistant",
            content,
            emb,
            memory_type,
            float(importance),
        )],
        column_names=[
            "user_id", "agent_id", "session_id", "turn_id", "role",
            "content", "content_embedding", "memory_type", "importance",
        ],
    )
    return emb


# ---------------------------------------------------------------------------
# HOT -- list_session_messages
# ---------------------------------------------------------------------------

@mcp.tool()
def list_session_messages(session_id: str, n: int = 20) -> dict[str, Any]:
    """HOT tier: replay the last N turns of the current chat session.

    Reads agent_memory_hot (Memory engine) so the last-N window is
    sub-5ms. Use at the start of a turn to rehydrate the LLM without
    running any vector math.

    Args:
        session_id: the session whose transcript to return
        n: number of most recent turns to return (default 20)
    """
    if not session_id:
        raise ValueError("session_id is required")
    client = get_ch_client()
    params = {"session_id": session_id, "n": int(n)}
    (rows, stats), elapsed = timed(query_with_stats, client, REPLAY_SESSION_SQL, params)

    # SQL returns newest-first; flip to chronological for replay.
    rows = list(reversed(rows))

    insights: dict[str, Any] = {
        "session_id": session_id,
        "turns_returned": len(rows),
        "n_requested": n,
    }
    if rows:
        insights["first_turn_id"] = rows[0].get("turn_id")
        insights["last_turn_id"] = rows[-1].get("turn_id")
        insights["last_role"] = rows[-1].get("role")

    precision = {
        "filters_applied": [f"session_id = {session_id}", f"LIMIT {n}"],
        "index_hint": "Memory engine (in-RAM scan of a single session)",
        "embedding_dim": None,
    }
    return envelope(
        tier="HOT",
        domain="conversation",
        operation="list_session_messages",
        sql=REPLAY_SESSION_SQL.strip(),
        latency_ms=elapsed,
        rows=rows,
        insights=insights,
        precision=precision,
        scan_stats=stats,
    )


# ---------------------------------------------------------------------------
# WARM -- get_conversation_history
# ---------------------------------------------------------------------------

@mcp.tool()
def get_conversation_history(user_id: str, query: str, k: int = 5) -> dict[str, Any]:
    """WARM tier: semantic recall across every past session for this user.

    Embeds `query`, runs cosineDistance against agent_memory_long scoped
    to user_id, returns the top-k most semantically similar past turns
    and distilled facts. This is long-term conversation memory.

    Args:
        user_id: the user whose long-term memory to search
        query: free-text question describing what to recall
        k: neighbours (default 5)
    """
    if not user_id:
        raise ValueError("user_id is required")
    if not query:
        raise ValueError("query is required")

    client = get_ch_client()
    emb_literal = _embed_literal(query)
    sql = RECALL_MEMORY_SQL.replace("{emb}", emb_literal)
    params = {"user_id": user_id, "k": int(k)}
    (rows, stats), elapsed = timed(query_with_stats, client, sql, params)

    insights: dict[str, Any] = {
        "user_id": user_id,
        "query_text": query[:160],
        "k": k,
        "matches": len(rows),
    }
    if rows:
        top = rows[0]
        insights["top_memory_type"] = top.get("memory_type")
        insights["top_similarity_distance"] = top.get("similarity_distance")
        insights["top_content"] = (top.get("content") or "")[:240]

    sql_for_display = RECALL_MEMORY_SQL.replace(
        "{emb}", "[<query embedding vector>]"
    ).strip()
    precision = {
        "filters_applied": [
            f"user_id = {user_id}",
            "ORDER BY cosineDistance(content_embedding, query_vec) ASC",
            f"LIMIT {k}",
        ],
        "index_hint": "HNSW vector_similarity on `content_embedding`, scoped by user_id prefix",
        "embedding_dim": 768,
    }
    return envelope(
        tier="WARM",
        domain="conversation",
        operation="get_conversation_history",
        sql=sql_for_display,
        latency_ms=elapsed,
        rows=rows,
        insights=insights,
        precision=precision,
        scan_stats=stats,
    )


# ---------------------------------------------------------------------------
# WARM -- add_memory
# ---------------------------------------------------------------------------

@mcp.tool()
def add_memory(
    user_id: str,
    fact: str,
    agent_id: str = "support-copilot",
    session_id: str = "",
    kind: str = "semantic",
    importance: float = 0.8,
) -> dict[str, Any]:
    """WARM tier: persist a distilled fact to this user's long-term memory.

    Writes a single row to agent_memory_long with memory_type=kind and
    importance. Use for standing preferences ("prefers Gemini"),
    agreed constraints ("SLO 99.9"), or durable notes the agent should
    surface in future sessions.

    Args:
        user_id: the user this fact belongs to
        fact: distilled content to persist
        agent_id: which agent is recording (ai-sre-agent, ai-netops-agent,
            ai-soc-agent, support-copilot). Defaults to support-copilot.
        session_id: optional session this fact was distilled from
        kind: memory_type (episodic | procedural | semantic)
        importance: 0.0 to 1.0, influences future ranking (default 0.8)
    """
    if not user_id:
        raise ValueError("user_id is required")
    if not fact:
        raise ValueError("fact is required")
    kind = (kind or "semantic").lower()
    if kind not in ("episodic", "procedural", "semantic"):
        raise ValueError("kind must be one of: episodic, procedural, semantic")

    agent_id = agent_id or "support-copilot"
    session_id = session_id or ""
    client = get_ch_client()

    def _do_write() -> list[dict]:
        _insert_memory(
            client,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            content=fact,
            memory_type=kind,
            importance=importance,
        )
        return [{
            "user_id": user_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "memory_type": kind,
            "importance": importance,
            "content": fact,
        }]

    rows, elapsed = timed(_do_write)

    insights = {
        "user_id": user_id,
        "agent_id": agent_id,
        "session_id": session_id or None,
        "memory_type": kind,
        "importance": importance,
        "content_preview": fact[:240],
        "persisted": True,
    }

    # Display a representative INSERT (real vector literal is too long to
    # render readably inside the tool card).
    sql_for_display = (
        "-- WARM tier: persist a distilled fact to long-term memory.\n"
        "-- The embed() call happens first; the vector is stored alongside\n"
        "-- the content so future recalls can rank by cosineDistance.\n"
        "INSERT INTO enterprise_memory.agent_memory_long\n"
        "    (user_id, agent_id, session_id, turn_id, role, content,\n"
        "     content_embedding, memory_type, importance)\n"
        "VALUES\n"
        "    ({user_id}, {agent_id}, {session_id}, 0, 'assistant', {fact},\n"
        "     [<embedding vector>], {kind}, {importance})"
    )

    precision = {
        "filters_applied": [
            f"user_id={user_id}",
            f"memory_type={kind}",
            f"importance={importance}",
        ],
        "index_hint": "MergeTree INSERT (partitioned by toYYYYMM(ts))",
        "embedding_dim": 768,
    }
    # Synthetic scan stats for the INSERT: we wrote 1 row.
    write_stats = {"read_rows": 0, "read_bytes": 0, "written_rows": 1, "written_bytes": 0}
    return envelope(
        tier="WARM",
        domain="conversation",
        operation="add_memory",
        sql=sql_for_display,
        latency_ms=elapsed,
        rows=rows,
        insights=insights,
        precision=precision,
        scan_stats=write_stats,
    )
