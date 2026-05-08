"""NL-gen failure → clustering snapshot adapter (W5.3).

The W3 clustering pipeline is duck-typed (`FailureLike` Protocol — needs
`text_signature: str` and `rmsse: float`). This module produces snapshots
satisfying that Protocol from NL-gen agent decisions, so Track A's
generated-sim traces flow through Track B's clustering pipeline without a
second clustering implementation.

Inputs:
  * `EvalCaseSet` — A4.1 generated cases (`sim_seed` / `n_steps` /
    `target_step_index` / `target_label_field` / `expected_value` /
    `provenance` / `is_test_fold`).
  * Per-case agent decisions: `actual_value: bool` + an optional
    one-line rationale string. Sourced from `solve_with_agent`'s
    `ReplayResult`s in production; tests pass them in directly.

Output:
  * Ranked list of `NLGenFailureSnapshot`s (worst-first by severity),
    one per FAILED case. Passing cases are filtered out — clustering
    only runs on failures (same shape as the M5 path).

Failure-mode hints (analogous to M5's `under-forecast` / `over-forecast`):
  * `false-negative`     — expected True, predicted False (missed alert)
  * `false-positive`     — expected False, predicted True (spurious alert)
  * `derived-miss`       — case provenance kind=derived; the past-miss
                           phrase the user verbatim flagged was missed
  * `inferred-miss`      — case provenance kind=inferred (named pattern)
  * `test-fold`          — held-out evaluation case
  * `train-fold`         — training-fold case

Severity (the `rmsse`-analog the FailureLike Protocol reads): a float in
[0.5, 1.0] that ranks failures for sample-ordering inside the clustering
pipeline. Higher = worse. Boosts:
  +0.3 if `is_test_fold` (held-out failures matter more than train-fold)
  +0.2 if provenance.kind == "derived" (verbatim user-flagged misses)

The pipeline reads this attribute under the name `rmsse` for protocol
parity with M5 — pure naming inertia. Internally we store it as
`severity_score` and expose `rmsse` via a property to keep callers honest.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .eval_case_set import EvalCaseSet, GeneratedEvalCase
from .spec import WorkflowSpec

_SEV_BASE = 0.5
_SEV_TEST_FOLD_BOOST = 0.3
_SEV_DERIVED_BOOST = 0.2

_PROVENANCE_SOURCE_TRUNCATE = 80
"""How many chars of provenance.source survive into `text_signature`.

The source is often a verbatim past-miss phrase that can run long; a hard
cap keeps the signature one-liner aligned with M5's compactness."""


@dataclass(frozen=True)
class NLGenFailureSnapshot:
    """One failed NL-gen eval case with structured context for clustering.

    Satisfies the W3 `FailureLike` Protocol via `text_signature` + the
    `rmsse` property (ranking float for worst-first sample ordering).
    """

    case_id: str
    workflow_spec_id: str
    target_label_field: str
    expected_value: bool
    actual_value: bool
    provenance_kind: str
    """`derived` (verbatim past-miss phrase) or `inferred` (named pattern)."""
    provenance_source: str
    case_rationale: str
    is_test_fold: bool
    sim_seed: int
    target_step_index: int
    feature_gap_hints: tuple[str, ...] = field(default_factory=tuple)
    severity_score: float = _SEV_BASE
    text_signature: str = ""

    @property
    def rmsse(self) -> float:
        """FailureLike Protocol shim. Identical to `severity_score`.

        The protocol attribute is named `rmsse` because M5 was the first
        substrate. NL-gen failures don't have an RMSSE — we expose the
        severity score under the same name for duck-typed reuse.
        """
        return self.severity_score


