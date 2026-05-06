"""NL-gen pipeline (W3 Track A).

A3.1: NL → workflow spec — `spec.WorkflowSpec` is the frozen-schema artifact.
A3.2: WorkflowSpec → simulator — `sim_plan.SimulationPlan` (LLM artifact)
      + `sim_render.render_simulation_module` (pure renderer, AST safety).
      `sim_generator.generate_simulation_plan` is the Anthropic tool-use call.
A3.3: Sim runs in sandbox — exercised via tests over the rendered output.
A3.4: Schema FROZEN at end of W3 — bumps `SCHEMA_VERSION` to "1.0".

A4.1: NL → eval cases — `eval_case_set.EvalCaseSet` + `eval_generator.generate_eval_case_set` + `eval_replay` + `eval_persistence`.
A4.2: NL → success metric (`metric_generator.py`, deferred).
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
]
