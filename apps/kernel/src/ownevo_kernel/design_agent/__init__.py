"""Design-agent module — conversational refinement at every loop stage.

The design agent runs alongside the domain expert at decision points where
NL-gen would otherwise have to guess. First entry point is authoring time
on `/workflows/new`: a short discovery interview surfaces metric trade-offs
and semantic ambiguities before the kernel spends tokens generating the
WorkflowSpec / SimulationPlan / EvalCaseSet trio.

This package ships in slices:

  1. `prompts/` — the template-aware prompt library (this slice).
  2. (next) `POST /api/design-agent/next-question` endpoint.
  3. (next) `ambiguity.py` — post-generation ambiguity-detection pass.
  4. (next) audit-chain integration via a `design-agent-negotiation` kind.
"""

from .prompts import (
    GENERIC_DISCOVERY_QUESTIONS,
    DiscoveryQuestion,
    DiscoveryQuestionKind,
    get_discovery_questions,
    known_template_ids,
)

__all__ = [
    "GENERIC_DISCOVERY_QUESTIONS",
    "DiscoveryQuestion",
    "DiscoveryQuestionKind",
    "get_discovery_questions",
    "known_template_ids",
]
