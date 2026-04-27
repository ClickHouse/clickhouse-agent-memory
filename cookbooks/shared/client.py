"""
shared/client.py
----------------
Shared ClickHouse client, multi-provider embedding + LLM generation,
and rich CLI formatting utilities used across all cookbook demos.
"""

import os
import time
import math
import random
import hashlib
from typing import Any

import clickhouse_connect
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax
from rich.columns import Columns
from rich.text import Text
from rich.rule import Rule
from rich import box

# -- Console ------------------------------------------------------------------
console = Console()

# -- Tier colour and label constants ------------------------------------------

TIER_STYLES = {
    "HOT":    {"colour": "red",    "icon": ">>>", "label": "HOT MEMORY",   "desc": "ClickHouse Memory Engine | sub-5ms | volatile"},
    "WARM":   {"colour": "yellow", "icon": "~~~", "label": "WARM MEMORY",  "desc": "MergeTree + Vector Search | 50-500ms | persistent"},
    "GRAPH":  {"colour": "green",  "icon": "ooo", "label": "GRAPH MEMORY", "desc": "SQL JOINs on MergeTree | 10-100ms | relationships"},
    "RESULT": {"colour": "blue",   "icon": "***", "label": "RESULT",       "desc": "Synthesised context for AI agent"},
}


# -- ClickHouse connection ----------------------------------------------------

def get_ch_client():
    """Return a ClickHouse client using environment variables or defaults."""
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        database=os.getenv("CLICKHOUSE_DB", "enterprise_memory"),
    )


# -- Embedding helpers --------------------------------------------------------

EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))


def _deterministic_embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    """
    Deterministic pseudo-embedding from text using seeded hashing.
    Used as fallback when no provider API key is configured.
    """
    words = text.lower().split()
    vec = [0.0] * dim
    for word in words:
        seed = int(hashlib.md5(word.encode()).hexdigest(), 16) % (2**31)
        rng = random.Random(seed)
        for i in range(dim):
            vec[i] += rng.gauss(0, 1)
    magnitude = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / magnitude for v in vec]


def embed(text: str) -> list[float]:
    """
    Generate an embedding vector using the configured provider.
    Falls back to deterministic local embedder when no provider is set.

    Supported providers (via EMBEDDING_PROVIDER env var):
      gemini, openai, ollama, vllm
    """
    provider = os.getenv("EMBEDDING_PROVIDER", "")
    model = os.getenv("EMBEDDING_MODEL", "")
    if not provider or not model:
        return _deterministic_embed(text, EMBED_DIM)
    try:
        if provider == "gemini":
            from google import genai
            client = genai.Client(api_key=os.getenv("GOOGLE_KEY"))
            result = client.models.embed_content(
                model=model,
                contents=text,
                config={"output_dimensionality": EMBED_DIM},
            )
            return result.embeddings[0].values
        elif provider == "openai":
            from openai import OpenAI
            client = OpenAI()
            result = client.embeddings.create(model=model, input=text)
            return result.data[0].embedding
        elif provider == "ollama":
            from openai import OpenAI
            client = OpenAI(
                base_url=os.getenv("OLLAMA_BASE_URL", "http://ollama:11434") + "/v1",
                api_key="not-needed",
            )
            result = client.embeddings.create(model=model, input=text)
            return result.data[0].embedding
        elif provider == "vllm":
            from openai import OpenAI
            client = OpenAI(
                base_url=os.getenv("VLLM_BASE_URL", "http://vllm:8000") + "/v1",
                api_key="not-needed",
            )
            result = client.embeddings.create(model=model, input=text)
            return result.data[0].embedding
        else:
            return _deterministic_embed(text, EMBED_DIM)
    except Exception as e:
        console.print(f"[yellow]Embedding fallback ({e})[/yellow]")
        return _deterministic_embed(text, EMBED_DIM)


