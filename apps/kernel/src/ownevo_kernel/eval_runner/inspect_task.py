"""Inspect AI Task adapter (A4.3).

Materializes a (case_set, plan, spec) trio as an `inspect_ai.Task` so an
agent can be wired through `inspect_ai.eval(task, model="anthropic/...")`
in A5+. Today (A4.3) the load-bearing path is `runner.run_replay`, which
doesn't need a model and runs in milliseconds; this module exists so the
integration shape is named and tested before there's an agent to plug in.

Why lazy import: `inspect-ai` is in the optional `eval` extra (heavy
transitive deps — sandboxes, browsers, scoring frameworks). Kernel
unit tests, the M5 baseline, and `make eval-replay` against the
fixtures don't require it; only this module's tests do (gated by an
import-skip).

Inspect AI vocabulary mapping:
  * `inspect_ai.dataset.Sample.input`     ← case_id (the human-readable
                                            handle the audit trail uses)
  * `inspect_ai.dataset.Sample.target`    ← `str(expected_value)`
                                            ("True" / "False")
  * `inspect_ai.dataset.Sample.metadata`  ← `{"sim_seed", "n_steps",
                                            "target_step_index",
                                            "target_label_field",
                                            "is_test_fold",
                                            "rationale", "provenance"}`
                                            so the future agent solver
                                            has every replay knob
                                            without re-joining against
                                            the source case set.

The Task carries `dataset` only — solver and scorer are caller-supplied
in A5+ when there's a model in the loop. A future helper (`run_inspect`)
will compose them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ownevo_kernel.nl_gen.eval_case_set import EvalCaseSet
from ownevo_kernel.nl_gen.sim_plan import SimulationPlan
from ownevo_kernel.nl_gen.spec import WorkflowSpec

if TYPE_CHECKING:  # pragma: no cover - import only for static type-check
    from inspect_ai import Task


def _sample_metadata(case_set: EvalCaseSet, case) -> dict[str, Any]:
    return {
        "workflow_spec_id": case_set.workflow_spec_id,
        "sim_seed": case.sim_seed,
        "n_steps": case.n_steps,
        "target_step_index": case.target_step_index,
        "target_label_field": case.target_label_field,
        "expected_value": case.expected_value,
        "is_test_fold": case.is_test_fold,
        "rationale": case.rationale,
        "provenance_kind": case.provenance.kind,
        "provenance_source": case.provenance.source,
    }


def build_inspect_task(
    case_set: EvalCaseSet,
    plan: SimulationPlan,
    spec: WorkflowSpec,
) -> "Task":
    """Build an `inspect_ai.Task` from the A4.1/A3.2/A3.1 trio.

    Cross-checks (workflow_spec_id agreement) mirror `replay_set`: a
    Task that lies about which workflow it's for is worse than no Task
    at all because the audit trail would attribute scores to the wrong
    workflow downstream.

    Args:
        case_set: A4.1 EvalCaseSet — becomes the Task's dataset.
        plan: A3.2 SimulationPlan — exposed via Task metadata so a
            future agent solver can render the sim if needed.
        spec: A3.1 WorkflowSpec — exposed via Task metadata.

    Returns:
        An `inspect_ai.Task` whose `dataset` is one Sample per case.
        `solver` and `scorer` are unset; the caller supplies them when
        `inspect_ai.eval()` is invoked.

    Raises:
        ValueError: case_set / plan / spec disagree on `workflow_spec_id`.
        ImportError: `inspect-ai` is not installed (install the `eval`
            extra: `uv pip install ownevo-kernel[eval]`).
    """
    if case_set.workflow_spec_id != spec.id:
        raise ValueError(
            f"case_set.workflow_spec_id={case_set.workflow_spec_id!r} "
            f"does not match spec.id={spec.id!r}"
        )
    if plan.workflow_spec_id != spec.id:
        raise ValueError(
            f"plan.workflow_spec_id={plan.workflow_spec_id!r} "
            f"does not match spec.id={spec.id!r}"
        )

    try:
        from inspect_ai import Task
        from inspect_ai.dataset import MemoryDataset, Sample
    except ImportError as exc:  # pragma: no cover - exercised by import-skipped test
        raise ImportError(
            "build_inspect_task requires the `eval` extra — install with "
            "`uv pip install ownevo-kernel[eval]` (adds inspect-ai)."
        ) from exc

    samples = [
        Sample(
            input=case.case_id,
            target=str(case.expected_value),
            metadata=_sample_metadata(case_set, case),
        )
        for case in case_set.cases
    ]

    return Task(
        dataset=MemoryDataset(samples),
        # Solver + scorer intentionally unset — A5+ wires them when
        # there's an agent in the loop. The dataset is what makes the
        # Task useful today (introspection, fixture validation).
    )


__all__ = ["build_inspect_task"]
