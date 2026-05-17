# Train / test fold discipline

**Authority:** when this doc disagrees with `apps/kernel/src/ownevo_kernel/agent_tools/metrics.py`, the code wins — update this doc to match.

A safety invariant: **the improvement-loop agent must never see test-fold
data during training.** Those rows are the held-out validation set the gate
scores against. If the agent reads them, the lift number stops measuring
generalization and starts measuring memorization.

This doc explains how that invariant is enforced and what the rules are
when extending the agent's tool surface.

---

## 1. The invariant in code

`agent_tools/metrics.py` declares:

```python
class TestFoldAccessRefused(PermissionError):
    """Raised when an agent tool would return a test-fold trace and the
    caller did not opt in via include_test_fold=True."""

FOLD_KEY = "fold"  # metric_outputs key the runner stamps
TEST_FOLD = "test"
```

Two tools enforce the rule:

| Tool | Behavior on `fold == "test"` |
|---|---|
| `read_metrics(db, trace_id, *, include_test_fold=False)` | Raises `TestFoldAccessRefused` unless the caller passes `include_test_fold=True`. |
| `analyze_failures(db, workflow_id, *, include_test_fold=False)` | Silently filters test-fold traces out of the returned list. Same opt-in flag if a caller needs them. |

The flag default is **`False`** everywhere. To surface test-fold data, a caller has to opt in explicitly. Authorized callers today (verified by grep):

- Two tests (`test_agent_tools_metrics.py:98, 147`) — exercise the opt-in path.

The train/test separation in production works via **fold stamping** in `gate/persistence.py` (which stamps the `fold` field when writing traces) and the `False` default in agent tools — not via a production code path calling `include_test_fold=True`. When the gate runner is extended to call `read_metrics` or `analyze_failures` directly, that will be the intended production opt-in caller.

Any new caller passing `include_test_fold=True` should trigger a code review eyebrow.

## 2. How traces get tagged

The iteration runner stamps each agent run's `metric_outputs` with a `fold` key:

```python
metric_outputs = {
    "fold": "train" | "validation" | "test",
    # ... other metrics
}
```

The stamp comes from the eval case the trace ran against: `eval_cases.is_test_fold` (DB column, see [`SCHEMA.md`](SCHEMA.md)). The runner reads `is_test_fold` at iteration start, decides the `fold` string per case, and writes it on every emitted `metric_outputs` row.

This is **fail-open at runtime** but **fail-closed in policy:** if the stamp is missing or unrecognised, the tools default to *allowing* access (treat the trace as train fold). The schema-side `is_test_fold` column is the authoritative ground truth — the policy check is the runtime backstop.

## 3. The exposed tool surface

`middleware/claude_sdk/tool_definitions.py` constructs the JSON-schema tool definitions the agent sees. **Internal flags like `include_test_fold=True` are not exposed.** The agent literally cannot pass it, because the schema doesn't have that parameter.

This is layer 1 of the defense. Layer 2 is the function default. Layer 3 is the explicit kwarg-only signature (`*, include_test_fold: bool = False`) so a positional-argument bug can't accidentally flip it.

## 4. What "test fold" actually protects

The improvement loop's lift claim is: *"every prior fix is regression-tested against every prior failure, so val_score reflects generalization."* That claim requires:

- The **gate** sees the test fold (to compute val_score).
- The **agent** does not see the test fold during reflection / proposal generation. Otherwise it would tune to the held-out set and the gate's val_score becomes circular.

The discipline applies regardless of who's writing the agent's instruction edit:
- LLM proposer (`evolution/proposer.py`) — gets `analyze_failures(..., include_test_fold=False)` only.
- Human reviewer typing a rejection comment — never reads case-level test data; rejection comments become new eval cases tagged train.
- A future curator that promotes clusters to eval cases — every promotion lands in the train fold by default; an operator can re-fold manually.

## 5. Extending the agent toolset — checklist

If you add a new agent-facing tool that touches trace data:

1. Take a `db` connection + the agent's `workflow_id`. **Never** accept a raw `trace_id` from the agent without checking the workflow scope.
2. Default `include_test_fold=False`. Make it a **keyword-only** argument.
3. Either `raise TestFoldAccessRefused` (read-style) or `silently filter` (list-style), matching the closest existing tool's idiom.
4. Do **not** expose `include_test_fold` in the tool's JSON schema. Strip it from the model the agent sees.
5. Add a test that:
   - Inserts a test-fold trace.
   - Calls your tool with the default; asserts refusal or filter.
   - Calls with `include_test_fold=True`; asserts the trace is visible.

## 6. Failure mode: silent test-fold leakage

The most likely accidental violation is **incorrect fold stamping at iteration-runner time** — a bug where `is_test_fold=True` rows get written with `metric_outputs.fold = "train"`. The tools would then happily surface them.

Two backstops:

- **Property test** (gap; not yet in the suite) — iterate every `eval_case` row; assert that for the most recent iteration, every produced trace's `metric_outputs.fold` matches `eval_cases.is_test_fold`. **TODO: add this to CI.**
- **Audit-log spot check** — gate-run audit entries carry the count of test-fold cases. A spike there should match the count of `is_test_fold=true` rows for the workflow.

If you suspect leakage: re-run the iteration with verbose logging on the runner's fold-stamping path; the gate-run-completed audit entry should match the eval-case ground truth.

---

## Related docs

- [`HARNESS.md`](HARNESS.md) — improvement-loop invariants
- [`SCHEMA.md`](SCHEMA.md) — `eval_cases.is_test_fold` column
- [`ARCHITECTURE.md`](ARCHITECTURE.md) §2 — the improvement loop overview
