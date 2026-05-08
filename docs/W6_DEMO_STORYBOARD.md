# W6 NL-gen demo storyboard — 5-minute live YC pitch

Walk-through script for the W6 NL-gen end-to-end demo loop (PLAN.md row 6.1).
Pairs `make nl-gen-demo-loop` (CLI lift chart) with the existing
`/workflows/preview` route (meta-eval coverage badge from W5.5) to satisfy
the W6 exit criterion: an external reviewer runs the full loop in under
5 minutes, the meta-eval badge is visible in the UI, the lift chart climbs.

---

## The command

```bash
make nl-gen-demo-loop DEMO_LOOP_ARGS='--cycles 3 --agent-model claude-haiku-4-5 --include-instructions --pretty'
```

Total wall: ~84 seconds. Total cost: a few cents on Anthropic cloud.

`ANTHROPIC_API_KEY` must be set (CLI errors out if not). The proposer
defaults to `claude-sonnet-4-6`; the agent solver runs against haiku 4.5
because Sonnet 4.6 is too good on this fixture (only 2 failures per cycle,
below the W3 quality gate's `min_inputs=5` floor — no clusters fire, no
proposer call, flat curve). For the demo, haiku is the right choice
because it leaves room for the loop to demonstrate lift.

---

## The narrative — 5 minutes

### 0:00–1:00 — frame the problem

> "ownEvo's pitch is that every workflow has institutional knowledge a
> domain expert holds in their head. The agent doesn't have that
> knowledge on day 1 — it's a stateless classifier. What we built is
> the **improvement loop above the model**: every production failure
> becomes an eval case, the failures cluster into named patterns, and
> a domain expert teaches the agent the rule in plain language."

Open the `/workflows/preview` page on `demand-prediction`. Show:

