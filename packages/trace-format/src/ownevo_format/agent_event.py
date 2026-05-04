"""AgentEvent — typed event schema (Pydantic).

Implements the discriminated union defined in `packages/trace-format/SPEC.md`.
SPEC.md is the canonical contract. This module is its Python implementation.

Conventions enforced here:
  - `type` literal discriminates the variant
  - Every event has the common base fields (event_id, trace_id, ...)
  - ToolCallResult.error_class is non-null only when status="error" AND the
    error came from the sandbox runtime (Timeout / OOM / Crash) — D3
  - ToolCallResult.error is non-null iff status="error"
  - AgentEventAdapter is the canonical entry point for parsing dict -> typed event

Versioning: spec is at v1.0 (frozen 2026-05-04 per PLAN.md A3.4
schema-freeze; tag `v1.0-frozen-2026-W3`). Drift detection lives in
`tests/test_schema_freeze.py` against the snapshot at
`schemas/agent_event.v1.0.json`. To intentionally change the schema,
bump SPEC.md + this docstring, regenerate via
`scripts/regen_schemas.py`, and re-test the W7 UI rendering.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeGuard
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

# ---------------------------------------------------------------------------
# Common base
# ---------------------------------------------------------------------------


class AgentEventBase(BaseModel):
    """Fields every AgentEvent variant carries.

    See SPEC.md § "Common base fields (every event)".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    trace_id: UUID
    iteration_id: UUID | None = None
    timestamp: datetime
    parent_span_id: UUID | None = None


# ---------------------------------------------------------------------------
# Enums / type aliases
# ---------------------------------------------------------------------------

ToolCallStatus = Literal["ok", "error"]
MonitorSeverity = Literal["info", "warn", "error"]
MonitorName = Literal["loop_detection", "redundancy", "context_near_limit"]


class SandboxErrorClass(StrEnum):
    """D3 — sandbox runtime failure classes for tool_call_result.error_class.

    Distinct from logical errors inside the tool. The gate runner does NOT
    advance best_ever_score when any of these are present.
    Canonical definition lives here (trace-format); ownevo-kernel re-exports.
    """

    TIMEOUT = "Timeout"
    OOM = "OOM"
    CRASH = "Crash"


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------


class ContentDelta(AgentEventBase):
    """LLM streaming output token(s).

    `cumulative_text` is optional and populated when the streaming source
    can supply it. Downstream readers should not rely on it.
    """

    type: Literal["content_delta"]
    text: str
    model: str
    cumulative_text: str | None = None


class ReasoningDelta(AgentEventBase):
    """Reasoning tokens (Claude extended thinking, OpenAI reasoning).

    Stored separately from `content_delta` because reasoning tokens don't
    surface to customers and have different downstream uses (failure
    clustering may consume; the UI usually does not render).
    """

    type: Literal["reasoning_delta"]
    text: str
    model: str


class ToolCallStart(AgentEventBase):
    """Agent invokes a tool.

    `call_id` is the provider's call id (e.g., Anthropic's `toolu_*`),
    used to match with the corresponding ToolCallResult.
    """

    type: Literal["tool_call_start"]
    call_id: str
    name: str
    args: dict[str, Any]


class ToolCallResult(AgentEventBase):
    """Tool call returned (success or failure).

    D3 enforcement (sandbox failure semantics):
      - `status="ok"` → `error` and `error_class` MUST be None.
      - `status="error"` AND logical error from inside the tool →
        `error` is non-null, `error_class` is None.
      - `status="error"` AND sandbox runtime killed the call (Timeout, OOM,
        Crash) → both `error` and `error_class` are non-null.

    The gate runner reads `error_class` to decide whether to advance
    `best_ever_score`. It does NOT advance on any sandbox-runtime error.
    """

    type: Literal["tool_call_result"]
    call_id: str
    name: str
    status: ToolCallStatus
    output: Any
    duration_ms: int = Field(ge=0)
    error: str | None = None
    error_class: SandboxErrorClass | None = None

    @model_validator(mode="after")
    def _check_error_invariants(self) -> ToolCallResult:
        if self.status == "ok":
            if self.error is not None:
                raise ValueError("ToolCallResult: error must be None when status='ok'")
            if self.error_class is not None:
                raise ValueError(
                    "ToolCallResult: error_class must be None when status='ok'",
                )
        else:  # status == "error"
            if self.error is None:
                raise ValueError("ToolCallResult: error required when status='error'")
            # error_class is optional even when status='error'
            # (None means logical error inside the tool, non-None means sandbox runtime kill).
        return self


class SkillLoaded(AgentEventBase):
    """A skill entered the agent's context.

    `retention_acknowledged` records whether the agent confirmed reading
    the retention contract. Required for the retention-violation eval class
    (per SKILL_FORMAT.md).
    """

    type: Literal["skill_loaded"]
    skill_id: str
    version_seq: int = Field(ge=1)
    retention_acknowledged: bool = False


class Citation(AgentEventBase):
    """A source citation referenced in the agent's output."""

    type: Literal["citation"]
    ref: int = Field(ge=1)
    source: str
    quote: str


class MonitorSignal(AgentEventBase):
    """A programmatic monitor fired (loop detection, redundancy, context_near_limit).

    The MVP set is fixed at 3 monitors. New monitors require extending the
    `MonitorName` literal and bumping the spec version.
    """

    type: Literal["monitor_signal"]
    monitor: MonitorName
    severity: MonitorSeverity
    details: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Discriminated union
# ---------------------------------------------------------------------------

AgentEvent = Annotated[
    (
        ContentDelta
        | ReasoningDelta
        | ToolCallStart
        | ToolCallResult
        | SkillLoaded
        | Citation
        | MonitorSignal
    ),
    Field(discriminator="type"),
]
"""Discriminated union over all variants. Use `AgentEventAdapter` to parse."""


AgentEventAdapter: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)
"""Canonical entry point for parsing dict -> typed event.

Example:
    >>> event = AgentEventAdapter.validate_python({
    ...     "type": "skill_loaded",
    ...     "event_id": "...",
    ...     "trace_id": "...",
    ...     "timestamp": "2026-05-03T14:32:01Z",
    ...     "skill_id": "supplier-negotiation",
    ...     "version_seq": 7,
    ...     "retention_acknowledged": True,
    ... })
"""


# ---------------------------------------------------------------------------
# Type guards (TS-style, for ergonomic narrowing)
# ---------------------------------------------------------------------------


def is_content_delta(e: BaseModel) -> TypeGuard[ContentDelta]:
    return getattr(e, "type", None) == "content_delta"


def is_reasoning_delta(e: BaseModel) -> TypeGuard[ReasoningDelta]:
    return getattr(e, "type", None) == "reasoning_delta"


def is_tool_call_start(e: BaseModel) -> TypeGuard[ToolCallStart]:
    return getattr(e, "type", None) == "tool_call_start"


def is_tool_call_result(e: BaseModel) -> TypeGuard[ToolCallResult]:
    return getattr(e, "type", None) == "tool_call_result"


def is_skill_loaded(e: BaseModel) -> TypeGuard[SkillLoaded]:
    return getattr(e, "type", None) == "skill_loaded"


def is_citation(e: BaseModel) -> TypeGuard[Citation]:
    return getattr(e, "type", None) == "citation"


def is_monitor_signal(e: BaseModel) -> TypeGuard[MonitorSignal]:
    return getattr(e, "type", None) == "monitor_signal"
