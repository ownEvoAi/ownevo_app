# ownevo_app

Production app for ownEvo — the institutional knowledge layer for AI agents.

The build plan, scope decisions, and stack rationale live in [`ownevo_docs/ownEvo_MVP.md`](../ownevo_docs/ownEvo_MVP.md). This repo is the implementation of that plan.

## Layout

```
apps/
  kernel/        Python — agent runtime, eval harness, failure clustering, regression gate
  web/           TS / Next.js — approval UX, diff viewer, lift chart, audit trail
packages/
  trace-format/  Typed AgentEvent schema (target: OSS spec)
infra/           Docker compose for local Langfuse + Postgres + ClickHouse + collector
docs/            Architecture notes specific to this repo (MVP plan stays in ownevo_docs)
```

## Stack split

Python owns the IP (improvement loop, eval, clustering, regression gate). TS owns the product surface (approval UX, real-time UI, customer-facing dashboards). Joined by a REST + SSE seam.

See `docs/` for the per-component details once they land.

## Status

Empty scaffold — Week 0. Next: foundation lift per `ownEvo_MVP.md` § Tentative Recommended Path.
