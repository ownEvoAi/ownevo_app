"""Anthropic SDK tool definitions for the 5 kernel agent_tools.

Each tool here is a thin wrapper over the corresponding `agent_tools/`
function: the SDK calls `dispatch_tool(name, args, ctx)`, we route to
the kernel function, and the result is shaped into a JSON-serializable
dict the model can read.

What stays in the kernel
------------------------
The agent_tools functions hold all the policy: train/test discipline
(read_metrics + analyze_failures refuse test-fold traces unless the
gate runner opts in), idempotent skill registration, sandbox-error
classification on run_pipeline. The middleware does **not** re-encode
any of that — it forwards the agent's args, catches the kernel's
exceptions, and shapes the response.

Tool schemas
------------
JSON Schema bodies are deliberately minimal: only the fields the agent
needs to set. Internal flags like `include_test_fold=True` are NOT
exposed (gate-runner-only per W2.1). `created_by` on `write_skill`
defaults to the model identifier from the runner — the agent doesn't
get to spoof its own attribution.

Error shaping
-------------
On any kernel-side exception, the tool result has `is_error=True` and
the message is the exception's string representation (truncated to
`_ERROR_MESSAGE_MAX_CHARS` so a runaway traceback doesn't poison the
context window). The model can see the error class via the prefix
(`SkillFormatError: ...`) and react. We deliberately do NOT raise out
of the dispatcher — Anthropic's tool_use protocol expects every call
to return a tool_result, and an unhandled exception would break the
agent loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from ...agent_tools.metrics import (
    TestFoldAccessRefused,
    analyze_failures,
    read_metrics,
)
from ...agent_tools.run_pipeline import PipelineResult, run_pipeline
from ...agent_tools.skills import (
    SkillFormatError,
    read_skill,
    write_skill,
)
from ...sandbox import LocalDockerSandbox

_ERROR_MESSAGE_MAX_CHARS = 4096
"""Cap on tool-error messages handed back to the model. A LightGBM
traceback can run thousands of lines — letting it through unbounded
would burn context with no upside since the model only needs the
exception class + first frame to act."""


# ---------------------------------------------------------------------------
# Kernel context — dependencies the tool dispatcher needs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KernelContext:
    """Per-agent-run dependencies for the 5 kernel tools.

    Built once at the top of `run_agent_turn`, passed to every tool
    dispatch. The agent never sees this — it's the closure over which
    the tool functions execute.

    Attributes:
        conn: asyncpg connection used by `read_skill` / `write_skill` /
            `read_metrics` / `analyze_failures`.
        sandbox: `LocalDockerSandbox` instance `run_pipeline` executes
            against. Caller chooses image + resource limits before
            constructing the context.
        actor: Goes on `write_skill.created_by` and is propagated into
            the trace by the runner. Format `agent:<model-id>` matches
            the AuditEntry actor convention.
        default_workflow_id: When the agent calls `analyze_failures`
            without a workflow_id, fall back to this. None means the
            tool will require an explicit workflow_id.
    """

    conn: asyncpg.Connection
    sandbox: LocalDockerSandbox
    actor: str
    default_workflow_id: str | None = None


# ---------------------------------------------------------------------------
# Tool schemas — exact shape Anthropic Messages API expects
# ---------------------------------------------------------------------------


def kernel_tool_definitions() -> list[dict[str, Any]]:
    """Return the 5 kernel tools as Anthropic API tool params.

    Returned as plain dicts so the call sites can splice them into
    `messages.create(tools=...)` without an SDK type import — the
    Anthropic SDK accepts dicts that match `ToolParam` and validates
    them server-side."""
    return [
        {
            "name": "read_skill",
            "description": (
                "Read the head version of a skill from the registry. Returns the "
                "full skill source including its YAML frontmatter (id, kind, "
                "retention contract, capability_tags). Use this before proposing "
                "changes — every diff is against the current head."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "description": (
                            "The skill's frontmatter id (e.g., "
                            "`m5.baseline.v1.feature_engineer`)."
                        ),
                    },
                },
                "required": ["skill_id"],
            },
        },
        {
            "name": "write_skill",
            "description": (
                "Register a new version of a skill. The frontmatter id inside "
                "`content` MUST equal `skill_id`; mismatches are rejected with "
                "SkillFormatError before any DB write. The new version becomes "
                "the head; the prior head is retained as version history. "
                "Pair with run_pipeline to validate the new version before the "
                "gate runs."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "description": "Skill id (must match frontmatter).",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Full skill source including YAML frontmatter. "
                            "For Python skills, the frontmatter is the "
                            "module-level docstring's `---` block."
                        ),
                    },
                    "diff_summary": {
                        "type": "string",
                        "description": (
                            "Optional one-line description of the change. "
                            "Surfaces in the audit log + approval UI."
                        ),
                    },
                },
                "required": ["skill_id", "content"],
            },
        },
        {
            "name": "run_pipeline",
            "description": (
                "Execute a skill version inside the hardened sandbox with "
                "structured input/output. The skill body must print a single "
                "JSON object on the last line of stdout — `outputs` parses that "
                "back. Sandbox errors (Timeout / OOM / Crash) are surfaced "
                "explicitly; a logical Python exception inside the skill returns "
                "status='error' with error_class=None. Use this to validate a "
                "proposed skill before assuming it improved anything — the gate "
                "will not advance best-ever on a sandbox-runtime error."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill_content": {
                        "type": "string",
                        "description": "The skill body to execute (full source).",
                    },
                    "input_data": {
                        "type": "object",
                        "description": (
                            "JSON-serializable dict exposed to the skill as a "
                            "Python global named `input_data`. Optional; defaults "
                            "to an empty dict."
                        ),
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "description": (
                            "Per-call sandbox wall-clock budget in seconds. "
                            "Defaults to 60. The whole call is also bounded by "
                            "task_timeout_seconds at the kernel layer."
                        ),
                    },
                    "memory_mb": {
                        "type": "integer",
                        "description": (
                            "cgroup memory cap in MiB. Defaults to 512. "
                            "Bigger values let the skill train heavier models "
                            "but raise the OOM-kill threshold."
                        ),
                    },
                },
                "required": ["skill_content"],
            },
        },
        {
            "name": "read_metrics",
            "description": (
                "Return the metric_outputs JSON for a specific trace. Use this to "
                "inspect what a skill version scored on a previous run. Train/test "
                "discipline: traces from the held-out test fold are blocked — only "
                "the gate runner sees them."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "trace_id": {
                        "type": "string",
                        "description": (
                            "Trace UUID (from a prior run_pipeline result, an "
                            "iteration row, or the analyze_failures output)."
                        ),
                    },
                },
                "required": ["trace_id"],
            },
        },
        {
            "name": "analyze_failures",
            "description": (
                "Return up to `k` recent traces for a workflow ranked by how many "
                "tool_call_result errors they contain (most failures first). The "
                "workflow_id defaults to the agent run's workflow when present. "
                "Train/test discipline: test-fold traces are filtered."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "workflow_id": {
                        "type": "string",
                        "description": (
                            "Workflow to scope the search to. Optional when the "
                            "agent run has a default workflow."
                        ),
                    },
                    "k": {
                        "type": "integer",
                        "description": (
                            "How many failure snapshots to return. Defaults to "
                            "10; capped at 100 to keep the result token-bounded."
                        ),
                    },
                },
                "required": [],
            },
        },
    ]


# ---------------------------------------------------------------------------
# Dispatch — agent's tool_use → kernel function call → JSON tool_result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolDispatchResult:
    """Outcome of one tool dispatch.

    `output` is what we hand back to Anthropic as the tool_result content.
    `is_error` mirrors the Anthropic protocol's `is_error` flag —
    set on any kernel-side exception so the model knows to react.

    `error_class` is propagated for `run_pipeline` only — it carries
    the sandbox-runtime classification (Timeout / OOM / Crash / None
    for a logical error). Threaded through to the trace's
    ToolCallResult event so the gate's D3 invariant holds end-to-end.
    """

    output: dict[str, Any] | str
    is_error: bool
    error_class: str | None
    duration_ms: int | None


_DEFAULT_PIPELINE_TIMEOUT_SECONDS = 60.0
_DEFAULT_PIPELINE_MEMORY_MB = 512
_MAX_ANALYZE_FAILURES_K = 100
"""Hard cap on `analyze_failures.k` so a "k=100000" call doesn't dump
the whole trace table into the agent's context."""


