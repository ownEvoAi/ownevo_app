"""Tests for `nl_gen.failure_clustering` (W5.3).

Pins:
  * Snapshot satisfies the W3 `FailureLike` Protocol (text_signature +
    rmsse property).
  * `analyze_nl_gen_failures` filters to failures, builds the right
    hints, ranks worst-first, and refuses cross-workflow input.
  * Severity boost rules.
  * The end-to-end smoke: feeding stub agent decisions through the
    fixture EvalCaseSets + the W3 `cluster_failures` produces ≥3
    clusters (the W5.3 spec gate).
"""

from __future__ import annotations

import pytest
from ownevo_kernel.clustering import FailureLike, cluster_failures
from ownevo_kernel.clustering.types import RawClusterAssignment
from ownevo_kernel.nl_gen.eval_case_set import EvalCaseSet, GeneratedEvalCase
from ownevo_kernel.nl_gen.failure_clustering import (
    NLGenFailureSnapshot,
    analyze_nl_gen_failures,
)
from ownevo_kernel.nl_gen.fixtures import (
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
)
from ownevo_kernel.nl_gen.spec import Provenance, WorkflowSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _case(
    *,
    case_id: str,
    expected: bool,
    kind: str = "derived",
    source: str = "past miss",
    is_test_fold: bool = False,
    sim_seed: int = 0,
    n_steps: int = 10,
    target_step_index: int = 0,
    target_label_field: str = "is_problematic",
) -> GeneratedEvalCase:
    return GeneratedEvalCase(
        case_id=case_id,
        provenance=Provenance(kind=kind, source=source),  # type: ignore[arg-type]
        sim_seed=sim_seed,
        n_steps=n_steps,
        target_step_index=target_step_index,
        target_label_field=target_label_field,
        expected_value=expected,
        rationale="r",
        is_test_fold=is_test_fold,
    )


def _case_set(spec_id: str, cases: list[GeneratedEvalCase]) -> EvalCaseSet:
    """Build an EvalCaseSet, padding with filler cases to clear the
    schema's min_length=10 + balanced-classes (≥3 True, ≥3 False) bar.

    Filler ids start at `filler-...` to avoid colliding with caller ids.
    """
    padded = list(cases)
    existing_ids = {c.case_id for c in padded}
    filler_idx = 0
    # Add fillers until we have ≥10 total + ≥3 True + ≥3 False.
    while True:
        true_count = sum(1 for c in padded if c.expected_value is True)
        false_count = len(padded) - true_count
        if (
            len(padded) >= 10
            and true_count >= 3
            and false_count >= 3
        ):
            break
        if true_count < 3:
            target = True
        elif false_count < 3:
            target = False
        else:
            target = filler_idx % 2 == 0
        cid = f"filler-{filler_idx}"
        while cid in existing_ids:
            filler_idx += 1
            cid = f"filler-{filler_idx}"
        padded.append(_case(case_id=cid, expected=target))
        existing_ids.add(cid)
        filler_idx += 1
    return EvalCaseSet(
        workflow_spec_id=spec_id,
        simulation_plan_workflow_id=spec_id,
        cases=padded,
    )


# Use one of the production fixture specs as a stand-in WorkflowSpec
# (cheaper than constructing one from scratch). Tests that need a
# different `spec.id` use `model_copy(update=...)`.
_BASE_SPEC: WorkflowSpec = FIXTURES["contract-review"]


# ---------------------------------------------------------------------------
# Snapshot dataclass
# ---------------------------------------------------------------------------


def test_snapshot_satisfies_failurelike_protocol():
    snap = NLGenFailureSnapshot(
        case_id="c1",
        workflow_spec_id="w1",
        target_label_field="x",
        expected_value=True,
        actual_value=False,
        provenance_kind="derived",
        provenance_source="past miss",
        case_rationale="r",
        is_test_fold=False,
        sim_seed=0,
        target_step_index=0,
        feature_gap_hints=("false-negative",),
        severity_score=0.7,
        text_signature="sig",
    )
    # Structural Protocol — must work without isinstance() since FailureLike
    # is a runtime-non-checked Protocol. Instead assert the attributes
    # `cluster_failures` reads.
    assert isinstance(snap.text_signature, str)
    assert isinstance(snap.rmsse, float)
    assert snap.rmsse == 0.7
    # And just to confirm the type-hinted Protocol export exists:
    assert FailureLike is not None


