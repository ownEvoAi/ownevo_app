# ownEvo Harness Design Guide

Design rules for the improvement loop harness: proposer context, agent prompts, eval skill shape, and gate contract. Grounded in the NeoSigma auto-harness pattern (`ownevo_docs/ownEvo_MVP.md` § Validated Reference Patterns) and the Meta-Harness findings (arXiv 2603.28052).

---

## What "harness" means here

The harness is everything that surrounds the coding agent — what context it receives, what tools it can call, what files it owns, how its outputs are evaluated, and what constitutes a valid proposed change. The agent itself is commodity (Claude Sonnet, any future model); the harness is the product.

The harness has three layers:

1. **Proposer context** — what the agent sees when it generates a proposed skill change
2. **Skill contract** — what shape a valid skill must have (`docs/SKILL_FORMAT.md`)
3. **Gate contract** — what the proposed change must prove before it can be approved

---

## 1. Proposer context

### Give the proposer raw traces, not summaries

The most important design rule, grounded in a published ablation (Meta-Harness, 2603.28052):

| Proposer sees | Accuracy |
|---|---|
| Scores only | 34.6% |
| Scores + LLM summaries | 34.9% |
| Full execution traces | 50.0% |

LLM-generated summaries of traces actively hurt. The proposer needs to read raw `AgentEvent` sequences to perform causal diagnosis — to identify *which specific step* caused a failure, not just that a failure occurred.

**Implication for `evolution/proposer`:** the proposer's context must include the raw `AgentEvent` trace for each failure cluster being addressed, not a label or summary. The clustering pipeline produces labels for the UI and for grouping; the proposer gets the underlying traces.

### Give the proposer prior proposal history

Meta-Harness's proposer reads a median of 82 files per iteration across 20+ prior candidates. The critical behavioral consequence: it can identify confounded diffs ("both structural changes AND prompt edits landed in iteration 3 — isolate them") and avoid re-exploring known-bad directions.

The proposer's context should include:
- Current skill version (the file it will edit)
- Prior N proposals for this skill: the diff, the gate outcome, the plain-language explanation, and the failure cluster that prompted it
- The `learnings.md` entries linked to this workflow's iterations

Without prior proposal history, the proposer rediscovers the same failure modes across iterations.

### Environment bootstrapping for the agent solver

At the start of each agent solver run (`eval_runner/`), inject a structured bootstrap block before the task:

```
Available data: {feature_list, data_shape, fold_dates}
Known constraints: {memory_limit_gb, timeout_s, forbidden_imports}
Prior run summary: {best_ever_score, iterations_run, last_successful_change}
```

This eliminates 2-4 exploratory turns where the agent probes its environment. On M5, this means the agent starts with the feature schema and baseline score rather than re-reading the data to infer them.

### One hypothesis per iteration

Propose one logical change per iteration. Multi-change diffs make gate attribution impossible — if a diff adds a new feature AND changes the loss function AND rewrites a data loader, a gate pass or fail can't be attributed to any specific change. When the same hypothesis fails 3× in a row, the proposer should abandon it and log the abandonment in `learnings.md`.

---

## 2. Agent prompt structure

### Proposer prompt shape

The proposer prompt has four sections in order:

```
[SKILL CONTRACT]
The file you own: {skill_id}
Current version: {version hash}
What this skill is responsible for: {retention_contract from SKILL_FORMAT}
What the gate will test: {eval_case_count} cases, best-ever score {score}

[FAILURE CONTEXT]
Cluster: {cluster_label} ({cluster_size} cases, severity {high|medium|low})
Representative traces (raw AgentEvent sequences):
  {trace_1}
  {trace_2}
  ...

[PRIOR PROPOSAL HISTORY]
{For each prior proposal, newest first:}
  Iteration {n}: {plain_language_change} → gate {PASS|REJECT|REGRESS} ({score_delta})
  Learnings: {learnings.md entries for this iteration}

[TASK]
Propose one change to {skill_id} that addresses the {cluster_label} cluster.
Requirements:
- One logical change only
- Must not modify {readonly_sections per retention_contract}
- Output a plain-language explanation (≤200 words) before the diff
- If you've tried this before and it failed, say so and explain what's different
```

