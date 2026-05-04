"""NL-gen pipeline (W3 Track A).

A3.1: NL → workflow spec — `spec.WorkflowSpec` is the frozen-schema artifact.
A3.2: NL → simulator (`sim_generator.py`, deferred).
A3.3: Sim runs in sandbox (no module — exercised via tests).
A3.4: Schema FROZEN at end of W3 — bumps `SCHEMA_VERSION` to "1.0".

A4.1: NL → eval cases (`eval_generator.py`, deferred).
A4.2: NL → success metric (`metric_generator.py`, deferred).
A4.6: meta-eval spec (`meta_eval/`, deferred to W4-W5).
"""

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
]