def test_snapshot_is_frozen():
    snap = NLGenFailureSnapshot(
        case_id="c1",
        workflow_spec_id="w1",
        target_label_field="x",
        expected_value=True,
        actual_value=False,
        provenance_kind="derived",
        provenance_source="s",
        case_rationale="r",
        is_test_fold=False,
        sim_seed=0,
        target_step_index=0,
    )
    with pytest.raises((AttributeError, TypeError)):
        snap.severity_score = 1.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# analyze_nl_gen_failures — basic behavior
# ---------------------------------------------------------------------------


def _fixture_spec(spec_id: str) -> WorkflowSpec:
    return _BASE_SPEC.model_copy(update={"id": spec_id})


def test_analyze_filters_passes_keeps_failures():
    spec = _fixture_spec("w1")
    cs = _case_set(
        "w1",
        [
            _case(case_id="pass-1", expected=True),
            _case(case_id="fail-1", expected=True),
            _case(case_id="pass-2", expected=False),
            _case(case_id="fail-2", expected=False),
            _case(case_id="other-pass", expected=True),
            _case(case_id="other-fail", expected=True),
        ],
    )
    decisions = [
        ("pass-1", True),
        ("fail-1", False),
        ("pass-2", False),
        ("fail-2", True),
        ("other-pass", True),
        ("other-fail", False),
    ]
    snaps = analyze_nl_gen_failures(cs, spec, decisions=decisions)
    assert sorted(s.case_id for s in snaps) == ["fail-1", "fail-2", "other-fail"]


def test_analyze_skips_cases_with_no_decision():
    """Missing case_ids in `decisions` are not treated as failures.

    This matches the typical caller flow: the agent solver runs over
    `case_set.cases` and produces a decision per case; if a case was
    skipped (e.g., budget exhausted), we don't synthesize a fake fail.
    """
    spec = _fixture_spec("w1")
    cs = _case_set(
        "w1",
        [
            _case(case_id="case-1", expected=True),
            _case(case_id="case-2", expected=False),
        ]
        + [_case(case_id=f"filler-{i}", expected=i % 2 == 0) for i in range(10)],
    )
    snaps = analyze_nl_gen_failures(cs, spec, decisions=[("case-1", False)])
    assert len(snaps) == 1
    assert snaps[0].case_id == "case-1"


def test_analyze_rejects_workflow_id_mismatch():
    spec = _fixture_spec("w1")
    cs = _case_set(
        "w-other",
        [_case(case_id=f"c{i}", expected=i % 2 == 0) for i in range(13)],
    )
    with pytest.raises(ValueError, match="workflow_spec_id"):
        analyze_nl_gen_failures(cs, spec, decisions=[])


# ---------------------------------------------------------------------------
# Hint derivation
# ---------------------------------------------------------------------------


def test_hints_false_negative_derived_train_fold():
    spec = _fixture_spec("w1")
    cs = _case_set(
        "w1",
        [_case(case_id="c1", expected=True, kind="derived")]
        + [_case(case_id=f"f{i}", expected=i % 2 == 0) for i in range(12)],
    )
    snaps = analyze_nl_gen_failures(cs, spec, decisions=[("c1", False)])
    assert len(snaps) == 1
    assert snaps[0].feature_gap_hints == (
        "false-negative",
        "derived-miss",
        "train-fold",
    )


def test_hints_false_positive_inferred_test_fold():
    spec = _fixture_spec("w1")
    cs = _case_set(
        "w1",
        [
            _case(
                case_id="c1",
                expected=False,
                kind="inferred",
                source="domain pattern",
                is_test_fold=True,
            )
        ]
        + [_case(case_id=f"f{i}", expected=i % 2 == 0) for i in range(12)],
    )
    snaps = analyze_nl_gen_failures(cs, spec, decisions=[("c1", True)])
    assert snaps[0].feature_gap_hints == (
        "false-positive",
        "inferred-miss",
        "test-fold",
    )


# ---------------------------------------------------------------------------
# Severity boost rules
# ---------------------------------------------------------------------------


def test_severity_base_no_boost():
    spec = _fixture_spec("w1")
    cs = _case_set(
        "w1",
        [_case(case_id="c1", expected=True, kind="inferred", is_test_fold=False)]
        + [_case(case_id=f"f{i}", expected=i % 2 == 0) for i in range(12)],
    )
    snaps = analyze_nl_gen_failures(cs, spec, decisions=[("c1", False)])
    assert snaps[0].severity_score == pytest.approx(0.5)


def test_severity_test_fold_boost_only():
    spec = _fixture_spec("w1")
    cs = _case_set(
        "w1",
        [_case(case_id="c1", expected=True, kind="inferred", is_test_fold=True)]
        + [_case(case_id=f"f{i}", expected=i % 2 == 0) for i in range(12)],
    )
    snaps = analyze_nl_gen_failures(cs, spec, decisions=[("c1", False)])
    assert snaps[0].severity_score == pytest.approx(0.5 + 0.3)