### Solver prompt shape (eval runner)

The solver prompt is the skill's task description plus the bootstrap block. Keep it short — the skill file itself carries the domain knowledge. The solver should not be given the improvement objective; it executes the current skill against the eval case inputs and returns outputs.

```
[ENVIRONMENT]
{bootstrap block: data shape, available features, constraints, runtime limits}

[TASK]
{skill.description from retention_contract}
Input: {eval_case.input}
Run the skill and return the output.
```

### Forbidden prompt patterns

- **Don't summarize prior traces for the proposer.** See §1 above.
- **Don't give the solver the improvement objective.** The solver executes; the proposer improves. Mixing these produces a solver that optimizes for the eval set rather than executing faithfully.
- **Don't ask the proposer to propose multiple changes.** One change, one hypothesis.
- **Don't include the test fold in any prompt.** The proposer sees validation failures only; the test fold is for the final gate run.

---

## 3. Eval skill design

An eval skill is a Python file that the sandbox executes. It must conform to `docs/SKILL_FORMAT.md`. Additional constraints for skills that feed the improvement loop:

### The skill owns one concern

The M5 baseline splits into six modules: `data_loader`, `outlier_handler`, `feature_engineer`, `model_trainer`, `predictor`, `ensemble`. Each is a separate file. The agent proposes changes to one module at a time. This is the "single mutable artifact" principle from the auto-harness pattern — drastically simplifies gate and revert.

If a skill file grows beyond ~400 LOC, split it. CI enforces this limit (`make lint`).

### Retention contracts are the gate's inputs

Every skill file has a YAML frontmatter block (`docs/SKILL_FORMAT.md`). The retention contract section declares:
- `readonly_sections` — sections the proposer must not modify (e.g., the data loading contract)
- `eval_invariants` — conditions that must hold across any proposed change (e.g., output shape)
- `improvement_target` — the metric the proposer is trying to move

The gate reads the retention contract before evaluating a proposed diff. A diff that modifies a `readonly_section` is rejected before eval cases even run.

### Eval cases describe failures, not success conditions

Each eval case is a production failure instance (or a cluster representative). The `expected_behavior` field describes what the correct output would have been, not a generic pass criterion. This means:

- A new eval case added from a rejection comment describes a specific failure mode, not an abstract property.
- Eval cases accumulate as the loop runs — each approval cycle that catches a new failure adds to the set.
- The gate's "no regression" condition means all prior eval cases must still pass — the skill can't trade one failure mode for another.

---

## 4. Gate contract

The 3-step gate (`gate/`) in order:

1. **Retention-contract check** — diff must not touch `readonly_sections`; eval invariants must hold on the proposed version. Runs before any sandbox execution.
2. **Regression check** — all prior eval cases must pass. A diff that fixes the new cluster but breaks a previously-passing case is rejected. Best-ever score must be met or exceeded.
3. **Sandbox-error check** — no `Timeout`, `OOM`, or `Crash` exit class. A proposed skill that consumes more memory or takes longer than the baseline is flagged even if it passes eval cases.

Gate does NOT advance `best_ever` on a sandbox error. It DOES log to `learnings.md` with `error_class` so the proposer can see it in the next iteration's prior proposal history.

**Don't add a fourth verification step.** Published ablations (NLAH, 2603.25723) show a verifier module costs −0.8% resolved rate at the benchmark level. The 3-step gate is the right size. Adding "did the agent explain its reasoning correctly?" or "is the diff semantically coherent?" passes overhead to every iteration without measurable improvement.

---

## 5. What not to build (MVP scope)

Per `ownevo_docs/ownEvo_MVP.md` § Out of Scope:

- **Multi-model ensemble proposals** — proposer generates one candidate per iteration. Multi-candidate search cost −2.4% in the NLAH ablation.
- **Automated harness search** (Meta-Harness style) — ownEvo's proposer operates at the skill/workflow layer, not the context-pipeline layer. Don't conflate the two.
- **Continuous proposer runs without gate** — every proposed change pays the gate toll. No "auto-approve if small diff" shortcut for MVP.
- **Custom verifier LLM pass** — the gate is the verifier. A second LLM judging the proposer's output adds latency and cost with no demonstrated benefit at this layer.
