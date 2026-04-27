"""
compare.py
----------
Structural comparison of the stitched-stack agent vs the ClickHouse agent.

We count lines fairly: blank lines, comment-only lines, and shebangs are
excluded for both. We do not count performance -- that is a separate
benchmark conversation. What this script measures is surface area.
"""

import os
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box


HERE = Path(__file__).resolve().parent
STITCHED = HERE / "stitched" / "agent.py"
CLICKHOUSE = HERE / "clickhouse" / "agent.py"


def count_fair_loc(path: Path) -> tuple[int, int, int]:
    """Return (total_lines, counted_loc, comment_or_blank).

    Rule: count every non-blank line that is not purely a comment.
    We deliberately do NOT try to strip multi-line strings: SQL queries
    inside triple-quoted strings are real code, not documentation, and
    both files carry SQL, so excluding them would unfairly favour the
    side with more SQL (i.e. ClickHouse). We also count the top-of-file
    module docstring the same way on both sides so the comparison stays
    symmetric.
    """
    total = 0
    counted = 0
    skipped = 0
    with path.open() as f:
        for raw in f:
            total += 1
            stripped = raw.strip()
            if not stripped:
                skipped += 1
                continue
            if stripped.startswith("#!") or stripped.startswith("#"):
                skipped += 1
                continue
            counted += 1
    return total, counted, skipped


def main() -> None:
    console = Console()
    s_total, s_loc, s_skip = count_fair_loc(STITCHED)
    c_total, c_loc, c_skip = count_fair_loc(CLICKHOUSE)

    console.print()
    console.print(Panel(
        "[bold]Stitched stack vs ClickHouse -- structural comparison[/bold]\n"
        "[dim]Same scenario, same six steps, two implementations.[/dim]",
        border_style="cyan", padding=(1, 4),
    ))

    t = Table(box=box.ROUNDED, show_lines=False, padding=(0, 1))
    t.add_column("Dimension", style="bold white")
    t.add_column("Stitched (Pinecone+Redis+Neo4j+Postgres)", style="yellow")
    t.add_column("ClickHouse", style="green")

    t.add_row("Lines of code (blanks/comments excluded)",
              str(s_loc), str(c_loc))
    t.add_row("Source file total lines", str(s_total), str(c_total))
    t.add_row("Client libraries imported",
              "redis, pinecone, neo4j, psycopg2 (4)",
              "clickhouse_connect (1)")
    t.add_row("Distinct databases / services to run",
              "4",
              "1")
    t.add_row("Query languages in flight",
              "Redis command protocol, Pinecone REST, Cypher, SQL (4)",
              "SQL (1)")
    t.add_row("Distance metric name mismatch risk",
              "yes -- Pinecone 'score', Postgres pgvector '<=>', Redis COSINE",
              "no -- cosineDistance() everywhere")
    t.add_row("Cross-tier write in one transaction",
              "no -- four services, no global txn",
              "yes -- single INSERT ... SELECT")
    t.add_row("Operational surfaces (backup/upgrade/monitor)",
              "4",
              "1")
    t.add_row("Cross-tier JOIN in one query",
              "no -- must fan-out + stitch in app code",
              "yes -- native SQL JOIN across HOT/WARM/GRAPH tables")

    console.print(t)

    delta = s_loc - c_loc
    pct = (delta / s_loc * 100) if s_loc else 0
    console.print()
    console.print(Panel(
        f"The stitched agent is [bold yellow]{s_loc} LoC[/bold yellow] across four client "
        f"libraries. The ClickHouse agent is [bold green]{c_loc} LoC[/bold green] against one. "
        f"That is [bold]{delta} fewer lines[/bold] ({pct:.0f}% reduction), but the line count "
        f"is the smaller story. The bigger story is that every cross-tier operation in the "
        f"stitched version is an un-atomic fan-out: the workspace write in Redis cannot be "
        f"committed in the same transaction as a Postgres row, vector matches in Pinecone cannot "
        f"be JOINed to the service catalog in Postgres, and a Neo4j blast-radius result has to "
        f"be stitched back into the rest in Python. On ClickHouse all four tiers are tables in "
        f"one database, which means one query plan, one set of credentials, one backup, one "
        f"upgrade, and one failure mode to reason about.",
        title="[bold]Summary[/bold]", border_style="cyan", padding=(1, 2),
    ))


if __name__ == "__main__":
    main()
