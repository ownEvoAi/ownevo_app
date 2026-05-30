# Multi-metric gate — what's in docs vs what the talk implies

**Status:** gap-analysis doc, not a design spec. Captures the delta between
the current single-composite gate and the multi-metric Pareto framing used
in the `/Loop Generate sim` talk. Resolve before the next workspace iteration.

---

## 1. What the docs and code currently say

| Source | Statement |
|---|---|
| `docs/ARCHITECTURE.md` §2 | "3-step gate maps `FAIL_REGRESSION` and `FAIL_NO_IMPROVEMENT` to `ProposalState.REJECTED`; only `SANDBOX_ERROR` maps to `GATE_FAILED`." |
| `docs/STATE_MACHINES.md` line 34 | "gate passes (val_score > best_ever AND prior suite still passes)" |
| `docs/STATE_MACHINES.md` line 114 | "best_ever_score_after = max(best_ever_score_before, val_score)" |
| `docs/HARNESS.md` | three layers of the harness (architectural, not three signal metrics) |
| `apps/kernel/src/ownevo_kernel/nl_gen/metric_def.py` | `MetricDefinition` = one `family`, one `direction`, one `target_value`, one `lower_bound`/`upper_bound`. **Single scalar.** |
| `apps/kernel/src/ownevo_kernel/gate/runner.py` lines 161-165 | `val_score <= best_ever_score + improvement_epsilon` ⇒ `FAIL_NO_IMPROVEMENT`. **Single-scalar comparison.** |

In short: the schema generates one composite metric per workflow; the gate
compares one float against one prior float; "best ever" is a single scalar.

The `/api/nl-gen/generate` pipeline produces exactly this shape — verified
by the AV sim-triage workflow generated 2026-05-14, whose metric is the
single composite `correct_triage_and_accepted_reproducer_rate`.

## 2. What the talk narrative implies

From the `/Loop Generate sim` deck, slides 14-17, plus the talk track:

> "Three signal-layer metrics — RCA precision/recall, reproducer
> fidelity, reproducer minimality. Promotion requires all three to
> improve, none to regress."

For the sim-failure-triage agent specifically, those decompose to:

| Signal | Source tool / field |
|---|---|
| **RCA precision/recall** | `diagnose_root_cause(...)` → `category` ∈ {perception_miss, prediction_error, planner_edge_case}, scored against ground-truth `root_cause.category` |
| **Reproducer fidelity** | `verify_reproducer(...)` → `reproduction_rate` (float), aggregated across submitted cases |
| **Reproducer minimality** | `propose_minimal_reproducer(...)` → `minimality_score` (float), aggregated across submitted cases |

The Pareto-style gate rule the talk implies:

```
promote iff (∀ m ∈ metrics : m_candidate ≥ m_best_ever)
       AND  (∃ m ∈ metrics : m_candidate >  m_best_ever + ε)
```

That's strictly stronger than the current rule (which only checks the
single composite). It's also strictly more useful — a candidate that
trades reproducer minimality for higher RCA recall would currently pass
the composite gate; under the talk's rule it would be blocked unless
both improve.

## 3. What's missing in docs

### 3.1 Schema-level
- `WorkflowSpec.success_criterion` and `MetricDefinition` need to support a
  **list of signal metrics** in addition to the single headline composite.
  Each list entry needs its own `direction`, `target_value`, and bounds.
- The NL-gen `metric_generator` needs to emit signal metrics when the
  WorkflowSpec implies a multi-dimensional task (tool calls that return
  multiple typed continuous-or-categorical outputs).

### 3.2 Gate-level
- `gate/runner.py` Step 2 ("improvement check") needs to take a tuple of
  `(val_score, signal_metrics)` and apply the Pareto rule. The current
  scalar comparison becomes a special case (one signal = the composite).
- `iterations.best_ever_score` (a single column) needs a per-signal
  counterpart, or a JSON `best_ever_signals` field. Migration required.

### 3.3 UI-level
- `LiftChart` is single-line today. Multi-metric demands either a small
  multiples view (one chart per signal) or a normalized stacked view
  with each signal's contribution.
- Proposal review sidebar shows `val_score: X.XXXX · best_ever: Y.YYYY`.
  Needs to render per-signal deltas and the Pareto verdict
  ("all improve ✓ / minimality regressed ✗").

### 3.4 Audit-level
- Audit entries currently store `{state, val_score, n_cases, n_failed,
  n_clusters, proposal_id, ...}`. The gate-run-completed payload needs a
  `signals: {rca_pr: ..., fidelity: ..., minimality: ...}` block to make
  the Pareto verdict reproducible from the log.

### 3.5 Doc-level
None of the existing docs describe the multi-metric gate. The three
authoritative places to update once design settles:
- `docs/ARCHITECTURE.md` §2 "Improvement loop" — replace the single-scalar
  gate description with the Pareto rule (today vs planned).
- `docs/STATE_MACHINES.md` lines 33-50 — the gate-pass transition gains
  a "signal-Pareto" sub-condition.
- `docs/HARNESS.md` — add a "Signal layer vs composite layer" section,
  distinct from the existing "three layers" (which is about harness
  architecture, not metric structure — easy point of confusion).

## 4. What is *not* missing — to avoid scope creep

- The **0.7 meta-eval agreement gate** in `nl_gen/` is unrelated. That's a
  pre-registration check on whether the four NL-gen artifacts are
  mutually consistent — fires before any iteration runs. Documented in
  `ARCHITECTURE.md` §3.
- The **0.85 LLM-judge approver gate**
  is a separate proposal-approval surface, not a regression gate. It
  would compose with — not replace — the multi-metric gate.
- The **append-only audit chain** doesn't need re-design; only the
  payload shape needs the `signals` block.

## 5. Minimum viable path to closing the gap

If we want to ship multi-metric in one focused PR rather than a re-design:

1. Add `MetricDefinition.signals: list[SignalMetric]` (optional, defaulting
   to empty for backwards compat). Each `SignalMetric` carries the same
   fields as the headline metric.
2. NL-gen: when the WorkflowSpec has ≥2 tools returning typed continuous
   or categorical outputs, generate signal metrics for them.
3. Gate: if `metric.signals` is non-empty, apply the Pareto rule on the
   signals in addition to the composite. Block on regression of any
   signal. Audit payload gains `signals` block. Single-metric workflows
   are unaffected.
4. UI: ship the lift chart unchanged (still plots composite); add a small
   `SignalDeltaTable` to the proposal sidebar. Defer small-multiples
   chart to a follow-up.
5. Docs: ARCHITECTURE / STATE_MACHINES / HARNESS updates per §3.5 above.

That's a bounded change set — ~3-5 days of work if the design holds.
Bigger lift if the WorkflowSpec schema needs to track per-tool typed
output declarations to drive signal inference (probably another 2 days).

---

**Owner for next pass:** Jit. Ping me with questions on either the gate
semantics or the schema before drafting the PR.