def analyze_nl_gen_failures(
    case_set: EvalCaseSet,
    spec: WorkflowSpec,
    *,
    decisions: list[tuple[str, bool]],
) -> list[NLGenFailureSnapshot]:
    """Filter `decisions` to failures and produce ranked snapshots.

    Args:
        case_set: A4.1 EvalCaseSet whose `cases` were graded.
        spec: WorkflowSpec the case_set was generated against. Cross-
            checked against `case_set.workflow_spec_id`.
        decisions: List of `(case_id, agent_predicted_value)` pairs.
            Order does not matter; missing case_ids are not failures
            (caller decides whether that's an error). The list may
            contain extra ids — they're ignored.

    Returns:
        Snapshots for every case where `actual != expected`, sorted by
        severity descending (ties broken by case_id ASC for determinism).
    """
    if case_set.workflow_spec_id != spec.id:
        raise ValueError(
            f"case_set.workflow_spec_id={case_set.workflow_spec_id!r} "
            f"does not match spec.id={spec.id!r}"
        )

    decisions_by_id: dict[str, bool] = dict(decisions)
    out: list[NLGenFailureSnapshot] = []
    for case in case_set.cases:
        if case.case_id not in decisions_by_id:
            continue
        actual = decisions_by_id[case.case_id]
        if actual == case.expected_value:
            continue
        out.append(_build_snapshot(case, actual=actual, spec=spec))

    out.sort(key=lambda s: (-s.severity_score, s.case_id))
    return out


def _build_snapshot(
    case: GeneratedEvalCase,
    *,
    actual: bool,
    spec: WorkflowSpec,
) -> NLGenFailureSnapshot:
    hints = _failure_hints(case, actual=actual)
    severity = _severity_for_case(case)
    sig = _text_signature(
        case=case,
        actual=actual,
        workflow_spec_id=spec.id,
        hints=hints,
    )
    return NLGenFailureSnapshot(
        case_id=case.case_id,
        workflow_spec_id=spec.id,
        target_label_field=case.target_label_field,
        expected_value=case.expected_value,
        actual_value=actual,
        provenance_kind=case.provenance.kind,
        provenance_source=case.provenance.source,
        case_rationale=case.rationale,
        is_test_fold=case.is_test_fold,
        sim_seed=case.sim_seed,
        target_step_index=case.target_step_index,
        feature_gap_hints=hints,
        severity_score=severity,
        text_signature=sig,
    )


def _failure_hints(case: GeneratedEvalCase, *, actual: bool) -> tuple[str, ...]:
    """Cheap descriptive tags for the embedding input. Stable order.

    Direction tag first, provenance tag second, fold tag last — keeps the
    text_signature deterministic across runs and gives the embedder a
    consistent vocabulary to cluster on.
    """
    hints: list[str] = []
    if case.expected_value is True and actual is False:
        hints.append("false-negative")
    elif case.expected_value is False and actual is True:
        hints.append("false-positive")
    if case.provenance.kind == "derived":
        hints.append("derived-miss")
    elif case.provenance.kind == "inferred":
        hints.append("inferred-miss")
    hints.append("test-fold" if case.is_test_fold else "train-fold")
    return tuple(hints)


def _severity_for_case(case: GeneratedEvalCase) -> float:
    score = _SEV_BASE
    if case.is_test_fold:
        score += _SEV_TEST_FOLD_BOOST
    if case.provenance.kind == "derived":
        score += _SEV_DERIVED_BOOST
    return score


def _text_signature(
    *,
    case: GeneratedEvalCase,
    actual: bool,
    workflow_spec_id: str,
    hints: tuple[str, ...],
) -> str:
    src = case.provenance.source
    if len(src) > _PROVENANCE_SOURCE_TRUNCATE:
        src = src[: _PROVENANCE_SOURCE_TRUNCATE - 1] + "…"
    hints_str = ",".join(hints) if hints else "none"
    return (
        f"{case.case_id} [{workflow_spec_id}/{case.target_label_field}] "
        f"expected={case.expected_value} actual={actual} "
        f"provenance={case.provenance.kind}:{src} "
        f"hints=[{hints_str}]"
    )


__all__ = [
    "NLGenFailureSnapshot",
    "analyze_nl_gen_failures",
]
