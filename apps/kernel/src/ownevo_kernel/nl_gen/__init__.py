"""NL-gen pipeline (W3 Track A).

A3.1: NL → workflow spec — `spec.WorkflowSpec` is the frozen-schema artifact.
A3.2: WorkflowSpec → simulator — `sim_plan.SimulationPlan` (LLM artifact)
      + `sim_render.render_simulation_module` (pure renderer, AST safety).
      `sim_generator.generate_simulation_plan` is the Anthropic tool-use call.
A3.3: Sim runs in sandbox — exercised via tests over the rendered output.
A3.4: Schema FROZEN at end of W3 — bumps `SCHEMA_VERSION` to "1.0".

A4.1: NL → eval cases — `eval_case_set.EvalCaseSet` (frozen schema)
      + `eval_generator.generate_eval_case_set` (Anthropic tool-use)
      + `eval_replay.replay_set` (in-process replay) +
      `eval_persistence.persist_eval_case_set` (DB write).
A4.2: NL → success metric — `metric_def.MetricDefinition` (frozen
      schema) + `metric_compute.compute_metric` (pure compute over
      ReplayResults) + `metric_generator.generate_metric_definition`
      (Anthropic tool-use).
A4.6: meta-eval spec (`meta_eval/`, deferred to W4-W5).
"""

from .eval_case_set import (
    SCHEMA_VERSION as EVAL_CASE_SET_SCHEMA_VERSION,
)
from .eval_case_set import (
    EvalCaseSet,
    GeneratedEvalCase,
)
from .eval_generator import (
    DEFAULT_MAX_TOKENS as EVAL_DEFAULT_MAX_TOKENS,
)
from .eval_generator import (
    DEFAULT_MODEL as EVAL_DEFAULT_MODEL,
)
from .eval_generator import (
    SYSTEM_PROMPT as EVAL_SYSTEM_PROMPT,
)
from .eval_generator import (
    TOOL_DESCRIPTION as EVAL_TOOL_DESCRIPTION,
)
from .eval_generator import (
    TOOL_NAME as EVAL_TOOL_NAME,
)
from .eval_generator import (
    EvalCaseSetValidationError,
    NoEvalToolUseError,
    generate_eval_case_set,
)
from .eval_persistence import persist_eval_case_set
from .eval_replay import (
    EvalReplayError,
    ReplayResult,
    replay_case,
    replay_set,
)
from .metric_compute import (
    MetricComputeError,
    MetricResult,
    compute_metric,
)
from .metric_def import (
    SCHEMA_VERSION as METRIC_DEFINITION_SCHEMA_VERSION,
)
from .metric_def import (
    MetricDefinition,
    MetricFamily,
)
from .metric_generator import (
    DEFAULT_MAX_TOKENS as METRIC_DEFAULT_MAX_TOKENS,
)
from .metric_generator import (
    DEFAULT_MODEL as METRIC_DEFAULT_MODEL,
)
from .metric_generator import (
    SYSTEM_PROMPT as METRIC_SYSTEM_PROMPT,
)
from .metric_generator import (
    TOOL_DESCRIPTION as METRIC_TOOL_DESCRIPTION,
)
from .metric_generator import (
    TOOL_NAME as METRIC_TOOL_NAME,
)
from .metric_generator import (
    MetricDefinitionValidationError,
    MetricDirectionMismatchError,
    NoMetricToolUseError,
    generate_metric_definition,
)
from .sim_generator import (
    DEFAULT_MAX_TOKENS as SIM_DEFAULT_MAX_TOKENS,
)
from .sim_generator import (
    DEFAULT_MODEL as SIM_DEFAULT_MODEL,
)
from .sim_generator import (
    SYSTEM_PROMPT as SIM_SYSTEM_PROMPT,
)
from .sim_generator import (
    TOOL_DESCRIPTION as SIM_TOOL_DESCRIPTION,
)
from .sim_generator import (
    TOOL_NAME as SIM_TOOL_NAME,
)
from .sim_generator import (
    NoSimToolUseError,
    SimulationPlanValidationError,
    generate_simulation_plan,
)
from .sim_plan import (
    ALLOWED_IMPORTS,
    EventField,
    SimulationPlan,
)
from .sim_render import SimRenderError, render_simulation_module
from .spec import (
    SCHEMA_VERSION,
    AgentTool,
    DataSource,
    Domain,
    Entity,
    EntityField,
    EnvGenerator,
    FieldType,
    Persona,
    Provenance,
    ReviewerSpec,
    SuccessCriterionStub,
    ToolParam,
    UILayout,
    UITab,
    WorkflowEnvironment,
    WorkflowSpec,
)
from .workflow_spec_generator import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    NLGenError,
    NoToolUseError,
    SYSTEM_PROMPT,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    WorkflowSpecValidationError,
    generate_workflow_spec,
)

__all__ = [
    "SCHEMA_VERSION",
    "Domain",
    "FieldType",
    "Provenance",
    "EntityField",
    "Entity",
    "DataSource",
    "EnvGenerator",
    "Persona",
    "WorkflowEnvironment",
    "ToolParam",
    "AgentTool",
    "ReviewerSpec",
    "SuccessCriterionStub",
    "UITab",
    "UILayout",
    "WorkflowSpec",
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "SYSTEM_PROMPT",
    "NLGenError",
    "WorkflowSpecValidationError",
    "NoToolUseError",
    "generate_workflow_spec",
    # A3.2
    "ALLOWED_IMPORTS",
    "EventField",
    "SimulationPlan",
    "SimRenderError",
    "render_simulation_module",
    "SIM_DEFAULT_MODEL",
    "SIM_DEFAULT_MAX_TOKENS",
    "SIM_TOOL_NAME",
    "SIM_TOOL_DESCRIPTION",
    "SIM_SYSTEM_PROMPT",
    "NoSimToolUseError",
    "SimulationPlanValidationError",
    "generate_simulation_plan",
    # A4.1
    "EVAL_CASE_SET_SCHEMA_VERSION",
    "EvalCaseSet",
    "GeneratedEvalCase",
    "EVAL_DEFAULT_MODEL",
    "EVAL_DEFAULT_MAX_TOKENS",
    "EVAL_TOOL_NAME",
    "EVAL_TOOL_DESCRIPTION",
    "EVAL_SYSTEM_PROMPT",
    "NoEvalToolUseError",
    "EvalCaseSetValidationError",
    "generate_eval_case_set",
    "EvalReplayError",
    "ReplayResult",
    "replay_case",
    "replay_set",
    "persist_eval_case_set",
    # A4.2
    "METRIC_DEFINITION_SCHEMA_VERSION",
    "MetricDefinition",
    "MetricFamily",
    "MetricComputeError",
    "MetricResult",
    "compute_metric",
    "METRIC_DEFAULT_MODEL",
    "METRIC_DEFAULT_MAX_TOKENS",
    "METRIC_TOOL_NAME",
    "METRIC_TOOL_DESCRIPTION",
    "METRIC_SYSTEM_PROMPT",
    "NoMetricToolUseError",
    "MetricDefinitionValidationError",
    "MetricDirectionMismatchError",
    "generate_metric_definition",
]