def test_severity_derived_boost_only():
    spec = _fixture_spec("w1")
    cs = _case_set(
        "w1",
        [_case(case_id="c1", expected=True, kind="derived", is_test_fold=False)]
        + [_case(case_id=f"f{i}", expected=i % 2 == 0) for i in range(12)],
    )
    snaps = analyze_nl_gen_failures(cs, spec, decisions=[("c1", False)])
    assert snaps[0].severity_score == pytest.approx(0.5 + 0.2)


def test_severity_full_boost_caps_at_one():
    spec = _fixture_spec("w1")
    cs = _case_set(
        "w1",
        [_case(case_id="c1", expected=True, kind="derived", is_test_fold=True)]
        + [_case(case_id=f"f{i}", expected=i % 2 == 0) for i in range(12)],
    )
    snaps = analyze_nl_gen_failures(cs, spec, decisions=[("c1", False)])
    assert snaps[0].severity_score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_results_ranked_worst_first_then_case_id():
    spec = _fixture_spec("w1")
    cs = _case_set(
        "w1",
        [
            _case(case_id="aaa", expected=True, kind="inferred"),  # 0.5
            _case(case_id="bbb", expected=True, kind="derived"),  # 0.7
            _case(case_id="ccc", expected=True, kind="inferred", is_test_fold=True),  # 0.8
            _case(case_id="ddd", expected=True, kind="derived", is_test_fold=True),  # 1.0
            _case(case_id="eee", expected=True, kind="derived"),  # 0.7
        ]
        + [_case(case_id=f"f{i}", expected=i % 2 == 0) for i in range(8)],
    )
    decisions = [(c.case_id, False) for c in cs.cases]
    snaps = analyze_nl_gen_failures(cs, spec, decisions=decisions)
    failed_ids = [s.case_id for s in snaps if s.case_id in {"aaa", "bbb", "ccc", "ddd", "eee"}]
    # Severity desc; ties broken by case_id ASC. bbb and eee both 0.7 → bbb first.
    assert failed_ids == ["ddd", "ccc", "bbb", "eee", "aaa"]


# ---------------------------------------------------------------------------
# Text signature
# ---------------------------------------------------------------------------


def test_text_signature_carries_load_bearing_fields():
    spec = _fixture_spec("w1")
    cs = _case_set(
        "w1",
        [
            _case(
                case_id="my-case",
                expected=True,
                kind="derived",
                source="missed the 2025 winter spike",
                target_label_field="alert_correct_label",
            )
        ]
        + [_case(case_id=f"f{i}", expected=i % 2 == 0) for i in range(12)],
    )
    snaps = analyze_nl_gen_failures(cs, spec, decisions=[("my-case", False)])
    sig = snaps[0].text_signature
    assert "my-case" in sig
    assert "w1" in sig
    assert "alert_correct_label" in sig
    assert "expected=True" in sig
    assert "actual=False" in sig
    assert "derived" in sig
    assert "missed the 2025 winter spike" in sig
    assert "false-negative" in sig
    assert "derived-miss" in sig


def test_text_signature_truncates_long_provenance_source():
    spec = _fixture_spec("w1")
    long_src = "x" * 200
    cs = _case_set(
        "w1",
        [_case(case_id="c1", expected=True, kind="derived", source=long_src)]
        + [_case(case_id=f"f{i}", expected=i % 2 == 0) for i in range(12)],
    )
    snaps = analyze_nl_gen_failures(cs, spec, decisions=[("c1", False)])
    sig = snaps[0].text_signature
    # Should be truncated to ~80 chars + ellipsis, not the full 200.
    assert len(long_src) - 100 > sig.count("x")
    assert "…" in sig


# ---------------------------------------------------------------------------
# End-to-end W5.3 smoke — feed fixture EvalCaseSets through cluster_failures
# ---------------------------------------------------------------------------


def _stub_strategy(case: GeneratedEvalCase) -> bool:
    """Mirror the CLI default — produces ≥3 clusters across the 3 fixtures.

    Kept inline rather than imported from the CLI so the test pins
    the strategy contract independently of the CLI's internals.
    """
    if case.provenance.kind == "derived":
        return not case.expected_value
    if (
        case.provenance.kind == "inferred"
        and case.expected_value is False
        and not case.is_test_fold
    ):
        return True
    return case.expected_value


