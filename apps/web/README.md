# web

TS / Next.js — approval queue UI scaffold (W2.5).

This app is the customer-facing surface for the kernel's improvement
loop. The customer-facing routes (W7) live under
`/workspaces/[wsId]/...`:

- `/workspaces/acme/inbox` — list of proposals (filtered by state).
  The legacy `/inbox` URL 307-redirects here.
- `/proposals/[id]` — proposal detail with skill diff, gate result,
  and Approve / Reject actions. Migration into the workspace shell
  lives on `w7-track1-rest`.

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

- SSE-driven live gate updates (W4)
- Audit chain page (W2.5 row in PLAN.md does not include it; lands as
  W7.1.5 polish)
- Workspace switcher, multi-workflow nav (W5 polish)
- Authentication (single-tenant per CEO review D4; multi-tenant
  retrofit when customer #2 onboards)
- Playwright smoke test (manual click-through covers W2.5 scaffold;
  CI smoke lands once `kernel-substrate-nightly.yml` is in place)

Static reference mocks for the polished form live at
`../../../www/preview/s26-rk7p3/`. The CSS in `public/styles/` is
copied verbatim from there.
