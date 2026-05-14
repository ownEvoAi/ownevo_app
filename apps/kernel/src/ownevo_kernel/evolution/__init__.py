"""Evolution loop — 4-stage pipeline (greenfield per W1 spike).

Reference architecture: 4-stage evolution pipeline
(Tracker → Reflector → Curator → Proposer). See `docs/SPIKE-RESULT.md`
for the go/no-go decision on wholesale lift (result: greenfield).

    Tracker  (record agent hypothesis + outcome from a trace/iteration)
       ↓
    Reflector  (review the outcome — finalize, continue, or replan)
       ↓
    Curator  (cluster outcomes into named patterns)
       ↓
    Proposer  (turn patterns into structured proposals)

The 4-stage shape is implemented greenfield: ownEvo's substrate
(failure_clusters, eval_cases, traces, proposals) replaces the
incident-management memory store the reference used.

This module declares the Protocol interfaces for the 4 stages. Concrete
implementations land in W2 (`tracker.py`, `reflector.py`, `curator.py`,
`proposer.py`) once the gate + clustering pipelines exist to feed them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol
from uuid import UUID

from ownevo_kernel.types import FailureCluster, Iteration, Learning, Proposal


class ReflectionDecision(StrEnum):
    """Next-step decision emitted by the Reflector after reviewing an iteration.

    FINALIZE: gate passed AND best-ever advanced → gate runner writes new skill version.
    CONTINUE: gate passed BUT no improvement → log to learnings; start next iteration.
    REPLAN:   gate blocked OR sandbox error → drop hypothesis; agent retries fresh.
    """

    FINALIZE = "finalize"
    CONTINUE = "continue"
    REPLAN = "replan"


class Tracker(Protocol):
    """Records agent hypothesis + observed outcome for an iteration.

    Inputs come from the trace pipeline (W1.5). Outputs are appended to
    the `learnings` table and surfaced to the loop-stuck alert (W2.4a).
    """

    async def record_hypothesis(self, iteration_id: UUID, hypothesis: str) -> Learning: ...

    async def record_outcome(
        self,
        iteration_id: UUID,
        actual_outcome: str,
        success: bool,
    ) -> Learning: ...


class Reflector(Protocol):
    """Reviews an iteration's outcome — returns the next-step decision.

    The concrete implementation also persists a Learning entry as a side-effect,
    but the return value is the decision the loop runner acts on.
    """

    async def reflect(self, iteration: Iteration) -> ReflectionDecision: ...


class Curator(Protocol):
    """Promotes recurring failure observations to named clusters.

    ownEvo equivalent of core/'s `promote_patterns`. Reads from `traces`
    (failed iteration trajectories), embeds via sentence-transformers,
    clusters via HDBSCAN, labels via LLM. Writes to `failure_clusters`.

    Per W3 Track B: cluster-quality threshold rejects "1 mega-cluster" /
    "all noise" outputs in favor of a "more iterations needed" UI state.
    """

    async def promote(self, workflow_id: str) -> list[FailureCluster]: ...


class Proposer(Protocol):
    """Turns failure clusters into structured proposals for the gate.

    Reads from `failure_clusters`, emits `Proposal` rows in state=PENDING.
    The gate runner picks them up; D6 adds `regression_gate` as a
    `ProposalAction.action_type` so gate outcomes flow back through the
    same pipeline.
    """

    async def propose(self, cluster: FailureCluster) -> Proposal: ...


__all__ = ["ReflectionDecision", "Tracker", "Reflector", "Curator", "Proposer"]
