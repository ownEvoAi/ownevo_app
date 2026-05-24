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
  4. `log.py` + audit chain integration via the `design-agent-negotiation`
     and `design-agent-ambiguity` entry kinds.
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
from .dimensions import (
    DESIGN_DIMENSIONS,
    DIMENSION_SPECS,
    DesignDimension,
    DimensionSpec,
    dimensions_remaining,
    spec_for,
)
from .interviewer import (
    DEFAULT_INTERVIEWER_MODEL,
    InterviewerError,
    OptionBrief,
    PriorAnswer,
    QuestionBrief,
    pick_next_question,
)
from .log import (
    DESIGN_AGENT_ACTOR,
    DesignAgentLog,
    DesignAgentLogEntry,
    load_design_agent_log,
    persist_design_agent_log,
)
from .prompts import (
    DISCOVERY_QUESTION_KINDS,
    GENERIC_DISCOVERY_QUESTIONS,
    TRACE_IMPORT_DISCOVERY_QUESTIONS,
    DiscoveryQuestion,
    DiscoveryQuestionKind,
    get_discovery_questions,
    get_trace_import_discovery_questions,
    known_template_ids,
)

__all__ = [
    "AmbiguityFinding",
    "AmbiguityKind",
    "AmbiguityReport",
    "AmbiguitySeverity",
    "DEFAULT_INTERVIEWER_MODEL",
    "DESIGN_AGENT_ACTOR",
    "DESIGN_DIMENSIONS",
    "DIMENSION_SPECS",
    "DISCOVERY_QUESTION_KINDS",
    "DesignAgentLog",
    "DesignAgentLogEntry",
    "DesignDimension",
    "DimensionSpec",
    "DiscoveryQuestion",
    "DiscoveryQuestionKind",
    "GENERIC_DISCOVERY_QUESTIONS",
    "InterviewerError",
    "TRACE_IMPORT_DISCOVERY_QUESTIONS",
    "OptionBrief",
    "PriorAnswer",
    "QuestionBrief",
    "analyze_workflow",
    "dimensions_remaining",
    "find_description_conflicts",
    "find_inferred_artifacts",
    "find_metric_direction_conflicts",
    "get_discovery_questions",
    "get_trace_import_discovery_questions",
    "known_template_ids",
    "load_design_agent_log",
    "persist_design_agent_log",
    "pick_next_question",
    "spec_for",
]