class _ByWorkflowDirectionClusterer:
    """Bucket snapshots by (workflow_spec_id, direction-hint).

    Mirrors the CLI's stub clusterer; pre-binds labels at construction
    so the Protocol's `cluster(reduced)` only has to verify alignment.
    """

    def __init__(self, snapshots: list[NLGenFailureSnapshot]) -> None:
        keys: list[tuple[str, str]] = []
        for s in snapshots:
            primary = s.feature_gap_hints[0] if s.feature_gap_hints else "no-hint"
            keys.append((s.workflow_spec_id, primary))
        seen: dict[tuple[str, str], int] = {}
        labels: list[int] = []
        for k in keys:
            if k not in seen:
                seen[k] = len(seen)
            labels.append(seen[k])
        import numpy as np

        self._arr = np.asarray(labels, dtype=np.int64)
        self._persistence = {lbl: 0.6 for lbl in seen.values()}

    def cluster(self, reduced):  # noqa: ANN001 — Protocol
        if reduced.shape[0] != self._arr.shape[0]:
            raise ValueError("alignment lost")
        return RawClusterAssignment(labels=self._arr, persistence=self._persistence)


class _HashEmbedder:
    """sha256 → 384-d float32 normalized vector. Same as CLI."""

    def embed(self, texts):  # noqa: ANN001
        import hashlib

        import numpy as np

        out = np.zeros((len(texts), 384), dtype=np.float32)
        for i, t in enumerate(texts):
            h = hashlib.sha256(t.encode("utf-8")).digest()
            seed = int.from_bytes(h[:4], "big") & 0x7FFFFFFF
            rng = np.random.default_rng(seed)
            v = rng.normal(size=384).astype(np.float32)
            v /= np.linalg.norm(v) + 1e-9
            out[i] = v
        return out


class _IdentityReducer:
    def reduce(self, embeddings):  # noqa: ANN001
        return embeddings


class _StubLabeler:
    def label(self, texts, idx):  # noqa: ANN001
        return f"cluster-{idx}"


def test_smoke_three_fixtures_produce_at_least_three_clusters():
    """W5.3 spec gate: NL-gen failures → ≥3 clusters.

    The default stub strategy fails on every `derived` case + on
    train-fold inferred-False cases. Across the 3 fixtures (~36 cases)
    that's enough variety to seed clusters partitioned by
    (workflow_spec_id × failure-direction).
    """
    snapshots: list[NLGenFailureSnapshot] = []
    for key, spec in FIXTURES.items():
        case_set = EVAL_CASE_SET_FIXTURES[key]
        decisions = [(c.case_id, _stub_strategy(c)) for c in case_set.cases]
        snapshots.extend(
            analyze_nl_gen_failures(case_set, spec, decisions=decisions)
        )

    assert len(snapshots) >= 10, "stub strategy should fail at least 10 cases"

    result = cluster_failures(
        snapshots,
        embedder=_HashEmbedder(),
        reducer=_IdentityReducer(),
        clusterer=_ByWorkflowDirectionClusterer(snapshots),
        labeler=_StubLabeler(),
    )
    # The W5.3 spec says "≥3 NL-gen-derived clusters appear".
    assert len(result.clusters) >= 3, (
        f"W5.3 spec gate failed: only {len(result.clusters)} clusters "
        "(expected ≥3)"
    )
    # And every cluster is grounded in a known workflow.
    workflow_ids = set(FIXTURES["demand-prediction"].id for _ in [0])
    workflow_ids = {spec.id for spec in FIXTURES.values()}
    for cluster in result.clusters:
        first_sig = cluster.sample_signatures[0] if cluster.sample_signatures else ""
        assert any(
            wid in first_sig for wid in workflow_ids
        ), f"cluster {cluster.label!r} sample is not tied to a fixture workflow"


def test_smoke_clusters_partitioned_by_workflow_when_strategy_uniform():
    """Sanity: an `always-false` strategy on the fixtures produces one
    cluster per workflow's false-negative population (3 clusters)."""
    snapshots: list[NLGenFailureSnapshot] = []
    for key, spec in FIXTURES.items():
        case_set = EVAL_CASE_SET_FIXTURES[key]
        decisions = [(c.case_id, False) for c in case_set.cases]
        snapshots.extend(
            analyze_nl_gen_failures(case_set, spec, decisions=decisions)
        )

    result = cluster_failures(
        snapshots,
        embedder=_HashEmbedder(),
        reducer=_IdentityReducer(),
        clusterer=_ByWorkflowDirectionClusterer(snapshots),
        labeler=_StubLabeler(),
    )
    assert len(result.clusters) == 3
