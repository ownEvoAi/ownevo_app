# W7 slice plan

*Drafted 2026-05-08 on `feat/w7-plan`. Source: PLAN.md § Phase 3 W7 (16 rows: 13 in Track 1, 3 in Track 3). Goal of this doc: pick a sub-slice that satisfies the W7 exit gate, defer the rest to W8 without blocking the YC video, and give each PR a clear scope before any code lands.*

---

## Current state (what we already have)

| Surface | What exists | What's missing |
|---|---|---|
| `apps/web/app/` | `inbox/`, `proposals/[id]/`, `workflows/preview/` (W5.5 NL-gen UI), `components/theme-toggle.tsx` | App shell with nav sidebar, `/workspaces/[wsId]/...` route prefix, health/lift/failures/audit/skills views |
| Kernel API (`apps/kernel/src/ownevo_kernel/api/`) | `GET/POST /proposals*`, `GET /workflows/preview*` | `/workflows`, `/workflows/{id}/iterations`, `/workflows/{id}/failure_clusters`, `/workflows/{id}/audit`, `POST /workflows/{id}/audit/verify` |
| Visual target | `../www/preview/s26-rk7p3/` — 32 mocks + `shell.css` + `primitives.css` (already lifted into `apps/web/` at W2.5) | wire-up — mocks are the spec, not the implementation |

`workspace_id` doesn't exist (D4 single-tenant). The URL param `[wsId]` is cosmetic for MVP — backend ignores it. Schema-clean retrofit when customer #2 lands.

---

## YC-video critical path (the binding constraint)

The 90-second North Star storyboard names the screens that must work live:

| Storyboard segment | Screen needed | PLAN row |
|---|---|---|
| 0:00-0:08 cold open — M5 lift climbs | Lift chart on Health page | **7.1.2 + 7.1.6** |
| 0:08-0:25 NL-gen flow | "type description → sim+evals+metric" | already shipped (W6 6.1 + W5.5 preview UI) |
| 0:25-0:50 loop runs | Failure cluster card → proposal → gate → approve → audit appears | **7.1.3 + existing /proposals/[id] + 7.1.5** |
| 1:05-1:30 four-workflow tab strip | Sidebar showing demand-prediction (live) + labour/contract/support (mock) | **7.1.8** + nav shell |

Rows NOT on the video critical path can slip to W8 without breaking the demo.

---

## Slicing

### Track 1 — workspace skin (sequential, depends on shell)

| Order | Slice | Scope | PLAN rows | Effort (CC) |
|---|---|---|---|---|
| 1 | **Shell + nav** | `app/workspaces/[wsId]/layout.tsx` with the sidebar from `01-health.html` (workspace-switcher, Activity / Workflows / Library nav sections). `globals.css` already has the design tokens via W2.5. | foundation for 7.1.1 | XS |
| 2 | **Health page + LiftChart** | `app/workspaces/[wsId]/page.tsx` (workflow-rows table from `01-health.html`) + `components/LiftChart.tsx` (SVG line: baseline vs ownEvo, annotated dots from approved proposals). New API: `GET /workflows`, `GET /workflows/{id}/iterations`. | 7.1.2 + 7.1.6 | S |
| 3 | **Failures view** | `app/workspaces/[wsId]/workflows/[wfId]/failures/page.tsx` reading `failure_clusters` table → `components/FailureClusterCard.tsx` (matches `16-failures.html`). New API: `GET /workflows/{id}/failure_clusters`. | 7.1.3 | S |
| 4 | **Audit trail + verify-chain** | `app/workspaces/[wsId]/audit/page.tsx` — chronological list, expandable rows, "verify chain" button hitting a new `POST /workflows/{id}/audit/verify` endpoint that wraps W2.4 `export_audit_log` + chain-verify. Visual target: `08-audit.html`. | 7.1.5 | S-M |
| 5 | **"New Workflow" sidebar entry** | Wire the sidebar "New workflow" item to `/workflows/preview` (the existing W5.5 surface). One-line change once shell is in place. | 7.1.7 | XS |
| 6 | **Three positioning mocks** | `app/workspaces/[wsId]/workflows/labour|contract|support/page.tsx` reading from a static `mocks.ts` (parallel to `ownEvo_MVP_mocks.md`). Visual parity with demand-prediction; explicit `<MockBanner />` component. | 7.1.8 | S |

**W7 Track 1 exit gate:** non-engineer opens workspace → sees lift chart climbing on Health → clicks demand-prediction → sees failure clusters → clicks one → opens existing `/proposals/[id]` → approves → audit page shows the new entry. **All wired by slices 1-4 + the existing W5/W6 surfaces.**

### Track 3 — τ³ template (parallel, independent of web app)

| Order | Slice | Scope | PLAN rows | Effort (CC) |
|---|---|---|---|---|
| A | **τ³ dataset + harness** | `apps/kernel/src/ownevo_kernel/benchmarks/tau3/` — dataset loader + train/test split + scoring against Sierra's published methodology. Verify: hand-run a known-good agent → score within ±2pp of Sierra's baseline. | 7.3.1 | M |
| B | **Per-domain templates** | Retail / airline / telecom multi-turn agent harnesses on top of W3 substrate. | 7.3.2 | M |
| C | **A+B replay → reproduce NeoSigma** | Frozen baseline + loop autonomous (= NeoSigma's auto-harness) on training subset. Validation gate: condition B reproduces published 0.78 ±5pp. | 7.3.3 | M |

Track 3 doesn't need the web app and can be scoped to a different agent in parallel if budget allows.

---

## Deferred to W8

**Defer-without-breaking:**
- 7.1.4 ProposalCard polish — the existing `/proposals/[id]` already covers the demo flow. Side-by-side diff + per-eval breakdown shipped W5.1.
- 7.1.9 Per-trace step inspection — closes a LangSmith parallel but isn't on the 90-second video path.
- 7.1.10 / 7.1.11 Per-skill detail (prompt + code variants) — same.
- 7.1.12 Workflow Agent-anatomy pane — supporting, not video-critical.

**W8 morning before video record:**
- 7.1.13 Demo workspace rollback runbook — text doc + dry-run (~1 hour).

---

## Build order (chronological)

```
W7 Mon-Wed  ──  Track 1 slices 1-3 (shell + Health + Failures)
W7 Thu      ──  Track 1 slices 4-5 (Audit + New-Workflow wiring)
W7 Fri      ──  Track 1 slice 6 (mocks) + Track 3 slice A in parallel
W7 weekend  ──  Track 3 slices B + C — NeoSigma ±5pp gate
```

Each slice = one PR. Six PRs Track 1; three Track 3.

**Single most-load-bearing PR:** slice 2 (Health + LiftChart). Without a credible lift chart, the cold open of the YC video has no visual hook. Build it second so the rest can hang off it. Schema, API, and a visible-in-browser climbing line all need to exist by end of W7 Tuesday.

---

## Resolved (2026-05-08)

1. **Lift chart data shape:** `iteration_index` × `val_score`. Every iteration is one point; no day-rollup aggregation. Annotated dots overlay where a proposal was approved. Direct read off the `iterations` table.
2. **URL convention:** `/workspaces/acme/...`. The slug is cosmetic — backend ignores it (D4 single-tenant) — but `acme` reads as a real customer in screenshots, where `default` reads as a placeholder.
3. **OpenAPI cadence:** Update `docs/api/openapi.yaml` once per slice that adds an endpoint. Pydantic models stay the source of truth on the kernel side; the YAML is regenerated to keep TS clients aligned.
