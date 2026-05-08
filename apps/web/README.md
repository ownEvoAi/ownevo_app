# web

TS / Next.js — customer-facing workspace UI (W7 Track 1 complete).

This app is the customer-facing surface for the kernel's improvement
loop. The customer-facing routes live under `/workspaces/[wsId]/...`:

- `/workspaces/acme` — Health page (workflow list + LiftChart).
- `/workspaces/acme/inbox` — proposals filtered by state. Legacy
  `/inbox` 307-redirects here.
- `/workspaces/acme/workflows/[wfId]` — workflow Overview with the
  Agent-anatomy pane (skills · tools · topology + reviewer).
  Tabs: Overview / Failures / Traces / Audit.
- `/workspaces/acme/workflows/[wfId]/failures` — failure clusters,
  one click into the latest cluster→proposal when one exists.
- `/workspaces/acme/workflows/[wfId]/traces` — per-workflow trace
  list; click into per-trace step inspection.
- `/workspaces/acme/traces/[traceId]` — per-trace step timeline
  rendering all seven AgentEvent variants with offset-from-start
  timing.
- `/workspaces/acme/skills/[skillId]` — per-skill detail; renders
  prompt-variant (SKILL.md + retention contract) for instruction
  skills, code-variant (inline diff + extracted signatures) for
  python / composite.
- `/workspaces/acme/proposals/[id]` — proposal detail with skill
  diff, gate result, and Approve / Reject actions. Legacy
  `/proposals/[id]` 307-redirects here.
- `/workspaces/acme/audit` — chronological audit trail with
  verify-chain.

It talks to the kernel REST API at
`apps/kernel/src/ownevo_kernel/api/` (FastAPI). The browser never holds
the kernel URL — every fetch runs server-side via App Router Server
Components / Server Actions.

## Requirements

- Node 20+ (tested on 24.x)
- Postgres reachable via `OWNEVO_DATABASE_URL`
- Python kernel installed with the `api` extra
  (`uv sync --package ownevo-kernel --extra api`)

## Run locally (3 terminals)

```bash
# Terminal 1 — kernel REST API on :8000
export OWNEVO_DATABASE_URL=postgres://ownevo:ownevo@localhost:5432/ownevo
make api

# Terminal 2 — web app on :3000
make web-dev

# Terminal 3 — seed one gate-passed proposal for manual click-through
export OWNEVO_DATABASE_URL=postgres://ownevo:ownevo@localhost:5432/ownevo
make seed-approval-demo
# prints the proposal id + a /proposals/<id> link
```

Open <http://localhost:3000>. You should land on
`/workspaces/acme/inbox` (after a redirect from `/`) with the seeded
proposal under "Awaiting review". Click through, hit "Approve &
advance", watch the sidebar swap to "Recorded decision". Re-load the
inbox — the proposal moves from "Awaiting review" to "Recently decided".

## Configuration

| Env var | Default | What it does |
| --- | --- | --- |
| `OWNEVO_KERNEL_API_URL` | `http://localhost:8000` | URL the server-side fetcher hits. Override when the kernel is on another host (e.g., Docker Compose or staging). |

## What's not in scope yet

- SSE-driven live gate updates (W8 polish).
- Workspace switcher / multi-tenant UX. Slug is cosmetic today;
  authentication + tenant resolution land with the multi-tenant
  retrofit before customer #2 (per CEO review D4).
- Pagination for trace events and the per-workflow list endpoints
  (workflows / iterations / failure_clusters / traces / skills) —
  tracked in TODOS.md § TODO-18, deferred until customer volume
  surfaces it.
- Playwright smoke test. Manual click-through covers the W7 surface;
  CI smoke lands once `kernel-substrate-nightly.yml` is in place.

Static reference mocks for the polished form live at
`../../../www/preview/s26-rk7p3/`. The CSS in `public/styles/` is
copied verbatim from there.
