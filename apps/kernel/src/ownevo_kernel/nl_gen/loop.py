"""W6 NL-gen end-to-end demo loop (PLAN.md row 6.1).

Closes the W4-W5 NL-gen pipeline into an iterative loop the demo can run
live in under 5 minutes:

  workflow description → meta-eval → sim → eval cases → metric
                       → live agent runs → failures cluster
                       → loop iterates → lift chart climbs

The agent is a stateless classifier — there's nothing structural to
"edit" between cycles like M5 has skill files. Instead, each cycle's
failures cluster (W5.3), the dominant cluster is handed to the
W6 instruction proposer (`instruction_proposer.propose_instruction_edit`),
and the proposer's `appended_text` is concatenated onto the cumulative
``per_workflow_instruction`` block injected into the next cycle's
per-case user message. The agent's decision boundary moves cycle over
cycle without any code edit.

What's intentionally NOT here (W6 deferrals):
  * **DB persistence.** This is an in-memory loop; the report carries
    everything the CLI needs. Wiring `persist_gate_run`-style
    iteration / proposal / approval rows is a follow-up once the
    demo storyboard validates the lift curve story.
  * **Real failure clustering.** Defaults to the W5.3 stub embedder /
    clusterer / labeler — the orchestrator accepts custom factories
    so a future test or run can swap in sentence-transformers + UMAP +
    HDBSCAN + Anthropic.
  * **Multi-workflow runs.** One workflow per loop. The CLI
    (``scripts/nl_gen_demo_loop.py``) iterates workflows externally if
    asked.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np

from ..clustering import (
    EMBEDDING_DIM,
    Clusterer,
    ClusteringResult,
    ClusteringSignal,
    ClusterSummary,
    Embedder,
    Labeler,
    Reducer,
    cluster_failures,
)
from ..clustering.types import RawClusterAssignment
from ..eval_runner.runner import EvalCaseOutcome, run_with_agent, run_with_mock_agent
from ..sim_tier import MockSimConfig
from .eval_case_set import EvalCaseSet
from .failure_clustering import (
    NLGenFailureSnapshot,
    analyze_nl_gen_failures,
)
from .instruction_proposer import (
    DEFAULT_MAX_TOKENS as DEFAULT_PROPOSER_MAX_TOKENS,
)
from .instruction_proposer import (
    DEFAULT_MODEL as DEFAULT_PROPOSER_MODEL,
)
from .instruction_proposer import (
    FailureExample,
    InstructionEdit,
    propose_instruction_edit,
)
from .metric_def import MetricDefinition
from .sim_plan import SimulationPlan
from .spec import WorkflowSpec

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)


DEFAULT_N_CYCLES = 3
"""Three cycles is the demo default: cycle 0 baseline, cycles 1-2 carry
the lift curve. Configurable via the orchestrator's ``n_cycles`` arg."""

DEFAULT_FAILURE_EXAMPLES_PER_CLUSTER = 5
"""How many representative failures from the dominant cluster get handed
to the proposer. The instruction-proposer's user message budgets
≤5 examples (one bullet each) without crowding out the rationale."""

INSTRUCTION_SEPARATOR = "\n\n"
"""Cumulative instructions concatenate with a blank line so each cycle's
addendum is visually separable in the agent's user message."""

_STUB_CLUSTER_PERSISTENCE = 0.6
"""Nominal stub persistence score for the demo clusterer. Not a real
HDBSCAN persistence value — just a placeholder so the ClusterSummary
threshold check passes in tests + demo runs."""

_MAX_CUMULATIVE_INSTRUCTION_CHARS = 5_000
"""Soft cap on the cumulative instruction string injected into each cycle's
agent user message. If the concatenation would exceed this, the oldest content
is trimmed and a warning is logged. Prevents quadratic token-cost growth on
long (>5 cycle) demo runs where each cycle appends up to 1,000 chars."""

_CYCLE_TIMEOUT_SECONDS = 180
"""Per-cycle wall-clock timeout. Covers the agent-solver pass (N concurrent
API calls) + the proposer call. 3 minutes is well above normal latency for
a 12-case set; this catches genuine API hangs."""

