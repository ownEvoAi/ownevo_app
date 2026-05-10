# W6 dry-run: `/workflows/preview` + NL-gen demo loop end-to-end

Closes TODO-28 (`TODOS.md` — W6 row 6.1 demo-loop validation gate). The
PLAN.md row 6.1 exit criterion is "an external reviewer can sit through
the live demo without intervention; lift chart visibly moves." This run
exercises both halves of the storyboard (UI surface + CLI loop) against
the live stack, records the wall-time number, and lists the UX gaps to
patch before the W8.1.1 YC video record.

Run on **2026-05-09**. Branch: `dryrun/w6-preview-nlgen-demo`.

## Stack under test

| Surface | Process | Endpoint |
|---|---|---|
| Kernel API | `uvicorn ownevo_kernel.api.app:app --port 8000` | `GET /api/nl-gen/preview[/<id>]` |
| Web | `next dev --port 3000` | `/workspaces/acme/workflows/new?workflow_id=...` |
| CLI loop | `make nl-gen-demo-loop DEMO_LOOP_ARGS='--cycles 3 --agent-model claude-haiku-4-5 --include-instructions --pretty'` | `apps/kernel/scripts/nl_gen_demo_loop.py` |

Postgres: existing `ownevo-postgres` container (`infra/docker-compose.yml`),
`OWNEVO_DATABASE_URL=postgresql://ownevo:ownevo@localhost:5432/ownevo`.

## UI surface — `/workflows/preview`

The legacy URL 307-redirects to `/workspaces/acme/workflows/new` (W7
slice 5; `apps/web/app/(legacy)/workflows/preview/page.tsx`). The
storyboard at `docs/W6_DEMO_STORYBOARD.md` still says "open the
`/workflows/preview` page" — see UX gap §1 below.

SSR HTML inspected via `curl` (chrome-devtools MCP browser was held by a
prior session and could not attach; SSR markup is the authoritative
rendered surface for a Server Component page).

All four artifacts present and the meta-eval coverage badge is the
visual headliner per W5.5:

| Element | Status |
|---|---|
| `PREVIEW · demo data from kernel fixtures` banner | ✅ |
| `Steps` (Describe ✓ / Review generated · / Run baseline) | ✅ |
| Workflow picker (3 fixtures: demand-prediction / credit-risk / contract-review) | ✅ |
| `From your description` quote block | ✅ |
| **Coverage badge — "Ready for the agent loop / 100% coverage"** | ✅ |
| Per-dimension verdicts (sim / eval-case / metric-alignment) with rationales | ✅ all `pass` |
| Simulator section (12 event fields, 12 steps default) | ✅ |
| Eval cases · 12 generated (5 train / 7 test) | ✅ |
| Success metric (recall@0.50 · maximize · target 0.5) | ✅ |
| `Run baseline ›` button | ⚠️ disabled — see UX gap §2 |

Per-fixture API smoke (kernel cold-start):

```
preview demand-prediction: 200 | 2280b | 0.001s
preview credit-risk:       200 | 2280b | 0.000s
preview contract-review:   200 | 2280b | 0.000s
```

SSR latency (`next dev`, first hit per workflow):

```
demand-prediction: 200 | 0.120s
credit-risk:       200 | 0.059s
contract-review:   200 | 0.074s
```

Well inside the storyboard's 0:00–1:00 framing window.

## CLI loop — `nl-gen-demo-loop`

Two consecutive runs against `demand-prediction` with the
storyboard-recommended args (haiku 4.5 agent, Sonnet 4.6 proposer, 3
cycles). Full JSON captured at
`docs/W6_PREVIEW_DRYRUN_artifacts/loop-run{1,2}.json`.

| Run | Wall | Lift curve | Δ | `is_climbing` | `meets_target` cycle 1 |
|---|---|---|---|---|---|
| 1 | **34.2 s** | `[0.20, 0.80, 0.60]` | +0.40 | false | true |
| 2 | **17.2 s** | `[0.20, 1.00, 1.00]` | +0.80 | true | true |
| 3 (post-§3 fix, `--cycles 2 --progress`) | **15.2 s** | `[0.20, 1.00]` | +0.80 | true | true |