def generate(messages: list[dict]) -> str:
    """
    Generate a text response from the configured LLM provider.

    Supported providers (via LLM_PROVIDER env var):
      gemini, openai, anthropic, ollama, vllm
    """
    provider = os.getenv("LLM_PROVIDER", "")
    model = os.getenv("LLM_MODEL", "")
    if not provider or not model:
        return "[LLM_PROVIDER and LLM_MODEL not configured -- set them in .env]"

    if provider == "gemini":
        from google import genai
        client = genai.Client(api_key=os.getenv("GOOGLE_KEY"))
        response = client.models.generate_content(
            model=model, contents=messages[-1]["content"]
        )
        return response.text
    elif provider == "openai":
        from openai import OpenAI
        client = OpenAI()
        response = client.chat.completions.create(model=model, messages=messages)
        return response.choices[0].message.content
    elif provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic()
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_msgs = [m for m in messages if m["role"] != "system"]
        response = client.messages.create(
            model=model, max_tokens=4096, system=system, messages=user_msgs
        )
        return response.content[0].text
    elif provider in ("ollama", "vllm"):
        from openai import OpenAI
        base = os.getenv(
            "OLLAMA_BASE_URL" if provider == "ollama" else "VLLM_BASE_URL"
        ) + "/v1"
        client = OpenAI(base_url=base, api_key="not-needed")
        response = client.chat.completions.create(model=model, messages=messages)
        return response.choices[0].message.content
    else:
        return f"[Unknown LLM_PROVIDER: {provider}]"