_H2_HEADER_RE = re.compile(r"^##\s+", re.MULTILINE)
"""Matches markdown H2 headers (lines starting with '## '). Used to strip
proposer output that could inject fake section headings into the agent's
user message (LLM-to-LLM injection guard)."""


# ---------------------------------------------------------------------------
# Stub clustering stages (deterministic, no network) — mirror W5.3 script
# ---------------------------------------------------------------------------


class _HashEmbedder:
    """Same sha256 → 384-d normalized vector pattern as the W5.3 / M5
    cluster scripts. Deterministic + offline; no sentence-transformers
    download. Tests can inject a different ``Embedder`` via the loop's
    ``embedder=`` parameter."""

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), EMBEDDING_DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            h = hashlib.sha256(t.encode("utf-8")).digest()
            seed = int.from_bytes(h[:4], "big") & 0x7FFFFFFF
            rng = np.random.default_rng(seed)
            v = rng.normal(size=EMBEDDING_DIM).astype(np.float32)
            v /= np.linalg.norm(v) + 1e-9
            out[i] = v
        return out


class _IdentityReducer:
    def reduce(self, embeddings: np.ndarray) -> np.ndarray:
        return embeddings


def _build_default_clusterer(snapshots: list[NLGenFailureSnapshot]) -> Clusterer:
    """Bucket failures by their primary feature-gap hint
    (``false-negative`` / ``false-positive`` / etc.). Same shape as the
    W5.3 script's stub — clusters are interpretable to a human reader
    without paying for sentence-transformers + HDBSCAN at demo time.
    """
    keys: list[str] = []
    for s in snapshots:
        primary_hint = s.feature_gap_hints[0] if s.feature_gap_hints else "no-hint"
        keys.append(primary_hint)

    seen: dict[str, int] = {}
    labels: list[int] = []
    for k in keys:
        if k not in seen:
            seen[k] = len(seen)
        labels.append(seen[k])

    arr = np.asarray(labels, dtype=np.int64)
    persistence = {lbl: _STUB_CLUSTER_PERSISTENCE for lbl in seen.values()}

    class _Pluggable:
        def cluster(self, reduced: np.ndarray) -> RawClusterAssignment:
            if reduced.shape[0] != arr.shape[0]:
                raise ValueError(
                    f"clusterer alignment lost: reduced={reduced.shape[0]} "
                    f"but pre-bound labels={arr.shape[0]}",
                )
            return RawClusterAssignment(labels=arr, persistence=persistence)

    return _Pluggable()


class _StubLabeler:
    """Builds a label from the first sample signature's hint tag.

    Same shape as the W5.3 script's stub — readable demo output without
    paying the LLM-labeler cost on every cycle. Tests + production
    callers can swap in a real ``Labeler``.
    """

    def label(self, sample_texts: list[str], cluster_index: int) -> str:
        if not sample_texts:
            return f"cluster-{cluster_index}"
        first = sample_texts[0]
        if "hints=[" in first:
            after = first.split("hints=[")[1]
            tag = after.split(",")[0].split("]")[0]
            if tag and tag != "none":
                return f"failure pattern: {tag}"
        return f"cluster-{cluster_index}"