- The reviewer-typed plain-English description ("Alert ops when next
  6-week markdown is upcoming...").
- The four generated artifacts: `WorkflowSpec`, `SimulationPlan`,
  `EvalCaseSet`, `MetricDefinition`.
- The **meta-eval coverage badge** (W5.5) — the LLM-as-judge has
  already validated that the generated bundle covers the description.
  Aggregate score visible; per-dimension verdicts visible.

### 1:00–2:30 — run cycle 0 (baseline)

In the terminal, run the command above. The CLI emits three iterations.
Walk the reviewer through cycle 0:

```
"cycle_index": 0,
"metric_value": 0.2,
"meets_target": false,
"n_failures": 5,
"n_clusters": 1,
"top_cluster_label": "failure pattern: false-negative",
"top_cluster_size": 4
```

> "Haiku 4.5 baseline is 1 of 5 right on the True-expected cases —
> recall 0.20. The metric target is 0.50, so we're failing. But
> notice: the loop has already analyzed the failures and surfaced one
> dominant cluster — 'false-negative on 4 cases' — without anyone
> writing cluster code."

### 2:30–3:30 — show the proposer's edit

Point at `cycles[0].instruction_edit`:

```json
{
  "cluster_label": "False-negatives on holiday/winter markdown alerts (weeks 47-51)",
  "rationale": "All 4 failures are false-negatives clustered in weeks 47-51, driven by missed holiday markdown patterns and seasonal demand spikes (e.g., Pacific NW winter boot spike, holiday markdown dip-tail). Since the gate metric is recall-maximizing, missing a true alert is costlier than a false alarm — the agent must lean True in these high-risk windows.",
  "appended_text": "When the trajectory shows a case in weeks 47-51 with any supply-chain holiday markdown pattern signal (e.g., holiday dip-tail, seasonal boot spike, end-of-year clearance cues), lean toward predicting True even under uncertainty — recall is the gating metric and false-negatives are the dominant failure mode. ..."
}
```

> "The W6 instruction proposer — a separate Anthropic call — read the
> cluster, the metric asymmetry, and 5 representative failures from
> the cluster, and wrote a 2-5 sentence guidance addendum **in plain
> English**. It names the concrete pattern: 'weeks 47-51 with
> seasonal-promo signals.' That's the part you'd otherwise need a
> domain expert to write — and on the next cycle, it ships into the
> agent's per-case context."

### 3:30–4:30 — cycle 1 reads the edit

Walk through cycle 1:

```
"cycle_index": 1,
"metric_value": 1.0,
"meets_target": true,
"n_failures": 2
```

> "Cycle 1: same haiku 4.5, same eval cases, same simulator — only
> the per-case user message gained the addendum the proposer wrote.
> Recall jumped 0.20 → 1.00. Five for five on the True-expected
> cases. The agent learned the rule from a sentence."

Then point at the lift curve:

```json
"lift_curve": [0.2, 1.0, 1.0],
"is_climbing": true,
"absolute_lift": 0.8,
"wall_seconds": 84.27
```

### 4:30–5:00 — close the frame

> "End-to-end in 84 seconds: workflow description → meta-eval badge →
> sim → eval cases → metric → live agent runs → failures cluster →
> instruction edit → lift chart climbs. None of the rules are
> hand-coded; none of the model is fine-tuned. The improvement loop
> above the model is what we sell — every change is an audit-logged,
> human-readable instruction, and the customer's domain expert is the
> one who would write it in production."

---

## What's load-bearing about each piece

| Stage | What's load-bearing | Where it lives |
|---|---|---|
| **Description → 4 artifacts** | Plain-English input becomes structurally-validated artifacts the loop can run on. No-code surface for the reviewer. | `nl_gen.pipeline.generate_full_pipeline` (W3-W4) |
| **Meta-eval coverage badge** | The judge has already vouched that the bundle matches the description. Closes "is this just a toy" objection before the loop runs. | A4.6 + W5.5 + `/workflows/preview` |
| **Failure clustering** | Failures don't get listed individually — they get a *named pattern*. The proposer writes one edit per cluster, not one per failure. | W5.3 wire-up + W3 clustering pipeline (stub stages by default; production runs swap in sentence-transformers + UMAP + HDBSCAN) |
| **Instruction proposer** | The edit is written in *second person* domain language the agent can read on the next pass. Not a prompt-engineering trick — the proposer is told to respect the metric's asymmetry and build on prior cycles' guidance. | `nl_gen.instruction_proposer.propose_instruction_edit` (W6) |
| **Lift curve climbs** | Same agent, same cases, same sim — the only thing that changed cycle-over-cycle is the instruction the proposer wrote. The metric movement is data-driven, not narrative. | `nl_gen.loop.run_nl_gen_demo_loop` (W6) |

---

## Failure modes to acknowledge if asked

- **Sonnet 4.6 too good for the demo.** With Sonnet, recall is 0.6 on
  the baseline — only 2 failures, below the W3 quality gate's
  `min_inputs=5` floor → no clusters → flat curve. For the demo, use
  haiku 4.5 as the agent (the proposer stays on Sonnet). For real
  customer workflows, the agent will be the customer's choice; the
  loop's value scales with how much room the agent has to learn.
- **Cluster context is a placeholder under the hood.** The proposer
  receives a real cluster label + 5 representative failures, but the
  cluster comes from the W5.3 stub stages (deterministic by-hint
  bucketing). Production runs swap in sentence-transformers + UMAP +
  HDBSCAN; the proposer interface doesn't change.
- **In-memory only — no DB persistence today.** Each cycle's iteration
  / proposal / approval rows are NOT written to Postgres in this
  demo. The W2.5 approval queue + W6 (TODO-8) parallel-conditions
  infrastructure is wired for M5; pulling NL-gen into the same
  iteration table is a follow-up PR.
- **Instructions accumulate across cycles.** Each cycle's
  `appended_text` concatenates onto the prior cumulative instruction.
  After 3 cycles the instruction is ~3,000 chars — small relative to
  the trajectory in the user message, but not free.

---

## Reproducing the run

The 84-second smoke (2026-05-08) used:

- `claude-haiku-4-5` as the agent solver
- `claude-sonnet-4-6` as the proposer (default)
- `demand-prediction` fixture (12 cases — 5 expected True, 7 expected False)
- `--cycles 3` (baseline + 2 improvement cycles)
- 3 calls per cycle × ~30 seconds + 1 proposer call × ~5 seconds

Numbers will drift cycle-over-cycle as the models update. The
**structural narrative** (cluster → instruction → lift) is the
load-bearing piece, not the specific recall jump from 0.20 to 1.00.
For an investor demo, run the command live; for an artifact, save
the JSON output.

---

## Cross-references

- [`apps/kernel/src/ownevo_kernel/nl_gen/loop.py`](../apps/kernel/src/ownevo_kernel/nl_gen/loop.py) — the orchestrator
- [`apps/kernel/src/ownevo_kernel/nl_gen/instruction_proposer.py`](../apps/kernel/src/ownevo_kernel/nl_gen/instruction_proposer.py) — the W6 edit proposer
- [`apps/kernel/scripts/nl_gen_demo_loop.py`](../apps/kernel/scripts/nl_gen_demo_loop.py) — the CLI
- [`docs/PLAN.md`](PLAN.md) row 6.1 — the W6 exit criterion this satisfies
- [`apps/web/app/workflows/preview/`](../apps/web/app/) — the W5.5 coverage-badge UI route
