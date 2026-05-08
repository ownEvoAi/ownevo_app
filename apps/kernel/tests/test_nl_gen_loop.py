"""Tests for `nl_gen.loop.run_nl_gen_demo_loop` (W6).

Pure-Python: fake AsyncAnthropic for both the agent solver and the
instruction proposer; stubbed clustering stages so we can drive the
loop's lift curve deterministically without sentence-transformers /
HDBSCAN.

The integration story (real Sonnet 4.6 over real fixtures) is exercised
by `scripts/nl_gen_demo_loop.py` end-to-end and validated separately —
this file pins the orchestration contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import numpy as np
import pytest

from ownevo_kernel.clustering import (
    Clusterer,
    Embedder,
    Labeler,
    Reducer,
)
from ownevo_kernel.clustering.types import RawClusterAssignment
from ownevo_kernel.nl_gen.fixtures import (
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)
from ownevo_kernel.nl_gen.instruction_proposer import (
    SCHEMA_VERSION,
    TOOL_NAME as PROPOSER_TOOL,
)
from ownevo_kernel.nl_gen.loop import (
    DemoLoopReport,
    INSTRUCTION_SEPARATOR,
    run_nl_gen_demo_loop,
)


# ---------------------------------------------------------------------------
# Fake AsyncAnthropic — handles BOTH agent (predict_label) and proposer tools
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedClient:
    """Hand-rolled fake AsyncAnthropic that dispatches per-call by tool name.

    The orchestrator makes two kinds of calls:
      * `predict_label` (agent solver) — one per case per cycle.
      * `propose_instruction_edit` (W6 proposer) — one per non-last cycle.

    We script each by name. Predictions are picked deterministically per
    case_id from a per-cycle map; proposer responses are popped in order.
    """

    predictions_by_cycle: list[dict[str, bool]] = field(default_factory=list)
    proposer_payloads: list[dict[str, Any]] = field(default_factory=list)
    cycle_index: int = 0
    proposer_calls: int = 0
    n_cases_seen_this_cycle: int = 0
    n_cases_per_cycle: int = 0

    @property
    def messages(self) -> "_FakeMessages":
        return _FakeMessages(self)


class _FakeMessages:
    def __init__(self, client: _ScriptedClient) -> None:
        self.client = client

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        tool_name = self._tool_name(kwargs)
        if tool_name == "predict_label":
            return self._predict_response(kwargs)
        if tool_name == PROPOSER_TOOL:
            return self._proposer_response()
        raise AssertionError(f"unexpected tool: {tool_name!r}")

    def _tool_name(self, kwargs: dict) -> str:
        tc = kwargs.get("tool_choice")
        if isinstance(tc, dict) and tc.get("type") == "tool":
            return tc["name"]
        # tool_choice="required" / "any" / etc — fall back to the lone tool
        tools = kwargs.get("tools") or []
        if len(tools) == 1:
            return tools[0]["name"]
        raise AssertionError(f"can't determine tool from kwargs: {tc!r}, tools={len(tools)}")

    def _predict_response(self, kwargs: dict) -> SimpleNamespace:
        """Pick prediction by extracting the case_id from the user message."""
        client = self.client
        user_msg = kwargs["messages"][0]["content"]
        # Cases reference `target_label_field` and `target_step_index`; the
        # case_id appears in the trajectory JSON header. We parse it out.
        # Simplest approach: map by call-order position in the cycle.
        case_id = self._case_id_from_call_order(client)
        cycle_map = client.predictions_by_cycle[client.cycle_index]
        value = cycle_map.get(case_id, False)

        client.n_cases_seen_this_cycle += 1
        if client.n_cases_seen_this_cycle >= client.n_cases_per_cycle:
            client.n_cases_seen_this_cycle = 0
            client.cycle_index += 1

        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name="predict_label",
                    input={"value": value, "rationale": "fake prediction"},
                    id="tu_p",
                ),
            ],
            stop_reason="tool_use",
        )

    def _case_id_from_call_order(self, client: _ScriptedClient) -> str:
        cycle_map = client.predictions_by_cycle[client.cycle_index]
        case_ids = list(cycle_map.keys())
        return case_ids[client.n_cases_seen_this_cycle]

    def _proposer_response(self) -> SimpleNamespace:
        client = self.client
        payload = client.proposer_payloads[client.proposer_calls]
        client.proposer_calls += 1
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name=PROPOSER_TOOL,
                    input={"edit": payload},
                    id="tu_e",
                ),
            ],
            stop_reason="tool_use",
        )


def _good_edit(label: str = "winter-spike", text: str = "Lean True on weeks 47-52.") -> dict:
    return {
        "cluster_label": label,
        "rationale": f"failures cluster on {label}",
        "appended_text": text,
        "schema_version": SCHEMA_VERSION,
    }


# ---------------------------------------------------------------------------
# Stub clusterer factories — drive deterministic clustering signals
# ---------------------------------------------------------------------------


class _TwoClusterClusterer:
    """Splits inputs evenly across two clusters. Neither hits the W3
    quality gate's mega-cluster threshold (>90% in one cluster). The
    proposer should pick the dominant cluster (ties → either, but the
    loop's deterministic resolution still leaves one fire per cycle)."""

    def cluster(self, reduced: np.ndarray) -> RawClusterAssignment:
        n = reduced.shape[0]
        labels = np.array(
            [0 if i < (n + 1) // 2 else 1 for i in range(n)],
            dtype=np.int64,
        )
        return RawClusterAssignment(
            labels=labels,
            persistence={0: 0.9, 1: 0.8},
        )


class _AllNoiseClusterer:
    """Returns -1 for every point — exercises the quality-gate
    insufficient-data path (no clusters → no proposer call)."""

    def cluster(self, reduced: np.ndarray) -> RawClusterAssignment:
        n = reduced.shape[0]
        return RawClusterAssignment(
            labels=np.full(n, -1, dtype=np.int64),
            persistence={},
        )


def _two_cluster_factory(snapshots: list) -> Clusterer:
    return _TwoClusterClusterer()


def _all_noise_factory(snapshots: list) -> Clusterer:
    return _AllNoiseClusterer()


class _FixedLabeler:
    def __init__(self, label: str = "stub-cluster") -> None:
        self.label_text = label

    def label(self, sample_texts: list[str], cluster_index: int) -> str:
        return f"{self.label_text}-{cluster_index}"


# ---------------------------------------------------------------------------
# Helpers — pick the demand-prediction fixture; build per-cycle prediction maps
# ---------------------------------------------------------------------------


def _fixture_bundle(workflow_id: str = "demand-prediction"):
    return (
        FIXTURES[workflow_id],
        SIM_PLAN_FIXTURES[workflow_id],
        EVAL_CASE_SET_FIXTURES[workflow_id],
        METRIC_FIXTURES[workflow_id],
    )


def _all_correct_predictions(case_set) -> dict[str, bool]:
    return {c.case_id: c.expected_value for c in case_set.cases}


def _all_wrong_predictions(case_set) -> dict[str, bool]:
    return {c.case_id: not c.expected_value for c in case_set.cases}


def _flip_first_n_correct(case_set, n_correct: int) -> dict[str, bool]:
    """Make the first ``n_correct`` cases right; rest wrong. Drives the
    lift curve up cycle-by-cycle by raising n_correct each time."""
    out: dict[str, bool] = {}
    for i, c in enumerate(case_set.cases):
        out[c.case_id] = c.expected_value if i < n_correct else (not c.expected_value)
    return out


def _predictions_with_n_true_correct(case_set, n_true_correct: int, n_false_correct: int) -> dict[str, bool]:
    """Engineer predictions so exactly ``n_true_correct`` of the True-
    expected cases and ``n_false_correct`` of the False-expected cases
    are predicted correctly. Lets recall and total-failure-count be set
    independently — useful for testing the loop's ≥5-failures clustering
    floor while driving the recall curve upward."""
    out: dict[str, bool] = {}
    n_true_seen = 0
    n_false_seen = 0
    for c in case_set.cases:
        if c.expected_value is True:
            correct = n_true_seen < n_true_correct
            out[c.case_id] = c.expected_value if correct else (not c.expected_value)
            n_true_seen += 1
        else:
            correct = n_false_seen < n_false_correct
            out[c.case_id] = c.expected_value if correct else (not c.expected_value)
            n_false_seen += 1
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_rejects_n_cycles_zero():
    spec, plan, case_set, metric = _fixture_bundle()
    client = _ScriptedClient()
    with pytest.raises(ValueError, match="n_cycles must be"):
        await run_nl_gen_demo_loop(
            spec=spec, plan=plan, case_set=case_set, metric=metric,
            client=client,  # type: ignore[arg-type]
            n_cycles=0,
        )


@pytest.mark.asyncio
async def test_loop_rejects_workflow_id_mismatch():
    """Cross-check parity with run_with_agent — mismatched
    workflow_spec_id between bundle parts is caught before any API call."""
    spec, plan, case_set, metric = _fixture_bundle("demand-prediction")
    other_metric = METRIC_FIXTURES["credit-risk"]  # different workflow_spec_id
    client = _ScriptedClient()
    with pytest.raises(ValueError, match="metric.workflow_spec_id"):
        await run_nl_gen_demo_loop(
            spec=spec, plan=plan, case_set=case_set, metric=other_metric,
            client=client,  # type: ignore[arg-type]
            n_cycles=2,
        )


# ---------------------------------------------------------------------------
# Single-cycle baseline path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_cycle_records_baseline_no_proposer_call():
    """``n_cycles=1`` is the baseline: no future cycle to write
    instructions for, so the proposer must not fire."""
    spec, plan, case_set, metric = _fixture_bundle()
    client = _ScriptedClient(
        predictions_by_cycle=[_all_correct_predictions(case_set)],
        proposer_payloads=[],
        n_cases_per_cycle=len(case_set.cases),
    )

    report = await run_nl_gen_demo_loop(
        spec=spec, plan=plan, case_set=case_set, metric=metric,
        client=client,  # type: ignore[arg-type]
        n_cycles=1,
        clusterer_factory=_two_cluster_factory,
        labeler=_FixedLabeler(),
    )

    assert report.n_cycles == 1
    assert report.cycles[0].metric_value == 1.0
    assert report.cycles[0].meets_target is True
    assert report.cycles[0].n_failures == 0
    assert report.cycles[0].instruction_before is None
    assert report.cycles[0].instruction_after is None
    assert report.cycles[0].instruction_edit is None
    assert client.proposer_calls == 0
    assert report.is_climbing() is False  # single-cycle is never climbing


# ---------------------------------------------------------------------------
# Multi-cycle climbing curve — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_cycle_loop_records_climbing_curve():
    """Three cycles with a climbing prediction map: the lift curve goes
    from low → high; ``is_climbing`` reports True; the proposer fires
    twice (cycles 0 and 1; not on the final cycle)."""
    spec, plan, case_set, metric = _fixture_bundle()
    n = len(case_set.cases)
    # Engineering a climbing recall curve with ≥5 failures per non-last
    # cycle (the W3 quality gate's min_inputs floor):
    #   Cycle 0: 1/5 True correct + 3/7 False correct → recall=0.2; 8 failures
    #   Cycle 1: 3/5 True correct + 5/7 False correct → recall=0.6; 4 failures
    #   Cycle 2: 5/5 True correct + 7/7 False correct → recall=1.0; 0 failures
    # Cycle 1's 4 failures will trigger insufficient-data — adjust to keep
    # the proposer firing on cycle 1 too:
    #   Cycle 1: 3/5 True correct + 4/7 False correct → recall=0.6; 5 failures
    client = _ScriptedClient(
        predictions_by_cycle=[
            _predictions_with_n_true_correct(case_set, n_true_correct=1, n_false_correct=3),
            _predictions_with_n_true_correct(case_set, n_true_correct=3, n_false_correct=4),
            _predictions_with_n_true_correct(case_set, n_true_correct=5, n_false_correct=7),
        ],
        proposer_payloads=[
            _good_edit("hint-A", "Cycle-1 addendum: lean True on past misses."),
            _good_edit("hint-B", "Cycle-2 addendum: also watch for false-positives on test fold."),
        ],
        n_cases_per_cycle=n,
    )

    report = await run_nl_gen_demo_loop(
        spec=spec, plan=plan, case_set=case_set, metric=metric,
        client=client,  # type: ignore[arg-type]
        n_cycles=3,
        clusterer_factory=_two_cluster_factory,
        labeler=_FixedLabeler(),
    )

    assert report.n_cycles == 3
    assert report.is_climbing() is True
    curve = report.lift_curve
    # Strictly increasing
    assert curve[0] < curve[1] < curve[2]
    # The proposer fired twice (cycles 0 and 1)
    assert client.proposer_calls == 2
    # First-cycle instruction is None; second-cycle inherits cycle-0's edit
    assert report.cycles[0].instruction_before is None
    assert report.cycles[1].instruction_before is not None
    assert "Cycle-1 addendum" in report.cycles[1].instruction_before
    # Cycle 2 sees BOTH addenda concatenated (cumulative)
    assert "Cycle-1 addendum" in (report.cycles[2].instruction_before or "")
    assert "Cycle-2 addendum" in (report.cycles[2].instruction_before or "")
    # Last cycle did not propose
    assert report.cycles[-1].instruction_edit is None
    # Final-cycle metric exceeds baseline by absolute_lift
    assert report.absolute_lift == pytest.approx(curve[2] - curve[0])


# ---------------------------------------------------------------------------
# Edge: zero failures mid-loop → no clusters → no proposer call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_failures_skip_proposer_call():
    """A cycle with all-correct predictions has 0 failures, hence 0
    clusters, hence no proposer call. Loop continues to next cycle with
    the prior instruction intact."""
    spec, plan, case_set, metric = _fixture_bundle()
    n = len(case_set.cases)
    client = _ScriptedClient(
        predictions_by_cycle=[
            _all_correct_predictions(case_set),
            _all_correct_predictions(case_set),
        ],
        proposer_payloads=[],  # never called
        n_cases_per_cycle=n,
    )

    report = await run_nl_gen_demo_loop(
        spec=spec, plan=plan, case_set=case_set, metric=metric,
        client=client,  # type: ignore[arg-type]
        n_cycles=2,
        clusterer_factory=_two_cluster_factory,
        labeler=_FixedLabeler(),
    )

    assert client.proposer_calls == 0
    assert report.cycles[0].n_failures == 0
    assert report.cycles[0].n_clusters == 0
    assert report.cycles[0].cluster_signal == "insufficient-data"
    assert report.cycles[0].cluster_signal_reason == "no-failures"
    assert report.cycles[0].instruction_after is None
    # Cycle 1 also sees no instruction (prior was None)
    assert report.cycles[1].instruction_before is None


# ---------------------------------------------------------------------------
# Edge: failures present but quality gate rejects the assignment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_noise_clustering_skips_proposer_call():
    """Failures exist but the clusterer marks every point as noise (-1).
    The W3 quality gate rejects that as ``insufficient-data: all-noise``,
    and the loop must skip the proposer call rather than crash."""
    spec, plan, case_set, metric = _fixture_bundle()
    n = len(case_set.cases)
    client = _ScriptedClient(
        predictions_by_cycle=[
            _all_wrong_predictions(case_set),
            _all_wrong_predictions(case_set),
        ],
        proposer_payloads=[],  # not called
        n_cases_per_cycle=n,
    )

    report = await run_nl_gen_demo_loop(
        spec=spec, plan=plan, case_set=case_set, metric=metric,
        client=client,  # type: ignore[arg-type]
        n_cycles=2,
        clusterer_factory=_all_noise_factory,
        labeler=_FixedLabeler(),
    )

    assert client.proposer_calls == 0
    assert report.cycles[0].n_failures == n
    assert report.cycles[0].n_clusters == 0
    assert report.cycles[0].cluster_signal == "insufficient-data"
    assert report.cycles[0].cluster_signal_reason in {"all-noise", "too-few-points"}


# ---------------------------------------------------------------------------
# Report serialization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_to_dict_is_json_serializable():
    spec, plan, case_set, metric = _fixture_bundle()
    n = len(case_set.cases)
    client = _ScriptedClient(
        predictions_by_cycle=[
            # Cycle 0: 9 failures so the proposer fires (≥5 min_inputs).
            _predictions_with_n_true_correct(case_set, n_true_correct=1, n_false_correct=2),
            _all_correct_predictions(case_set),
        ],
        proposer_payloads=[_good_edit()],
        n_cases_per_cycle=n,
    )
    report = await run_nl_gen_demo_loop(
        spec=spec, plan=plan, case_set=case_set, metric=metric,
        client=client,  # type: ignore[arg-type]
        n_cycles=2,
        clusterer_factory=_two_cluster_factory,
        labeler=_FixedLabeler(),
    )
    d = report.to_dict()
    # Round-trip cleanly
    assert json.loads(json.dumps(d)) == d
    # Top-level keys
    for key in (
        "workflow_spec_id",
        "started_at",
        "ended_at",
        "wall_seconds",
        "n_cycles",
        "metric_family",
        "metric_target",
        "lift_curve",
        "is_climbing",
        "absolute_lift",
        "cycles",
    ):
        assert key in d
    # Cycle 0 produces an edit (for cycle 1 to consume); the edit dict
    # round-trips through to_dict.
    assert d["cycles"][0]["instruction_edit"] is not None
    assert d["cycles"][0]["instruction_edit"]["appended_text"] == _good_edit()["appended_text"]
    # Cycle 1 is the last; the orchestrator doesn't propose a new edit.
    assert d["cycles"][1]["instruction_edit"] is None
    # The cumulative instruction going INTO cycle 1 carries the cycle-0 addendum.
    assert "Lean True" in d["cycles"][1]["instruction_before"]


# ---------------------------------------------------------------------------
# is_climbing semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_climbing_false_on_flat_curve():
    """All cycles produce identical metrics — the curve is flat, so
    ``is_climbing`` returns False even though no cycle regressed."""
    spec, plan, case_set, metric = _fixture_bundle()
    n = len(case_set.cases)
    half = n // 2
    same_predictions = _flip_first_n_correct(case_set, half)
    client = _ScriptedClient(
        predictions_by_cycle=[same_predictions, same_predictions, same_predictions],
        proposer_payloads=[_good_edit("a"), _good_edit("b")],
        n_cases_per_cycle=n,
    )
    report = await run_nl_gen_demo_loop(
        spec=spec, plan=plan, case_set=case_set, metric=metric,
        client=client,  # type: ignore[arg-type]
        n_cycles=3,
        clusterer_factory=_two_cluster_factory,
        labeler=_FixedLabeler(),
    )
    assert report.is_climbing() is False
    assert report.absolute_lift == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_is_climbing_false_on_dip():
    """A cycle that regresses below its predecessor breaks
    monotonicity → ``is_climbing`` is False even if the end > start."""
    spec, plan, case_set, metric = _fixture_bundle()
    n = len(case_set.cases)
    client = _ScriptedClient(
        predictions_by_cycle=[
            _flip_first_n_correct(case_set, 4),  # 4 right
            _flip_first_n_correct(case_set, 8),  # 8 right (climb)
            _flip_first_n_correct(case_set, 6),  # 6 right (dip)
            _flip_first_n_correct(case_set, n),  # all right (recover)
        ],
        proposer_payloads=[_good_edit("a"), _good_edit("b"), _good_edit("c")],
        n_cases_per_cycle=n,
    )
    report = await run_nl_gen_demo_loop(
        spec=spec, plan=plan, case_set=case_set, metric=metric,
        client=client,  # type: ignore[arg-type]
        n_cycles=4,
        clusterer_factory=_two_cluster_factory,
        labeler=_FixedLabeler(),
    )
    assert report.is_climbing() is False  # dip in cycle 2
    assert report.lift_curve[-1] > report.lift_curve[0]