def cosine_distance(a: list[float], b: list[float]) -> float:
    """Compute cosine distance between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 1.0
    return 1.0 - dot / (mag_a * mag_b)


# -- Pretty-print helpers ----------------------------------------------------

def print_welcome():
    """Print the main welcome banner with architecture overview."""
    arch = """
    +-----------------------------------------------------------------------+
    |             ENTERPRISE AGENT MEMORY ARCHITECTURE                      |
    |                                                                       |
    |  +-------------------+  +---------------------+  +-----------------+  |
    |  |    HOT MEMORY     |  |    WARM MEMORY      |  |  GRAPH MEMORY   |  |
    |  |  Memory Engine    |  |  MergeTree + HNSW   |  |  SQL JOINs on   |  |
    |  |  sub-5ms latency  |  |  cosineDistance()   |  |  MergeTree      |  |
    |  |  volatile/live    |  |  50-500ms latency   |  |  3-10ms at 2hop |  |
    |  +--------+----------+  +---------+-----------+  +--------+--------+  |
    |           |                       |                       |           |
    |           +----------+------------+-----------+-----------+           |
    |                      |                        |                       |
    |                      v                        v                       |
    |                +---------------------------------------+              |
    |                |   ClickHouse 26.3 (single cluster)    |              |
    |                +---------------------------------------+              |
    +-----------------------------------------------------------------------+

    Three memory tiers, one ClickHouse cluster, three enterprise use cases.
    """
    console.print(Panel(
        arch,
        title="[bold white] Enterprise Agent Memory [/bold white]",
        subtitle="[dim]Three-Tier Memory Architecture for AI Agents[/dim]",
        border_style="bright_cyan",
        padding=(0, 2),
    ))


def print_header(title: str, subtitle: str = ""):
    """Print a cookbook header with title and subtitle."""
    console.print()
    console.print(Panel(
        f"[bold cyan]{title}[/bold cyan]\n[dim]{subtitle}[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))


def print_tier_banner(tier: str):
    """Print a prominent tier transition banner."""
    style = TIER_STYLES.get(tier, TIER_STYLES["RESULT"])
    colour = style["colour"]
    label = style["label"]
    desc = style["desc"]
    icon = style["icon"]
    console.print()
    console.print(Rule(
        f"[bold {colour}] {icon} {label} {icon} [/bold {colour}]",
        style=colour,
    ))
    console.print(f"  [dim]{desc}[/dim]")


def print_step(step: int, total: int, label: str, memory_type: str):
    """Print a step indicator with memory tier badge."""
    style = TIER_STYLES.get(memory_type, TIER_STYLES["RESULT"])
    colour = style["colour"]
    badge = style["label"]
    console.print(
        f"\n  [bold white]Step {step}/{total}[/bold white]  "
        f"[bold {colour} on {colour}] {badge} [/bold {colour} on {colour}]  "
        f"[white]{label}[/white]"
    )


def print_sql(sql: str):
    """Print a SQL query with syntax highlighting."""
    console.print(Syntax(sql.strip(), "sql", theme="monokai", line_numbers=False, padding=1))


def print_results(rows: list[dict], title: str = "Results"):
    """Print query results in a formatted table."""
    if not rows:
        console.print(f"  [dim]  -> No results[/dim]")
        return
    t = Table(title=title, box=box.SIMPLE_HEAD, show_lines=True, padding=(0, 1))
    for col in rows[0].keys():
        t.add_column(str(col), style="cyan", overflow="fold", max_width=60)
    for row in rows:
        t.add_row(*[str(v)[:120] for v in row.values()])
    console.print(t)


def print_insight(label: str, value: str):
    """Print a key insight line."""
    console.print(f"    [bold green]>[/bold green] [bold]{label}:[/bold] {value}")


def print_query_time(elapsed_ms: float, engine_desc: str):
    """Print query timing with engine description."""
    if elapsed_ms < 10:
        colour = "green"
    elif elapsed_ms < 100:
        colour = "yellow"
    else:
        colour = "red"
    console.print(
        f"    [dim]Query time:[/dim] [{colour}]{elapsed_ms:.1f}ms[/{colour}] "
        f"[dim]({engine_desc})[/dim]"
    )


def print_tier_summary(tier_timings: list[dict]):
    """Print a summary table of all memory tiers used and their timings.

    Args:
        tier_timings: list of dicts with keys: step, tier, description, elapsed_ms, rows_returned
    """
    console.print()
    console.print(Rule("[bold white] Memory Tier Usage Summary [/bold white]", style="bright_cyan"))
    console.print()

    t = Table(box=box.ROUNDED, show_lines=False, padding=(0, 1))
    t.add_column("Step", style="bold white", justify="center", width=6)
    t.add_column("Tier", justify="center", width=14)
    t.add_column("Operation", style="white", min_width=40)
    t.add_column("Latency", justify="right", width=10)
    t.add_column("Rows", justify="right", width=6)

    total_ms = 0.0
    tier_totals = {}

    for entry in tier_timings:
        tier = entry["tier"]
        style = TIER_STYLES.get(tier, TIER_STYLES["RESULT"])
        colour = style["colour"]
        ms = entry.get("elapsed_ms", 0.0)
        total_ms += ms

        tier_totals.setdefault(tier, 0.0)
        tier_totals[tier] += ms

        if ms < 10:
            time_colour = "green"
        elif ms < 100:
            time_colour = "yellow"
        else:
            time_colour = "red"

        t.add_row(
            str(entry["step"]),
            f"[bold {colour}]{tier}[/bold {colour}]",
            entry["description"],
            f"[{time_colour}]{ms:.1f}ms[/{time_colour}]",
            str(entry.get("rows_returned", "-")),
        )

    console.print(t)

    # Per-tier breakdown bar
    console.print()
    for tier, ms in tier_totals.items():
        style = TIER_STYLES.get(tier, TIER_STYLES["RESULT"])
        colour = style["colour"]
        pct = (ms / total_ms * 100) if total_ms > 0 else 0
        bar_len = max(1, int(pct / 2))
        bar = "=" * bar_len
        console.print(
            f"    [{colour}]{tier:>8}[/{colour}]  "
            f"[{colour}]{bar}[/{colour}] "
            f"[dim]{ms:.1f}ms ({pct:.0f}%)[/dim]"
        )

    console.print(f"\n    [bold bright_cyan]Total across all tiers: {total_ms:.0f}ms[/bold bright_cyan]")
    console.print()


def time_query(fn, *args, **kwargs):
    """Run a function and return (result, elapsed_ms)."""
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = (time.perf_counter() - t0) * 1000
    return result, elapsed


def query_to_dicts(client, sql: str, params: dict | None = None) -> list[dict]:
    """Execute a SQL query and return results as a list of dicts."""
    result = client.query(sql, parameters=params or {})
    cols = result.column_names
    return [dict(zip(cols, row)) for row in result.result_rows]


def query_with_stats(
    client, sql: str, params: dict | None = None
) -> tuple[list[dict], dict]:
    """Execute + return (rows as dicts, scan stats from ClickHouse summary).

    The scan stats dict carries read_rows / read_bytes / elapsed_ns etc.
    as ClickHouse reports them. We use this to build the `precision`
    envelope so solution architects can prove "we scanned N rows, not
    the whole 100TB table."
    """
    result = client.query(sql, parameters=params or {})
    cols = result.column_names
    rows = [dict(zip(cols, row)) for row in result.result_rows]
    # result.summary can be None or a string-keyed dict with string values.
    raw = result.summary or {}
    stats = {
        "read_rows": int(raw.get("read_rows", 0) or 0),
        "read_bytes": int(raw.get("read_bytes", 0) or 0),
        "written_rows": int(raw.get("written_rows", 0) or 0),
        "written_bytes": int(raw.get("written_bytes", 0) or 0),
        "elapsed_ns": int(raw.get("elapsed_ns", 0) or 0),
    }
    return rows, stats
