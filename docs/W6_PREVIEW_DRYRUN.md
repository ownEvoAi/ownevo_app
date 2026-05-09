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

### §3 — cycle-2 regression risk on a live demo

Run 1 above showed `[0.20, 0.80, 0.60]` — the lift curve climbs on
cycle 1 then regresses on cycle 2. On the YC video this is a 50/50
risk: the storyboard's "lift chart climbs" framing breaks if cycle 2
goes the wrong way. Three options before the record:

- **(a)** Tag the demo with `--cycles 2` in the storyboard's command —
  the structural narrative (cluster → instruction → lift, 0.20 → 1.00)
  is intact and the regression risk is excised. Cost: drops the
  "instruction accumulates across cycles" beat.
- **(b)** Pre-record the loop output to a JSON file and replay
  deterministically during the video. Cheap; no model-noise dependency.
  Cost: not a "live" demo.
- **(c)** Rerun until the curve is monotone before tape rolls. Honest
  about model noise; ~50% of runs will need a retry.

Recommendation: **(a) for the YC video**, with the talking track
"every cycle is a clustered failure pattern + an instruction edit." The
2-cycle version is shorter (~12 s), still demonstrates the loop, and
doesn't carry the regression coin flip.

### §4 — no live progress output during the loop

The CLI emits one stderr preflight line then a single JSON dump at the
end. For a 17–34 s wall window in a video, no per-cycle progress
markers means the screen is silent until the JSON lands. A 3-line
stream (`cycle 0: metric=0.20`, `cycle 1: metric=1.00 (lift +0.80)`,
`cycle 2: ...`) would let the presenter narrate against the terminal
in real time.

Patch shape: stderr `print` per cycle in
`apps/kernel/src/ownevo_kernel/nl_gen/loop.py:run_nl_gen_demo_loop`
behind a `--progress` flag (or default-on if `--pretty` is set). Out of
scope for this PR; logged here.

## Summary

| Gate | Result |
|---|---|
| Page renders, all 4 artifacts visible | ✅ |
| Coverage badge prominently positioned, all dimensions pass | ✅ |
| 3 fixtures swappable via picker | ✅ |
| CLI loop completes inside 5-minute budget | ✅ (17–34 s) |
| Lift curve visibly moves | ✅ both runs (+0.40 / +0.80) |
| Lift curve **strictly climbs** | ⚠️ noisy on haiku — see §3 |

Recommend this branch is the carrier for the §1 storyboard fix and the
§2 tooltip patch. §3 is a storyboard-text decision; §4 is a separate
loop-CLI follow-up.
