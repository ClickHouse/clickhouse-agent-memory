# ClickHouse style guide for the session report

The session-trace report is styled after clickhouse.com so it looks
like a first-party ClickHouse artifact a solution architect can hand
to an enterprise buyer. All colors, typography, and spacing below come
out of the current ClickHouse brand system.

## Color palette

| Token | Hex | Usage |
|---|---|---|
| `--ch-yellow`    | `#FAFF69` | Primary accent. CTAs, KPI values, section underlines, tier "GRAPH" |
| `--ch-yellow-ink` | `#1E1E1E` | Text placed on top of yellow |
| `--ch-red`       | `#FC2424` | The ClickHouse "C" red. Error states, HOT tier accent |
| `--ch-purple`    | `#8430CE` | Accent for WARM semantic search |
| `--ch-bg`        | `#0F0F10` | Page background (near-black, warm) |
| `--ch-panel`     | `#17171A` | Card / panel background |
| `--ch-panel-2`   | `#1F1F24` | Elevated panel (code blocks) |
| `--ch-border`    | `#2A2A32` | Subtle 1px lines |
| `--ch-text`      | `#F5F5F7` | Primary text on dark |
| `--ch-muted`     | `#9CA0AD` | Secondary / captions |
| `--ch-good`      | `#34A853` | Success / precise retrieval |

Tier mapping (used for GFM-alert-style left borders in the chat and
for step accents in the report):

| Tier  | Color token   | Rationale |
|-------|---------------|-----------|
| HOT   | `--ch-red`    | Active, urgent, live stream |
| WARM  | `--ch-purple` | Stored, semantic, searchable |
| GRAPH | `--ch-yellow` | Relationships, ClickHouse signature |

## Typography

- **Primary:** `Inter`, `-apple-system`, `BlinkMacSystemFont`, `"Segoe UI"`,
  `Roboto`, `"Helvetica Neue"`, `Arial`, `sans-serif`.
- **Mono (SQL, envelopes):** `"JetBrains Mono"`, `"IBM Plex Mono"`,
  `ui-monospace`, `SFMono-Regular`, `Menlo`, monospace.
- **Scale:**
  - H1 page title: 40-48px, weight 700, letter-spacing -0.02em.
  - H2 section: 22px, weight 600, uppercase, tracking 0.08em, color muted.
  - H3 step label: 18px, weight 600.
  - Body: 15px, line-height 1.55.
  - Mono: 13px.
- **Emphasis:** ClickHouse uses weight, not italics, for emphasis.
  Italics in the report are reserved for inline "why we picked this
  tool" reasoning lines.

## Spacing

Base unit is 8px. Sections separated by 72-96px vertical rhythm on
desktop. Cards use 24px padding. KPI tiles use 32px padding.

## Components

### KPI tile

Used for the precision scorecard. Dark panel, thin 1px border, large
monospaced value, small uppercase label. Yellow underline on the
`rows_read` tile because it is the scorecard's hero metric.

### Tier chip

Small uppercase label with a colored dot to the left:

```
<span class="tier-chip hot">HOT</span>
```

### Step card

One card per tool call. Three horizontal sub-panels:

1. Reasoning (italic "why" prose)
2. Query (monospace SQL with inline comments, dark panel)
3. Precision + Result (two-column: precision filters + insight text)

A thin colored left border (HOT red / WARM purple / GRAPH yellow) makes
the tier scannable from the scroll.

### Missing-data state

When a tool returns 0 rows, the step card switches to a muted appearance
and replaces the Result section with a boxed `NO DATA` label. This is a
feature, not a bug: we SHOW the agent choosing not to guess.

## Print / PDF

The report is designed to print cleanly on A4 / Letter. Backgrounds
invert to white, tier accents become slim colored rules, and page
breaks avoid splitting a step card.

## File locations

- `docs/report/example-report.html` -- canonical pre-rendered example.
- `docs/report/example-session.json` -- source data for the canonical
  example; feed into the generator to regenerate the HTML.
- `cookbooks/report/template.py` -- HTML builder functions.
- `cookbooks/report/generate.py` -- CLI that renders a session JSON
  into a standalone HTML file.
- `cookbooks/report/demo_session.py` -- runs a canonical investigation
  against the live ClickHouse stack and writes session JSON + HTML.
