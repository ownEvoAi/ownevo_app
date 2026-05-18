"""Design-agent module — conversational refinement at every loop stage.

The design agent runs alongside the domain expert at decision points where
NL-gen would otherwise have to guess. First entry point is authoring time
on `/workflows/new`: a short discovery interview surfaces metric trade-offs
and semantic ambiguities before the kernel spends tokens generating the
WorkflowSpec / SimulationPlan / EvalCaseSet trio.

This package ships in slices:

  1. `prompts/` — the template-aware prompt library.
  2. `POST /api/design-agent/next-question` — stateless discovery interview endpoint.
  3. `ambiguity.py` — post-generation ambiguity-detection pass over the
     WorkflowSpec + MetricDefinition.
  4. (next) audit-chain integration via a `design-agent-negotiation` kind.
"""

from .ambiguity import (
    AmbiguityFinding,
    AmbiguityKind,
    AmbiguityReport,
    AmbiguitySeverity,
    analyze_workflow,
    find_description_conflicts,
    find_inferred_artifacts,
    find_metric_direction_conflicts,
)
from .prompts import (
    DISCOVERY_QUESTION_KINDS,
    GENERIC_DISCOVERY_QUESTIONS,
    DiscoveryQuestion,
    DiscoveryQuestionKind,
    get_discovery_questions,
    known_template_ids,
)

__all__ = [
    "AmbiguityFinding",
    "AmbiguityKind",
    "AmbiguityReport",
    "AmbiguitySeverity",
    "DISCOVERY_QUESTION_KINDS",
    "DiscoveryQuestion",
    "DiscoveryQuestionKind",
    "GENERIC_DISCOVERY_QUESTIONS",
    "analyze_workflow",
    "find_description_conflicts",
    "find_inferred_artifacts",
    "find_metric_direction_conflicts",
    "get_discovery_questions",
    "known_template_ids",
]
