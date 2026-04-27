"""
cookbooks/report/generate.py
----------------------------
Turn a session JSON into a standalone ClickHouse-styled HTML report.

Usage:
    python -m cookbooks.report.generate --session session.json --out report.html

The session JSON schema (see demo_session.py for a generator):

    {
      "agent_preset": "ai-sre-agent",
      "agent_label": "AI SRE Agent (Observability)",
      "user_id": "u-maruthi",
      "question": "svc-payments is failing, walk me through it",
      "started_at": "2026-04-19T18:42:00Z",
      "brute_force_baseline_bytes": 2000000000,
      "brute_force_baseline_latency_s": 40,
      "steps": [
        {
          "operation_label": "Live event stream",
          "reasoning": "The user asked 'right now', so ...",
          "insight_text": "2 ERROR events on svc-payments-pod-3 ...",
          "envelope": { ... the MCP envelope returned by the tool ... }
        }
      ],
      "final_brief": {
        "fields": {
          "Trigger": "svc-payments / DB_TIMEOUT / pod-3",
          "Blast radius": "1 direct / 2 indirect / 2 critical"
        },
        "recommended": "raise max_connections to 200 ..."
      }
    }
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

# Work both inside the demo-app container (/app == cookbooks/) and at project root.
_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))
sys.path.insert(0, str(_HERE.parent.parent.parent))

from report.template import render


def main() -> None:
    ap = argparse.ArgumentParser(description="Render a session JSON to HTML.")
    ap.add_argument("--session", required=True, help="Path to session JSON.")
    ap.add_argument("--out", required=True, help="Output HTML path.")
    args = ap.parse_args()

    session_path = pathlib.Path(args.session)
    out_path = pathlib.Path(args.out)

    session = json.loads(session_path.read_text())
    html_doc = render(session)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc)

    print(f"wrote {out_path} ({len(html_doc):,} bytes)")


if __name__ == "__main__":
    main()