# ---------------------------------------------------------------------------
# Cycle / report types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CycleOutcome:
    """One cycle's snapshot — agent run + clustering + instruction edit.

    ``metric_value`` is what the lift chart plots; ``meets_target`` is
    whether the cycle cleared the workflow's metric target. ``n_failures``
    drives the lift-curve narrative (failures should drop cycle over
    cycle; metric should climb).

    ``instruction_before`` is the cumulative addendum the agent saw on
    THIS cycle (None on cycle 0). ``instruction_after`` is what the next
    cycle will see — i.e. ``instruction_before`` + the proposer's new
    ``appended_text``. ``instruction_edit`` is the structured edit
    object the proposer returned (None when no clusters or last cycle).
    """

    cycle_index: int
    metric_value: float
    meets_target: bool
    n_failures: int
    n_clusters: int
    cluster_signal: str  # "ok" | "insufficient-data"
    cluster_signal_reason: str | None
    top_cluster_label: str | None
    top_cluster_size: int
    instruction_before: str | None
    instruction_after: str | None
    instruction_edit: InstructionEdit | None
    wall_seconds: float

    # Persistence-facing payload. The original CycleOutcome was JSON-only
    # (CLI report). These fields carry the structured artifacts so the
    # iteration runner can persist traces / failure_clusters / audit rows
    # without re-running the agent. Default-empty so existing test
    # construction (kwargs only) keeps working.
    outcomes: tuple[EvalCaseOutcome, ...] = field(default_factory=tuple)
    snapshots: tuple[NLGenFailureSnapshot, ...] = field(default_factory=tuple)
    clusters: tuple[ClusterSummary, ...] = field(default_factory=tuple)
    clustering_result: ClusteringResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_index": self.cycle_index,
            "metric_value": self.metric_value,
            "meets_target": self.meets_target,
            "n_failures": self.n_failures,
            "n_clusters": self.n_clusters,
            "cluster_signal": self.cluster_signal,
            "cluster_signal_reason": self.cluster_signal_reason,
            "top_cluster_label": self.top_cluster_label,
            "top_cluster_size": self.top_cluster_size,
            "instruction_before": self.instruction_before,
            "instruction_after": self.instruction_after,
            "instruction_edit": (
                self.instruction_edit.model_dump()
                if self.instruction_edit is not None
                else None
            ),
            "wall_seconds": self.wall_seconds,
        }


