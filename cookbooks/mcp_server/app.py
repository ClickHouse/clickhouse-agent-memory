"""
mcp_server/app.py
-----------------
Owns the FastMCP instance and server instructions.

Split out from server.py so every tool module (server.py for the five
domain tools, conversation.py for the three conversation-memory tools)
imports `mcp` from here. That kills the earlier circular import between
server.py and conversation.py.

Also runs a best-effort embedding-dimension sanity check when the server
starts, so a drift between EMBED_DIM and the seeded vectors surfaces as
a visible warning instead of silently poisoning cosineDistance.
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP


SERVER_INSTRUCTIONS = """\
Enterprise Agent Memory provides one ClickHouse-backed memory cluster with
three tiers. Call tools in tier order to assemble incident / fault / case
context for your assigned domain (observability, telco, cybersecurity), or
to maintain continuity across user sessions via the conversation-memory
tools:

  Domain tier tools
    memory_hot_scan          -- live event stream (Memory engine, sub-5ms)
    memory_hot_workspace     -- materialise a per-case workspace
    memory_warm_search       -- vector similarity over historical incidents
    memory_warm_lookup       -- full playbook / threat intel by id
    memory_graph_traverse    -- blast radius / topology / lateral movement

  Conversation-memory tools
    memory_conversation_window   -- last N turns in the current session (HOT)
    memory_conversation_recall   -- cross-session semantic recall (WARM)
    memory_conversation_remember -- persist a distilled preference (WARM)

Every response includes a banner_markdown field. Echo it verbatim before
your text reply so the user can see which memory tier answered.
"""


mcp = FastMCP(
    "enterprise-agent-memory",
    instructions=SERVER_INSTRUCTIONS,
    stateless_http=True,
    json_response=True,
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8765")),
)


def check_embedding_dim_consistency() -> None:
    """Warn loudly if EMBED_DIM no longer matches the seeded vector length.

    A mismatch makes cosineDistance silently meaningless: every row still
    returns a number, but the ranking is nonsense. This check is
    best-effort: if ClickHouse is not reachable yet, we stay quiet and
    let the normal failure path surface.
    """
    expected = int(os.getenv("EMBED_DIM", "768"))
    try:
        # Late import so that importing app.py in test contexts does not
        # require a ClickHouse server to exist.
        from shared.client import get_ch_client

        client = get_ch_client()
        probes = [
            "enterprise_memory.obs_historical_incidents",
            "enterprise_memory.agent_memory_long",
            "enterprise_memory.knowledge_base",
        ]
        for table in probes:
            try:
                result = client.query(
                    f"SELECT length(embedding) FROM {table} LIMIT 1"
                    if "embedding" in table or "incidents" in table
                    else f"SELECT length(content_embedding) FROM {table} LIMIT 1"
                )
                if result.result_rows:
                    actual = int(result.result_rows[0][0])
                    if actual != expected:
                        print(
                            f"WARNING: embedding dim mismatch on {table}. "
                            f"EMBED_DIM={expected} but seeded rows have "
                            f"dim={actual}. cosineDistance ranking will be "
                            f"meaningless. Reseed after changing EMBED_DIM, "
                            f"or set EMBED_DIM={actual} in your .env.",
                            file=sys.stderr,
                            flush=True,
                        )
                    return
            except Exception:
                continue
    except Exception as e:
        print(f"Embedding-dim check skipped: {e}", file=sys.stderr, flush=True)
