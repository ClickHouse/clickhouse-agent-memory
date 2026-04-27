"""
Enterprise Agent Memory -- Cookbook CLI Runner
----------------------------------------------

Usage:
  python main.py seed             # Seed ClickHouse with demo data
  python main.py run observability
  python main.py run telco
  python main.py run cybersecurity
  python main.py run-all           # Run all three demos
"""

import typer
import time

from shared.client import console, print_welcome
from shared.seeders import seed_all
from observability import retrieval as obs_retrieval
from telco import retrieval as telco_retrieval
from cybersecurity import retrieval as cyber_retrieval

app = typer.Typer(
    name="enterprise-agent-memory",
    help="Runnable demos for the Enterprise Agent Memory architecture.",
    add_completion=False,
)


@app.command()
def seed():
    """Seed ClickHouse with synthetic data for all three domains."""
    seed_all.main()


@app.command(name="run")
def run_cookbook(
    cookbook: str = typer.Argument(
        ...,
        help="The cookbook to run: observability, telco, or cybersecurity",
    )
):
    """Run a single cookbook demo by name."""
    print_welcome()
    name = cookbook.lower()
    if name == "observability":
        obs_retrieval.run()
    elif name == "telco":
        telco_retrieval.run()
    elif name == "cybersecurity":
        cyber_retrieval.run()
    else:
        console.print(f"[bold red]Unknown cookbook: '{cookbook}'[/bold red]")
        console.print("Available: observability, telco, cybersecurity")


@app.command(name="run-all")
def run_all_cookbooks():
    """Run all three cookbook demos sequentially."""
    print_welcome()

    t_start = time.perf_counter()

    obs_retrieval.run()
    telco_retrieval.run()
    cyber_retrieval.run()

    total_s = time.perf_counter() - t_start

    console.print()
    console.print(
        f"[bold bright_cyan]"
        f"All three cookbooks completed in {total_s:.2f}s -- "
        f"demonstrating HOT + WARM + GRAPH memory retrieval "
        f"across observability, telco, and cybersecurity domains."
        f"[/bold bright_cyan]"
    )
    console.print()


if __name__ == "__main__":
    app()