@dataclass(frozen=True)
class DemoLoopReport:
    """All cycles plus run metadata.

    ``lift_curve`` is the per-cycle metric value sequence — the demo's
    headline visual. ``is_climbing()`` returns True iff the curve ends
    strictly above where it started AND is monotonic non-decreasing
    (mirrors `ReplayReport.is_climbing` from the W5.4 7-day replay).
    """

    workflow_spec_id: str
    cycles: tuple[CycleOutcome, ...]
    started_at: datetime
    ended_at: datetime
    metric_target: float
    metric_family: str
    metric_direction: str

    @property
    def n_cycles(self) -> int:
        return len(self.cycles)

    @property
    def lift_curve(self) -> tuple[float, ...]:
        return tuple(c.metric_value for c in self.cycles)

    @property
    def final_metric_value(self) -> float | None:
        return self.cycles[-1].metric_value if self.cycles else None

    @property
    def baseline_metric_value(self) -> float | None:
        return self.cycles[0].metric_value if self.cycles else None

    @property
    def absolute_lift(self) -> float | None:
        if not self.cycles:
            return None
        return self.cycles[-1].metric_value - self.cycles[0].metric_value

    @property
    def wall_seconds(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()

    def is_climbing(self) -> bool:
        """True iff the curve ends strictly above its start AND every
        cycle is ≥ the previous (no regressions). A flat or
        single-cycle run returns False — same convention as
        ``ReplayReport.is_climbing``.
        """
        curve = self.lift_curve
        if len(curve) < 2:
            return False
        if curve[-1] <= curve[0]:
            return False
        for i in range(1, len(curve)):
            if curve[i] < curve[i - 1]:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_spec_id": self.workflow_spec_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "wall_seconds": self.wall_seconds,
            "n_cycles": self.n_cycles,
            "metric_family": self.metric_family,
            "metric_direction": self.metric_direction,
            "metric_target": self.metric_target,
            "baseline_metric_value": self.baseline_metric_value,
            "final_metric_value": self.final_metric_value,
            "absolute_lift": self.absolute_lift,
            "is_climbing": self.is_climbing(),
            "lift_curve": list(self.lift_curve),
            "cycles": [c.to_dict() for c in self.cycles],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _failure_examples_from_cluster(
    cluster: ClusterSummary,
    snapshots: list[NLGenFailureSnapshot],
    *,
    limit: int = DEFAULT_FAILURE_EXAMPLES_PER_CLUSTER,
) -> list[FailureExample]:
    """Pick up to ``limit`` representative failures from the cluster.

    Members are pre-sorted worst-first by `analyze_nl_gen_failures`, so
    we just take the first N member indices and project each snapshot
    into the proposer's expected ``FailureExample`` shape.
    """
    examples: list[FailureExample] = []
    for idx in cluster.member_indices[:limit]:
        if idx >= len(snapshots):
            raise ValueError(
                f"cluster member_index {idx} is out of bounds for "
                f"snapshots list of length {len(snapshots)}"
            )
        s = snapshots[idx]
        direction = (
            "false-negative"
            if (s.expected_value is True and s.actual_value is False)
            else "false-positive"
        )
        provenance_kind = (
            "derived" if s.provenance_kind == "derived" else "inferred"
        )
        examples.append(
            FailureExample(
                case_id=s.case_id,
                direction=direction,
                provenance_kind=provenance_kind,
                is_test_fold=s.is_test_fold,
                text_signature=s.text_signature,
            )
        )
    return examples


def _pick_dominant_cluster(
    result: ClusteringResult,
) -> ClusterSummary | None:
    """The cluster with the most members. Ties broken by label ASC for
    determinism (the W5.3 pipeline already orders clusters by label,
    so we just pick the largest)."""
    if result.signal != ClusteringSignal.OK or not result.clusters:
        return None
    return max(result.clusters, key=lambda c: (len(c.member_indices), c.label))


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


# Factory types — Embedder / Clusterer / Labeler are Protocols so we
# accept either zero-arg builders or pre-built instances. Reducer is
# always identity for the demo (the stub embedder is already at
# EMBEDDING_DIM).

ClustererFactory = Callable[[list[NLGenFailureSnapshot]], Clusterer]


async def run_nl_gen_demo_loop(
    *,
    spec: WorkflowSpec,
    plan: SimulationPlan,
    case_set: EvalCaseSet,
    metric: MetricDefinition,
    client: AsyncAnthropic,
    n_cycles: int = DEFAULT_N_CYCLES,
    agent_model: str | None = None,
    agent_openai_client: Any | None = None,
    initial_instruction: str | None = None,
    proposer_model: str = DEFAULT_PROPOSER_MODEL,
    proposer_max_tokens: int = DEFAULT_PROPOSER_MAX_TOKENS,
    n_failure_examples_per_cluster: int = DEFAULT_FAILURE_EXAMPLES_PER_CLUSTER,
    embedder: Embedder | None = None,
    reducer: Reducer | None = None,
    clusterer_factory: ClustererFactory | None = None,
    labeler: Labeler | None = None,
    mock_config: MockSimConfig | None = None,
    mock_iteration_index: int | None = None,
) -> DemoLoopReport:
    """Drive ``n_cycles`` of agent-run → cluster failures → propose
    instruction edit on a single NL-gen workflow.

    Args:
        spec / plan / case_set / metric: the four artifacts from the
            A3-A4 NL-gen pipeline. The orchestrator does NOT regenerate
            them — that's a separate concern (``generate_full_pipeline``
            for live workflows; fixtures for the demo).
        client: AsyncAnthropic client used for the instruction proposer
            (and for the agent solver, when ``agent_openai_client`` is
            None). Tests can pass a fake.
        n_cycles: How many cycles to run. Each cycle = one full agent
            pass over ``case_set.cases`` + clustering + (except the
            last cycle) one instruction-proposer call. Min 1.
        agent_model: Override the agent solver's default model. None →
            ``agent_solver.DEFAULT_MODEL``.
        agent_openai_client: When set, the agent solver routes through
            this client instead of the Anthropic ``client`` — used by
            the per-workflow model picker to dispatch the agent leg
            through OpenAI, xAI, Gemini, Fireworks, OpenRouter, or
            Ollama while the proposer continues to use Anthropic.
        initial_instruction: Seed for ``cumulative_instruction`` on
            cycle 0. Used to inject domain-expert steering text from a
            ``changes-requested`` proposal so the next iteration's
            agent (and downstream proposer) sees the redirect.
        proposer_model: Anthropic model id for the proposer. Default
            sonnet 4.6.
        proposer_max_tokens: Output cap on the proposer call.
        n_failure_examples_per_cluster: How many representative failures
            from the dominant cluster get handed to the proposer.
        embedder / reducer / clusterer_factory / labeler: Stage
            overrides for the W5.3 clustering pipeline. None → default
            stubs (deterministic, offline). Tests inject zero-cluster
            stubs to exercise the no-edit path.

    Returns:
        ``DemoLoopReport`` with one ``CycleOutcome`` per cycle.

    Raises:
        ValueError: ``n_cycles < 1`` or workflow_spec_id mismatch.
        Various LLM errors propagate (the loop does NOT swallow them —
        a hard failure in the agent solver or the proposer halts the
        run; the CLI catches and surfaces).
    """
    if n_cycles < 1:
        raise ValueError(f"n_cycles must be >= 1, got {n_cycles}")
    if mock_config is not None and mock_iteration_index is None:
        raise ValueError(
            "mock_config provided without mock_iteration_index — the "
            "MockAgentSolver's accuracy curve is keyed by iteration, "
            "so the caller must supply it explicitly",
        )
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
    if metric.workflow_spec_id != spec.id:
        raise ValueError(
            f"metric.workflow_spec_id={metric.workflow_spec_id!r} "
            f"does not match spec.id={spec.id!r}"
        )

    # Resolve clustering stages — defaults are W5.3-compatible stubs.
    embedder_inst = embedder if embedder is not None else _HashEmbedder()
    reducer_inst = reducer if reducer is not None else _IdentityReducer()
    labeler_inst = labeler if labeler is not None else _StubLabeler()
    clusterer_factory_resolved = (
        clusterer_factory
        if clusterer_factory is not None
        else _build_default_clusterer
    )

    started_at = datetime.now(UTC)
    cycles: list[CycleOutcome] = []
    cumulative_instruction: str | None = (
        initial_instruction.strip() if initial_instruction and initial_instruction.strip() else None
    )

    for cycle_idx in range(n_cycles):
        cycle_started = datetime.now(UTC)
        is_last = cycle_idx == n_cycles - 1

        instruction_before = cumulative_instruction

        async with asyncio.timeout(_CYCLE_TIMEOUT_SECONDS):
            # 1. Run the agent over the full case set with the cumulative
            #    instruction (None on cycle 0). Mock-tier swaps the
            #    LLM-backed solver for a deterministic curve-driven one;
            #    everything downstream (clustering, proposer, gate)
            #    consumes the same EvalRunReport shape either way.
            if mock_config is not None:
                report = await run_with_mock_agent(
                    case_set,
                    plan,
                    spec,
                    metric,
                    mock_config=mock_config,
                    iteration_index=mock_iteration_index,  # type: ignore[arg-type]
                )
            else:
                report = await run_with_agent(
                    case_set,
                    plan,
                    spec,
                    metric,
                    client=client,
                    model=agent_model,
                    openai_client=agent_openai_client,
                    per_workflow_instruction=instruction_before,
                )
            metric_value = report.value
            meets_target = report.meets_target

            # 2. Build (case_id, predicted) decisions → failure snapshots.
            decisions = [(o.case_id, o.actual_value) for o in report.outcomes]
            snapshots = analyze_nl_gen_failures(
                case_set,
                spec,
                decisions=decisions,
            )

            # 3. Cluster the failures via the W5.3 pipeline. Empty failures
            #    → empty clustering result (signal=insufficient-data).
            clustering_result: ClusteringResult
            if snapshots:
                clusterer = clusterer_factory_resolved(snapshots)
                clustering_result = cluster_failures(
                    snapshots,
                    embedder=embedder_inst,
                    reducer=reducer_inst,
                    clusterer=clusterer,
                    labeler=labeler_inst,
                )
            else:
                clustering_result = ClusteringResult(
                    signal=ClusteringSignal.INSUFFICIENT_DATA,
                    clusters=(),
                    n_inputs=0,
                    n_noise=0,
                    insufficient_data_reason="no-failures",
                )

            top_cluster = _pick_dominant_cluster(clustering_result)
            top_cluster_label = top_cluster.label if top_cluster is not None else None
            top_cluster_size = (
                len(top_cluster.member_indices) if top_cluster is not None else 0
            )

            # 4. Propose an instruction edit (skip on last cycle — no next
            #    cycle to inject into; skip when no clusters).
            edit: InstructionEdit | None = None
            instruction_after = instruction_before
            if not is_last and top_cluster is not None:
                examples = _failure_examples_from_cluster(
                    top_cluster,
                    snapshots,
                    limit=n_failure_examples_per_cluster,
                )
                edit = await propose_instruction_edit(
                    client,
                    spec=spec,
                    metric=metric,
                    cluster_label=top_cluster_label or "",
                    cluster_size=top_cluster_size,
                    failure_examples=examples,
                    current_instruction=instruction_before,
                    model=proposer_model,
                    max_tokens=proposer_max_tokens,
                )
                # Strip markdown H2 headers from proposer output before
                # injecting into the agent's user message — prevents the
                # proposer from inserting fake section headings (e.g. a
                # spurious "## Decision" block) that could shift the agent's
                # classification boundary in an uncontrolled way.
                new_addendum = _H2_HEADER_RE.sub("", edit.appended_text).strip()
                if instruction_before:
                    instruction_after = (
                        instruction_before.strip()
                        + INSTRUCTION_SEPARATOR
                        + new_addendum
                    )
                else:
                    instruction_after = new_addendum
                # Warn + trim if the cumulative instruction has grown too large.
                if instruction_after and len(instruction_after) > _MAX_CUMULATIVE_INSTRUCTION_CHARS:
                    logger.warning(
                        "cumulative_instruction exceeded %d chars (%d); "
                        "trimming to the most recent content to control token cost.",
                        _MAX_CUMULATIVE_INSTRUCTION_CHARS,
                        len(instruction_after),
                    )
                    instruction_after = instruction_after[-_MAX_CUMULATIVE_INSTRUCTION_CHARS:]
                cumulative_instruction = instruction_after

        cycle_ended = datetime.now(UTC)
        cycles.append(
            CycleOutcome(
                cycle_index=cycle_idx,
                metric_value=metric_value,
                meets_target=meets_target,
                n_failures=len(snapshots),
                n_clusters=len(clustering_result.clusters),
                cluster_signal=clustering_result.signal.value,
                cluster_signal_reason=clustering_result.insufficient_data_reason,
                top_cluster_label=top_cluster_label,
                top_cluster_size=top_cluster_size,
                instruction_before=instruction_before,
                instruction_after=instruction_after,
                instruction_edit=edit,
                wall_seconds=(cycle_ended - cycle_started).total_seconds(),
                outcomes=tuple(report.outcomes),
                snapshots=tuple(snapshots),
                clusters=tuple(clustering_result.clusters),
                clustering_result=clustering_result,
            )
        )
        logger.info(
            "cycle %d/%d: metric=%.3f failures=%d clusters=%d %s",
            cycle_idx + 1,
            n_cycles,
            metric_value,
            len(snapshots),
            len(clustering_result.clusters),
            f"top={top_cluster_label!r}" if top_cluster_label else "no-edit",
        )

    ended_at = datetime.now(UTC)
    return DemoLoopReport(
        workflow_spec_id=spec.id,
        cycles=tuple(cycles),
        started_at=started_at,
        ended_at=ended_at,
        metric_target=metric.target_value,
        metric_family=metric.family,
        metric_direction=metric.direction,
    )


__all__ = [
    "DEFAULT_FAILURE_EXAMPLES_PER_CLUSTER",
    "DEFAULT_N_CYCLES",
    "CycleOutcome",
    "DemoLoopReport",
    "INSTRUCTION_SEPARATOR",
    "run_nl_gen_demo_loop",
]