async def dispatch_tool(
    name: str,
    args: dict[str, Any],
    ctx: KernelContext,
) -> ToolDispatchResult:
    """Route one Anthropic tool_use call to the matching kernel function.

    Each branch builds a JSON-serializable response dict. Exceptions
    are caught and shaped into is_error=True results — the agent loop
    is in tool_use state and expects a tool_result for every call;
    raising here would break the protocol.
    """
    try:
        if name == "read_skill":
            return await _dispatch_read_skill(args, ctx)
        if name == "write_skill":
            return await _dispatch_write_skill(args, ctx)
        if name == "run_pipeline":
            return await _dispatch_run_pipeline(args, ctx)
        if name == "read_metrics":
            return await _dispatch_read_metrics(args, ctx)
        if name == "analyze_failures":
            return await _dispatch_analyze_failures(args, ctx)
    except (
        SkillFormatError,
        TestFoldAccessRefused,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        return _shape_exception(exc)
    return ToolDispatchResult(
        output=f"Unknown tool: {name!r}",
        is_error=True,
        error_class=None,
        duration_ms=None,
    )


# ---------------------------------------------------------------------------
# Per-tool branches
# ---------------------------------------------------------------------------


async def _dispatch_read_skill(
    args: dict[str, Any],
    ctx: KernelContext,
) -> ToolDispatchResult:
    skill_id = _required_str(args, "skill_id")
    record = await read_skill(ctx.conn, skill_id)
    if record is None:
        return ToolDispatchResult(
            output={
                "skill_id": skill_id,
                "found": False,
                "message": (
                    f"No skill registered under {skill_id!r}. Use write_skill to "
                    "create the first version."
                ),
            },
            is_error=False,
            error_class=None,
            duration_ms=None,
        )
    return ToolDispatchResult(
        output={
            "found": True,
            "skill_id": record.skill_id,
            "kind": record.kind,
            "version_id": str(record.version_id),
            "version_seq": record.version_seq,
            "content": record.content,
            "created_by": record.created_by,
        },
        is_error=False,
        error_class=None,
        duration_ms=None,
    )


async def _dispatch_write_skill(
    args: dict[str, Any],
    ctx: KernelContext,
) -> ToolDispatchResult:
    skill_id = _required_str(args, "skill_id")
    content = _required_str(args, "content")
    diff_summary = args.get("diff_summary")
    if diff_summary is not None and not isinstance(diff_summary, str):
        raise TypeError("diff_summary must be a string when present")

    register_result = await write_skill(
        ctx.conn,
        skill_id,
        content,
        created_by=ctx.actor,
        diff_summary=diff_summary,
    )
    return ToolDispatchResult(
        output={
            "skill_id": register_result.skill_id,
            "version_id": str(register_result.version_id),
            "version_seq": register_result.version_seq,
            "parent_version_id": (
                str(register_result.parent_version_id)
                if register_result.parent_version_id is not None
                else None
            ),
        },
        is_error=False,
        error_class=None,
        duration_ms=None,
    )


async def _dispatch_run_pipeline(
    args: dict[str, Any],
    ctx: KernelContext,
) -> ToolDispatchResult:
    skill_content = _required_str(args, "skill_content")
    input_data_raw = args.get("input_data")
    if input_data_raw is not None and not isinstance(input_data_raw, dict):
        raise TypeError("input_data must be a JSON object (dict) when present")

    timeout_seconds = float(
        args.get("timeout_seconds") or _DEFAULT_PIPELINE_TIMEOUT_SECONDS
    )
    memory_mb = int(args.get("memory_mb") or _DEFAULT_PIPELINE_MEMORY_MB)
    if timeout_seconds <= 0:
        raise ValueError(f"timeout_seconds must be positive; got {timeout_seconds}")
    if memory_mb <= 0:
        raise ValueError(f"memory_mb must be positive; got {memory_mb}")

    result: PipelineResult = await run_pipeline(
        ctx.sandbox,
        skill_content=skill_content,
        input_data=input_data_raw,
        timeout_seconds=timeout_seconds,
        memory_mb=memory_mb,
    )
    # Shape the response so the agent sees the same trust signals the
    # gate does: status, error_class (sandbox-runtime kill or None for
    # logical), structured outputs (parsed JSON last-line), tail-only
    # stdout/stderr to bound context cost.
    is_error = not result.ok
    return ToolDispatchResult(
        output={
            "status": result.status,
            "outputs": result.outputs,
            "raw_stdout": _tail(result.raw_stdout),
            "raw_stderr": _tail(result.raw_stderr),
            "duration_ms": result.duration_ms,
            "error": result.error,
            "error_class": result.error_class,
        },
        is_error=is_error,
        error_class=result.error_class,
        duration_ms=result.duration_ms,
    )


async def _dispatch_read_metrics(
    args: dict[str, Any],
    ctx: KernelContext,
) -> ToolDispatchResult:
    trace_id_raw = _required_str(args, "trace_id")
    try:
        trace_id = UUID(trace_id_raw)
    except ValueError as exc:
        raise ValueError(f"trace_id is not a valid UUID: {trace_id_raw!r}") from exc

    metrics = await read_metrics(ctx.conn, trace_id)
    if metrics is None:
        return ToolDispatchResult(
            output={
                "trace_id": trace_id_raw,
                "found": False,
                "message": "No metric_outputs recorded for this trace.",
            },
            is_error=False,
            error_class=None,
            duration_ms=None,
        )
    return ToolDispatchResult(
        output={
            "trace_id": trace_id_raw,
            "found": True,
            "metrics": metrics,
        },
        is_error=False,
        error_class=None,
        duration_ms=None,
    )


async def _dispatch_analyze_failures(
    args: dict[str, Any],
    ctx: KernelContext,
) -> ToolDispatchResult:
    workflow_id = args.get("workflow_id") or ctx.default_workflow_id
    if not isinstance(workflow_id, str) or not workflow_id:
        raise ValueError(
            "workflow_id is required (no default configured for this agent run)"
        )
    k_raw = args.get("k", 10)
    try:
        k = int(k_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"k must be an int; got {k_raw!r}") from exc
    if k <= 0:
        raise ValueError(f"k must be positive; got {k}")
    k = min(k, _MAX_ANALYZE_FAILURES_K)

    snapshots = await analyze_failures(ctx.conn, workflow_id=workflow_id, k=k)
    return ToolDispatchResult(
        output={
            "workflow_id": workflow_id,
            "k": k,
            "results": [
                {
                    "trace_id": str(s.trace_id),
                    "iteration_id": (
                        str(s.iteration_id) if s.iteration_id is not None else None
                    ),
                    "skill_version_id": (
                        str(s.skill_version_id)
                        if s.skill_version_id is not None
                        else None
                    ),
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                    "tool_errors": s.tool_errors,
                    "metric_outputs": s.metric_outputs,
                    "fold": s.fold,
                }
                for s in snapshots
            ],
        },
        is_error=False,
        error_class=None,
        duration_ms=None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _required_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise KeyError(f"required tool argument missing or empty: {key!r}")
    return value


def _tail(text: str | None, *, max_chars: int = 4000) -> str:
    """Return the last `max_chars` of `text` to bound tool-result size."""
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text
    return "[…truncated…]\n" + text[-max_chars:]


def _shape_exception(exc: BaseException) -> ToolDispatchResult:
    msg = f"{type(exc).__name__}: {exc}"
    if len(msg) > _ERROR_MESSAGE_MAX_CHARS:
        msg = msg[: _ERROR_MESSAGE_MAX_CHARS] + "…[truncated]"
    return ToolDispatchResult(
        output=msg,
        is_error=True,
        error_class=None,
        duration_ms=None,
    )


__all__ = [
    "KernelContext",
    "ToolDispatchResult",
    "dispatch_tool",
    "kernel_tool_definitions",
]