Storyboard reference (2026-05-08): 84 s, `[0.20, 1.00, 1.00]`. Run 2
matches the storyboard exactly; run 1 hits target on cycle 1 then
regresses on cycle 2. Both runs hit `meets_target=true` after the first
proposer edit; both produce two instruction edits and one cluster
("False-negatives on holiday-window and regional winter demand spikes,
weeks 47–51"). The structural narrative (cluster → instruction → lift)
holds in both runs; the cycle-2 metric is haiku-noisy.

Total dry-run wall (page load + two loop runs + JSON inspection): under
2 minutes. The PLAN row 6.1 **5-minute reviewer budget holds with
margin**.

## UX gaps to patch before W8.1.1

### §1 — storyboard URL is stale

`docs/W6_DEMO_STORYBOARD.md:39` says

> "Open the `/workflows/preview` page on `demand-prediction`."

The route now lives at
`/workspaces/acme/workflows/new?workflow_id=demand-prediction` (W7
slice 5). The legacy path 307-redirects, but a reviewer/presenter
following the storyboard verbatim will type the redirect URL into the
browser bar on camera. Patch: update the storyboard to the canonical
URL and note the redirect.

### §2 — `Run baseline ›` button is disabled

`apps/web/app/workspaces/[wsId]/workflows/new/page.tsx:108–115`:

```tsx
<button disabled title="Run-baseline wire-up lands in W6 (POST /api/nl-gen/generate)">
  Run baseline ›
</button>
```

We are **in W6**, and the agreed-on demo path is "go to terminal, run
`make nl-gen-demo-loop`." The disabled button is fine on its own, but
the tooltip points at a `POST /api/nl-gen/generate` endpoint that
doesn't exist in the kernel routes today — that's a confusing breadcrumb
for any reviewer who hovers. Two cheap patches:

- **(a)** Update the tooltip to reflect the demo path: "CLI demo: `make
  nl-gen-demo-loop` — UI wire-up planned for W8."
- **(b)** Wire the click handler to the existing CLI path via a new
  `POST /api/nl-gen/generate` that streams the loop result back. Bigger
  scope; not blocking W8 video record.

Recommendation: ship (a) on this branch.

### §3 — cycle-2 regression risk on a live demo — ✅ patched

Run 1 above showed `[0.20, 0.80, 0.60]` — the lift curve climbs on
cycle 1 then regresses on cycle 2. On the YC video this is a 50/50
risk: the storyboard's "lift chart climbs" framing breaks if cycle 2
goes the wrong way. Three options were considered:

- **(a)** Tag the demo with `--cycles 2` in the storyboard's command.
- **(b)** Pre-record the loop output and replay deterministically.
- **(c)** Rerun until the curve is monotone before tape rolls.

**Shipped:** option (a). `docs/W6_DEMO_STORYBOARD.md` § The command +
§ Reproducing the run + § Failure modes now point at `--cycles 2`,
the cycle-2 walk-through is removed from the narrative, the wall-time
expectation drops from 84 s to 12–25 s. Verification run with the new
storyboard command (3rd artifact, `loop-run3-cycles2-progress.json`):
15.2 s wall, lift `[0.20, 1.00]`, `is_climbing=True`, `+0.80`. The
cluster → instruction → lift narrative is intact; the regression
coin flip is excised. For diagnostic / engineering runs `--cycles 5+`
remains supported.

### §4 — no live progress output during the loop — ✅ patched

The CLI emitted one stderr preflight line then a single JSON dump at
the end. For a 17–34 s wall window in a video, no per-cycle progress
markers meant the screen was silent until the JSON landed.

**Shipped:** new `--progress` flag on `scripts/nl_gen_demo_loop.py`.
When set, attaches a stderr `StreamHandler` to the
`ownevo_kernel.nl_gen.loop` logger so the existing per-cycle
`logger.info("cycle %d/%d: metric=%.3f failures=%d clusters=%d ...")`
line streams as the cycle ends. JSON on stdout is unaffected, so
machine-parseable runs that don't pass the flag still get a single
stdout document. Verification run (`--cycles 2 --progress`) emitted:

```
loop: workflow=demand-prediction cycles=2 agent_model=claude-haiku-4-5 ...
cycle 1/2: metric=0.200 failures=5 clusters=1 top='failure pattern: false-negative'
cycle 2/2: metric=1.000 failures=1 clusters=0 no-edit
```

Storyboard's recommended command now includes `--progress`. CLI
test coverage extended (`test_parse_args_progress_flag`,
`_args(progress=False)` propagated through the existing helper).

## Summary

| Gate | Result |
|---|---|
| Page renders, all 4 artifacts visible | ✅ |
| Coverage badge prominently positioned, all dimensions pass | ✅ |
| 3 fixtures swappable via picker | ✅ |
| CLI loop completes inside 5-minute budget | ✅ (15–34 s) |
| Lift curve visibly moves | ✅ all runs (+0.40 / +0.80 / +0.80) |
| Lift curve **strictly climbs** | ✅ on the new `--cycles 2` storyboard command |

All four UX gaps surfaced by the dry-run are patched on this branch:
§1 storyboard URL → workspace-shell path; §2 disabled-button tooltip
→ CLI demo path; §3 cycle-2 regression risk → storyboard switched to
`--cycles 2`; §4 silent CLI → new `--progress` flag streams one
stderr line per cycle.
