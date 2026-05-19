"""Agent solver for A4.4: Claude predicts hidden label per case.

The smoke-test seam between A4.1 eval cases (which carry deterministic
ground-truth labels from the sim) and a real agent loop. For each
case, the agent receives:

  * The workflow's plain-English description (so it understands the task).
  * The workflow's tool definitions (vocabulary; signatures only — no
    actual tool execution in the v1 solver).
  * The trajectory from step 0 through `target_step_index`, with the
    `target_label_field` REDACTED on the target event only. Past
    events keep their labels — those are the training signal a real
    agent would have access to (past markdown weeks, past defaults,
    past flagged clauses).

…and emits, via single-turn forced tool-use:

  * `predict_label(value: bool, rationale: str)` — bool prediction +
    one-line plain English the audit trail captures.

The result is wired into the existing `compute_metric` path via
`ReplayResult` so the scoring is identical to the deterministic
`run_replay` flow — only the source of `actual_value` changes.

Why single-turn forced tool-use (not a multi-turn agent loop with
tools the agent can call):

  * Keeps the smoke test fast + cheap (12 cases × 3 workflows = 36
    calls; haiku is the default model).
  * Keeps the failure mode legible — if the agent can't classify with
    full visibility minus the target label, multi-turn tool use
    won't save it. The smoke test exercises whether the *artifacts*
    are well-formed enough to drive a model, not whether the
    sim+tools are realistic enough for a multi-turn agent.
  * The multi-turn agent loop arrives in W5+ once the workspace UI
    + approval surface are in place; the per-case bool framing here
    is the simplest contract the gate can score.

The Inspect AI Task adapter (A4.3 `build_inspect_task`) carries the
same per-case shape under `Sample.metadata`; an A5+ Inspect AI driven
solver can swap this single-turn predictor without changing the
report shape.

Cost note: defaults to haiku for a reason — opus on this prompt is
~10x cost with marginal quality gain on bool classification. Override
via `model=` if a workflow shows headroom under haiku and needs
escalation.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ownevo_kernel.nl_gen.eval_case_set import EvalCaseSet, GeneratedEvalCase
from ownevo_kernel.nl_gen.eval_replay import ReplayResult, exec_sim_module
from ownevo_kernel.nl_gen.metric_def import MetricDefinition
from ownevo_kernel.nl_gen.sim_plan import SimulationPlan
from ownevo_kernel.nl_gen.spec import WorkflowSpec
from ownevo_kernel.nl_gen.workflow_spec_generator import NLGenError
from pydantic import ValidationError

if TYPE_CHECKING:  # pragma: no cover - import only for static type-check
    from anthropic import AsyncAnthropic
    from openai import AsyncOpenAI

    from .token_budget import TokenBudget


DEFAULT_MODEL = os.environ.get("OWNEVO_AGENT_SOLVER_MODEL") or "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4_000
"""Bool + one-line rationale fits in <500 tokens; the Operate-tab
`output_payload_json` (multi-primitive shape: metrics + time_series +
table + alerts + kanban + ...) eats 1.5-3k tokens — a 1k cap forces
the model to silently drop the payload to fit, so we budget for it."""
DEFAULT_MAX_TOKENS_OPENAI = 8_000
"""Higher cap for OpenAI-compat path: reasoning/thinking models emit a
long preamble before committing the tool call; 1k hits the wall too early."""

REDACTED_TOKEN = "<REDACTED>"
"""Sentinel value substituted into the target event's label field."""

TOOL_NAME = "predict_label"
TOOL_DESCRIPTION = (
    "Emit (a) a single bool prediction `value` for the redacted label "
    "field at the target step, (b) a one-line plain-English "
    "`rationale`, and (c) an `output_payload_json` string carrying a "
    "JSON object whose keys match the Operate-tab payload block in "
    "the user message (forecast curves, redline pairs, recommendation "
    "rows, etc., shaped from the trajectory + workflow description). "
    "Always set `output_payload_json` to at least `'{}'`. Call this "
    "tool exactly once."
)
TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "value": {
            "type": "boolean",
            "description": (
                "Predicted value of the redacted label field at the target step."
            ),
        },
        "rationale": {
            "type": "string",
            "minLength": 1,
            "description": (
                "One short sentence explaining the prediction. Surfaces in "
                "the audit trail next to the score."
            ),
        },
        "output_payload_json": {
            "type": "string",
            "description": (
                "JSON-encoded domain-shaped output for the Operate UI: a "
                "single JSON object whose keys are listed in the user "
                "message's `Operate-tab payload` block (e.g. "
                "`{\"time_series\": {...}, \"table\": {...}, \"alerts\": "
                "[...]}`). MUST be a valid JSON string, NOT a Python "
                "dict — start with '{' and end with '}'. Always include; "
                "pass `'{}'` only when the user message does NOT list an "
                "Operate-tab payload block. The gate scores only on "
                "`value`; this surfaces in the Operate tab."
            ),
            "minLength": 2,
        },
    },
    "required": ["value", "rationale", "output_payload_json"],
}

SYSTEM_PROMPT = (
    "You are an expert classifier embedded inside ownEvo's NL-gen "
    "smoke-test loop. You receive (a) a workflow's plain-English "
    "description, (b) the tools the workflow's agent has access to (for "
    "vocabulary; you do not call them), (c) a deterministic event "
    "trajectory through some target step, (d) the name of the redacted "
    "bool label at the target step, and (e) a gate-metric framing block "
    "telling you which error mode is the dominant cost on this workflow.\n\n"
    "Your job: predict the redacted label as True or False. Past events "
    "in the trajectory carry their true labels — those are your training "
    "signal. The target event has the label field replaced with "
    f"{REDACTED_TOKEN!r}; every other field on the target event is the "
    "visible state at decision time.\n\n"
    "Rules:\n"
    "1. Call `predict_label` exactly once with THREE fields:\n"
    "   a. `value`: bool — the prediction.\n"
    "   b. `rationale`: str — one short sentence.\n"
    "   c. `output_payload_json`: a JSON-encoded string (starts with "
    "`{`, ends with `}`). When the user message includes an `Operate-"
    "tab payload` block, fill the JSON with EVERY key listed there "
    "(e.g. `time_series`, `table`, `alerts`) populated with workflow-"
    "shaped content the operator would see in production (a 7+ point "
    "forecast for demand workflows; a redline pair for review "
    "workflows; recommendation rows for risk workflows). When there's "
    "no block, pass `\"{}\"`. The eval gate scores only on `value`, "
    "but `output_payload_json` is ALWAYS required.\n"
    "2. Use the past trajectory + the visible state at the target step. "
    "The hidden label was computed deterministically from earlier state, "
    "so there IS a learnable rule — your job is to find it.\n"
    "3. Do not invent fields the trajectory doesn't show for the bool "
    "prediction. The trajectory is the entire context for `value`; the "
    "workflow description is framing. For `output_payload_json`, you "
    "may extrapolate from the trajectory + workflow description to "
    "produce the kind of artifact a domain expert would expect to "
    "see — that extrapolation is the entire point of the Operate view.\n"
    "4. Read the gate-metric framing block and let it shape your tie-"
    "breaker on borderline cases. A `recall`-gated workflow penalizes "
    "False Negatives heavily — predict True under uncertainty when the "
    "evidence even moderately supports it. A `precision`-gated workflow "
    "penalizes False Positives — require strong evidence before True. "
    "`balanced_accuracy` and `f1` weight both errors; calibrate without "
    "a default lean. The metric is given to you per call; use it."
)


def _maybe_no_think_suffix(model: str) -> str:
    """Suppress thinking traces on Qwen3-base models via `/no_think` directive.

    Qwen3-base builds (qwen3:*, qwen3-coder:*) ship with thinking mode ON by
    default. Without suppression, the `<think>...</think>` reasoning trace
    consumes the entire max_tokens budget before the model commits to a
    `predict_label` tool call (see Ollama issue #14502, Crush #2457, F14h-hang
    in docs/local-model-testing.md). The `/no_think` soft-switch disables
    thinking for the current turn — appended to the system prompt where Qwen
    parses it.

    Note: the match also covers qwen3.5 and qwen3.6 variants (substring), but
    those lineages embed thinking more deeply and are not reliably suppressed
    by this directive (see F14i). Applying it to them is harmless — the
    directive is treated as plain text by models that don't parse it.
    """
    if "qwen3" in model.lower():
        return "\n\n/no_think"
    return ""


class AgentSolverError(NLGenError):
    """Solver-level failure: API errors, trajectory bounds, or sim execution.

    Subclass of NLGenError so smoke-test orchestrators can catch the
    NL-gen + agent-solver surfaces uniformly. The original exception
    is chained via `__cause__`.
    """


class NoPredictToolUseError(AgentSolverError):
    """Agent stopped without calling `predict_label`."""

    def __init__(
        self, message: str, *, stop_reason: str | None, content_preview: str
    ):
        super().__init__(message)
        self.stop_reason = stop_reason
        self.content_preview = content_preview


class PredictToolValidationError(AgentSolverError):
    """Agent called `predict_label` but the input failed schema validation."""

    def __init__(
        self, message: str, *, raw_input: Any, pydantic_error: ValidationError | None = None
    ):
        super().__init__(message)
        self.raw_input = raw_input
        self.pydantic_error = pydantic_error


@dataclass(frozen=True)
class AgentPrediction:
    """One agent prediction.

    `case_id` ties the prediction back to the source case; `value` is
    the bool the agent emitted; `rationale` is the one-line audit-trail
    explanation; `model` records which model produced this for
    cross-run comparability. `output_payload` is the optional
    domain-shaped artifact the agent emits when the workflow's Operate
    UI declares primitives that need richer-than-bool content (forecast
    curves, redline pairs, recommendation tables). None when the agent
    didn't emit one — Operate falls back to its empty state.
    """

    case_id: str
    value: bool
    rationale: str
    model: str
    output_payload: dict[str, Any] | None = None




def _trajectory_for_case(
    case: GeneratedEvalCase, ns: dict[str, Any]
) -> list[dict[str, Any]]:
    """Run the sim and return events 0 .. target_step_index inclusive."""
    run_simulation = ns["run_simulation"]
    result = run_simulation(case.sim_seed, case.n_steps)
    trajectory = result["trajectory"]
    if case.target_step_index >= len(trajectory):
        raise AgentSolverError(
            f"case {case.case_id!r}: target_step_index="
            f"{case.target_step_index} is past the trajectory length "
            f"({len(trajectory)})"
        )
    return trajectory[: case.target_step_index + 1]


def _redact_target_event(
    events: list[dict[str, Any]], target_label_field: str
) -> list[dict[str, Any]]:
    """Replace the target event's label field with REDACTED_TOKEN.

    Past events keep their true labels — those are training signal.
    Only the last event (target) is redacted. The original event dict
    is not mutated.
    """
    if not events:
        return events
    redacted = dict(events[-1])
    redacted[target_label_field] = REDACTED_TOKEN
    return [*events[:-1], redacted]


_METRIC_FAMILY_GUIDANCE = {
    # Tuned for the smoke-test failure mode observed on haiku 4.5
    # (2026-05-05): with no metric framing, the agent defaulted to
    # False on every sparse-True workflow and tanked recall to 0.0.
    # Each entry names the dominant error cost for that family so the
    # tie-breaker on borderline cases lands the right way.
    "recall": (
        "False Negatives are the dominant cost. Predict True when the "
        "evidence even moderately supports it; missing a true positive "
        "is much worse than a borderline false alarm."
    ),
    "precision": (
        "False Positives are the dominant cost. Require strong evidence "
        "before predicting True; spurious alerts are worse than missing "
        "a borderline positive."
    ),
    "f1": (
        "Both error modes hurt symmetrically. Calibrate without a "
        "default lean — predict the class the evidence actually supports."
    ),
    "balanced_accuracy": (
        "Both classes count equally regardless of class frequency. Do "
        "not bias toward the larger class — class imbalance does not "
        "imply the rare class should be predicted rarely."
    ),
    "specificity": (
        "False Positives are the dominant cost. Predict True only when "
        "the evidence is strong; the negative class is the costly one "
        "to confuse."
    ),
    "pass_rate": (
        "Both error modes count equally. Predict the class the evidence "
        "supports without a default lean."
    ),
}


def _metric_framing(metric: MetricDefinition) -> str:
    """Per-workflow gate-framing block injected into the user message.

    Names the metric (so the agent can re-derive its error-mode prior),
    the target value (so the agent knows the bar isn't 100%), and the
    family-specific guidance from `_METRIC_FAMILY_GUIDANCE`. Direction
    is included so a future minimize-direction family doesn't break the
    framing silently.

    The block is short (<300 chars) — long enough to land the
    asymmetry, short enough not to dominate the trajectory rendering.
    """
    guidance = _METRIC_FAMILY_GUIDANCE.get(
        metric.family,
        "Calibrate without a default lean.",
    )
    return (
        f"## Gate metric\n"
        f"family: `{metric.family}`  ·  direction: `{metric.direction}`  "
        f"·  target: {metric.target_value:.2f}\n"
        f"{guidance}"
    )


def _format_tools_for_context(spec: WorkflowSpec) -> str:
    """Render the workflow's tools as plain-English vocabulary.

    The agent does NOT call these — they're context for what kind of
    actions the live workflow's agent would take. Helps the LLM tune
    its mental model of the domain.
    """
    lines = []
    for tool in spec.tools:
        inputs = ", ".join(
            f"{p.name}: {p.type}" for p in tool.inputs
        ) or "no inputs"
        outputs = ", ".join(
            f"{p.name}: {p.type}" for p in tool.outputs
        ) or "no outputs"
        lines.append(
            f"  - {tool.name}({inputs}) → ({outputs}) — {tool.description}"
        )
    return "\n".join(lines)


# Per-primitive payload key + canonical shape. The web Operate resolver
# looks up `output_payload[key]` for each primitive declared on the
# spec's Operate tab; agreement here is what makes the round-trip work.
# Shapes are intentionally thin — enough for the renderer to populate
# the primitive, not enough to bloat the agent's tool call budget.
_PRIMITIVE_PAYLOAD_GUIDE: dict[str, tuple[str, str]] = {
    "MetricCards": (
        "metrics",
        '[{ "label": str, "value": str | number, "delta_pct"?: number }, ...] '
        "— headline numbers the operator sees first. 2–4 items.",
    ),
    "TimeSeriesChart": (
        "time_series",
        '{ "title"?: str, "series": [{ "name": str, "points": '
        '[{ "t": str, "value": number }, ...] }], "y_format"?: '
        '"number" | "percent" | "currency" } — for forecasts, the '
        '"t" key is an ISO date or step label; the "value" is the '
        "predicted quantity.",
    ),
    "TableView": (
        "table",
        '{ "title"?: str, "columns": [{ "key": str, "label": str }, ...], '
        '"rows": [{ ...column keys... }] } — one row per recommendation '
        "or per affected entity (account, SKU, ticket). Pick columns "
        "an operator would scan to make the call.",
    ),
    "AlertList": (
        "alerts",
        '[{ "severity": "low" | "medium" | "high", "title": str, '
        '"meta"?: str }, ...] — things the operator should look at '
        "RIGHT NOW. Cap at 3–5.",
    ),
    "KanbanBoard": (
        "kanban",
        '{ "columns": [{ "key": str, "label": str }, ...], '
        '"cards": [{ "column_key": str, "title": str, "body"?: str, '
        '"meta"?: str }, ...] } — work-items grouped by state.',
    ),
    "ScheduleGrid": (
        "schedule",
        '{ "row_labels": [str, ...], "col_labels": [str, ...], '
        '"cells": [{ "row": str, "col": str, "value": number, '
        '"target"?: number }, ...] } — staffing or capacity grid.',
    ),
    "ConversationView": (
        "conversation",
        '{ "turns": [{ "role": "user" | "agent" | "tool", '
        '"text": str, "ts"?: str }, ...] } — threaded exchange '
        "with the customer or counter-party.",
    ),
    "SideBySideView": (
        "side_by_side",
        '{ "left": { "title": str, "body": str }, "right": '
        '{ "title": str, "body": str } } — for redlines, left is '
        'the original clause, right is the proposed change.',
    ),
    "DocumentReader": (
        "document",
        '{ "blocks": [{ "type": "heading" | "paragraph" | "clause", '
        '"text": str, "id"?: str }, ...], "annotations"?: '
        '[{ "block_id": str, "text": str, "kind"?: "issue" | "note" '
        '| "suggest" }, ...] } — structured doc + margin notes.',
    ),
}


def _operate_primitives(spec: WorkflowSpec) -> list[str]:
    """Pick the operate-tab primitive types declared on the spec.

    Mirrors the web resolver's tab-fallback: look for a tab literally
    named "operate" (case-insensitive), else the second tab, else the
    first. Returns the distinct primitive type names; empty if the
    spec has no UI plan.
    """
    ui = getattr(spec, "ui", None)
    if ui is None:
        return []
    tabs = list(getattr(ui, "tabs", None) or [])
    if not tabs:
        return []
    operate_tab = next(
        (t for t in tabs if (getattr(t, "name", "") or "").lower() == "operate"),
        None,
    )
    if operate_tab is None:
        operate_tab = tabs[1] if len(tabs) >= 2 else tabs[0]
    primitives = list(getattr(operate_tab, "primitives", None) or [])
    seen: list[str] = []
    for p in primitives:
        ptype = getattr(p, "type", None)
        if isinstance(ptype, str) and ptype in _PRIMITIVE_PAYLOAD_GUIDE and ptype not in seen:
            seen.append(ptype)
    return seen


def _format_output_payload_guidance(spec: WorkflowSpec) -> str | None:
    """Per-workflow `output_payload` shape, derived from the spec's UI.

    Returns None when the spec declares no renderable primitives on its
    Operate tab — the agent then omits `output_payload` and the Operate
    UI stays in its honest empty state.

    The block tells the agent which keys it should fill on
    `output_payload` and what shape each key expects. The agent emits
    workflow-correct content; the resolver renders it through the
    matching web primitive.
    """
    types = _operate_primitives(spec)
    if not types:
        return None
    lines: list[str] = []
    for ptype in types:
        key, shape = _PRIMITIVE_PAYLOAD_GUIDE[ptype]
        lines.append(f"- `{key}` ({ptype}): {shape}")
    bullets = "\n".join(lines)
    return (
        "## Operate-tab payload (REQUIRED — pass `output_payload_json`)\n"
        "On the same `predict_label` tool call, set "
        "`output_payload_json` to a JSON-encoded string (i.e. a string "
        "starting with `{` and ending with `}`) carrying the keys "
        "listed below, each populated with real domain content the "
        "agent acting in production would have produced (forecast "
        "curves with real numbers, redline pairs with real clause "
        "text, recommendation rows with real account/SKU/case ids, "
        "etc.). Extrapolate confidently from the trajectory + workflow "
        "description — do not hand back empty arrays or placeholders. "
        "The eval gate still scores only on `value`; this string is "
        "what makes the Operate view useful.\n\n"
        f"{bullets}\n\n"
        "Example wrapper (replace the inner keys with what's listed "
        "above):\n"
        '`output_payload_json`: `\'{ "metrics": [...], "alerts": [...] }\'`'
    )


def _format_user_message(
    spec: WorkflowSpec,
    case: GeneratedEvalCase,
    trajectory: list[dict[str, Any]],
    metric: MetricDefinition,
    *,
    per_workflow_instruction: str | None = None,
) -> str:
    """Assemble the per-case user message.

    Pieces, in order:
      * Gate-metric framing (family + direction + target + dominant
        error cost) — load-bearing because it shapes the tie-breaker
        on borderline cases. With no framing, haiku 4.5 defaulted to
        False on every sparse-True case and tanked recall to 0.0
        (observed 2026-05-05).
      * Workflow framing (description + domain).
      * Tool vocabulary.
      * **(W6 demo loop)** Workflow-specific guidance, when the caller
        passes ``per_workflow_instruction``. Lives in the user message
        rather than the system prompt so each demo cycle's instruction
        update doesn't invalidate Anthropic prompt-cache hits on the
        system prompt across cases — the system prompt stays static,
        the per-call message carries the cycle's addendum.
      * Trajectory through the target step (target event redacted).
      * The decision ask, naming target_step_index + target_label_field.
    """
    redacted = _redact_target_event(trajectory, case.target_label_field)
    trajectory_json = json.dumps(redacted, indent=2, sort_keys=True, default=str)
    tools_block = _format_tools_for_context(spec)
    framing = _metric_framing(metric)
    parts = [
        framing,
        f"## Workflow\nid: `{spec.id}`  ·  domain: `{spec.domain}`",
        f"## Tools (vocabulary only — do not call them)\n{tools_block}",
    ]
    if per_workflow_instruction and per_workflow_instruction.strip():
        parts.append(
            "## Workflow-specific guidance (refined from prior-cycle failures)\n"
            f"{per_workflow_instruction.strip()}"
        )
    payload_guidance = _format_output_payload_guidance(spec)
    if payload_guidance is not None:
        parts.append(payload_guidance)
    parts.append(
        f"## Event trajectory through step {case.target_step_index}\n"
        f"(target event has `{case.target_label_field}` redacted)\n"
        f"```json\n{trajectory_json}\n```"
    )
    decision_lines = [
        "## Decision",
        f"Predict the redacted value of `{case.target_label_field}` at "
        f"step {case.target_step_index}. Call `predict_label` exactly once with:",
        "- `value`: bool",
        "- `rationale`: one short sentence",
    ]
    if payload_guidance is not None:
        decision_lines.append(
            "- `output_payload_json`: JSON-encoded string filling the "
            "keys from the `Operate-tab payload` block above (REQUIRED — "
            "do not omit, do not pass `\"{}\"`)."
        )
    else:
        decision_lines.append(
            "- `output_payload_json`: `\"{}\"` (no Operate primitives "
            "declared for this workflow)."
        )
    parts.append("\n".join(decision_lines))
    return "\n\n".join(parts)


def _build_tool_definition() -> dict[str, Any]:
    """Anthropic tool definition format."""
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": TOOL_INPUT_SCHEMA,
    }


def _build_openai_tool_definition() -> dict[str, Any]:
    """OpenAI tool definition format (for Ollama / LM Studio direct calls)."""
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": TOOL_DESCRIPTION,
            "parameters": TOOL_INPUT_SCHEMA,
        },
    }


_TOOL_DEFINITION = _build_tool_definition()
_OPENAI_TOOL_DEFINITION = _build_openai_tool_definition()


def _decode_payload(raw: Any) -> dict[str, Any] | None:
    """Coerce the agent's `output_payload_json` field into a dict.

    The schema asks for a JSON-encoded string. Anthropic-side
    serialization sometimes returns an object literal (a dict) instead;
    accept either. Anything that doesn't decode to a non-empty dict
    falls back to None so the case row still writes without an
    Operate-tab payload.
    """
    if isinstance(raw, dict):
        return raw if raw else None
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or s == "{}":
        return None
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) and parsed else None


def _validate_prediction_input(
    raw_input: Any, *, case_id: str
) -> None:
    """Validate predict_label tool input. Raises PredictToolValidationError on bad input."""
    if not isinstance(raw_input, dict):
        raise PredictToolValidationError(
            f"case {case_id!r}: predict_label input was not a dict: "
            f"{type(raw_input).__name__}",
            raw_input=raw_input,
        )
    if "value" not in raw_input or "rationale" not in raw_input:
        raise PredictToolValidationError(
            f"case {case_id!r}: predict_label input missing required keys "
            f"(value, rationale); got {sorted(raw_input.keys())}",
            raw_input=raw_input,
        )
    value = raw_input["value"]
    rationale = raw_input["rationale"]
    if not isinstance(value, bool):
        raise PredictToolValidationError(
            f"case {case_id!r}: predict_label `value` must be bool; got "
            f"{type(value).__name__}={value!r}",
            raw_input=raw_input,
        )
    if not isinstance(rationale, str) or not rationale.strip():
        raise PredictToolValidationError(
            f"case {case_id!r}: predict_label `rationale` must be a "
            f"non-empty string; got {rationale!r}",
            raw_input=raw_input,
        )
    # output_payload_json is a JSON-encoded string. Decoded by the
    # extractor; we just sanity-check the type here so a wrong shape
    # doesn't blow up later. Empty / non-string / non-JSON values are
    # tolerated — they fall back to None payload, the iteration still
    # records the bool. Anthropic occasionally returns object types
    # here instead of stringified JSON (rare, but seen in practice);
    # those land as a dict downstream.
    if "output_payload_json" in raw_input:
        v = raw_input["output_payload_json"]
        if v is not None and not isinstance(v, (str, dict)):
            raise PredictToolValidationError(
                f"case {case_id!r}: predict_label `output_payload_json` "
                f"must be a string or object; got {type(v).__name__}",
                raw_input=raw_input,
            )


def _extract_prediction(
    msg: Any, *, case_id: str, model: str
) -> AgentPrediction:
    """Parse Anthropic-format response into AgentPrediction."""
    tool_blocks = [
        b for b in msg.content
        if getattr(b, "type", None) == "tool_use"
        and getattr(b, "name", None) == TOOL_NAME
    ]
    if not tool_blocks:
        text_blocks = [b for b in msg.content if getattr(b, "type", None) == "text"]
        preview = (text_blocks[0].text if text_blocks else "")[:300]
        raise NoPredictToolUseError(
            f"case {case_id!r}: agent did not call {TOOL_NAME} "
            f"(stop_reason={msg.stop_reason!r})",
            stop_reason=msg.stop_reason,
            content_preview=preview,
        )
    raw_input = tool_blocks[0].input
    _validate_prediction_input(raw_input, case_id=case_id)
    payload = _decode_payload(raw_input.get("output_payload_json"))
    return AgentPrediction(
        case_id=case_id,
        value=raw_input["value"],
        rationale=raw_input["rationale"],
        model=model,
        output_payload=payload,
    )


def _extract_prediction_openai(
    response: Any, *, case_id: str, model: str
) -> AgentPrediction:
    """Parse OpenAI-format response into AgentPrediction."""
    if not response.choices:
        raise NoPredictToolUseError(
            f"case {case_id!r}: OpenAI response has no choices",
            stop_reason=None,
            content_preview="",
        )
    choice = response.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    tool_calls = getattr(choice.message, "tool_calls", None) or []
    matching = [tc for tc in tool_calls if tc.function.name == TOOL_NAME]
    if not matching:
        preview = (getattr(choice.message, "content", "") or "")[:300]
        raise NoPredictToolUseError(
            f"case {case_id!r}: agent did not call {TOOL_NAME} "
            f"(stop_reason={finish_reason!r})",
            stop_reason=finish_reason,
            content_preview=preview,
        )
    args = matching[0].function.arguments
    if args is None:
        raise PredictToolValidationError(
            f"case {case_id!r}: predict_label arguments is null",
            raw_input=None,
        )
    try:
        raw_input = json.loads(args)
    except json.JSONDecodeError as exc:
        raise PredictToolValidationError(
            f"case {case_id!r}: predict_label arguments not valid JSON: {exc}",
            raw_input=args,
        ) from exc
    _validate_prediction_input(raw_input, case_id=case_id)
    payload = _decode_payload(raw_input.get("output_payload_json"))
    return AgentPrediction(
        case_id=case_id,
        value=raw_input["value"],
        rationale=raw_input["rationale"],
        model=model,
        output_payload=payload,
    )


async def predict_one(
    client: "AsyncAnthropic",
    case: GeneratedEvalCase,
    *,
    spec: WorkflowSpec,
    metric: MetricDefinition,
    namespace: dict[str, Any],
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    openai_client: "AsyncOpenAI | None" = None,
    budget: "TokenBudget | None" = None,
    per_workflow_instruction: str | None = None,
) -> AgentPrediction:
    """Run one agent prediction for one case.

    When `openai_client` is provided the call goes through its
    `chat.completions.create()` interface instead of Anthropic's
    `/v1/messages`. Pass `AsyncOpenAI` for LM Studio / vLLM, or
    `OllamaChatClient` (from eval_runner.ollama_native) for Ollama
    daemons — the latter routes to /api/chat with options.think=false
    for qwen3-family models (see TODO-25, F14h-hang). Tool definitions
    and response parsing are converted automatically; all other logic
    is identical.

    Args:
        client: AsyncAnthropic client (used when openai_client is None).
        case: The eval case to predict.
        spec: Source WorkflowSpec (for the description + tool vocabulary).
        metric: A4.2 MetricDefinition — drives the gate-metric framing.
        namespace: Pre-exec'd sim namespace.
        model: Model id. Default haiku (Anthropic) or pass a local model id.
        max_tokens: Output cap.
        openai_client: When set, use OpenAI-compat API instead of Anthropic.
        budget: Optional A4.5 token-budget accumulator. Reads
            `input_tokens`/`output_tokens` (Anthropic) or
            `prompt_tokens`/`completion_tokens` (OpenAI-compat).
            If cumulative usage crosses the cap, raises `TokenBudgetExceededError`.
    """
    trajectory = _trajectory_for_case(case, namespace)
    user_message = _format_user_message(
        spec,
        case,
        trajectory,
        metric,
        per_workflow_instruction=per_workflow_instruction,
    )

    try:
        system_prompt = SYSTEM_PROMPT + _maybe_no_think_suffix(model)
        if openai_client is not None:
            response = await openai_client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                tools=[_OPENAI_TOOL_DEFINITION],
                # "required" (not named) for Ollama/LM Studio compat: some
                # local backends don't support the named {"type":"function"}
                # form. With only one tool registered this is equivalent.
                tool_choice="required",
            )
            if budget is not None:
                usage = getattr(response, "usage", None)
                if usage is not None:
                    budget.record(
                        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                        label=case.case_id,
                    )
            return _extract_prediction_openai(
                response, case_id=case.case_id, model=model
            )
        else:
            msg = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                tools=[_TOOL_DEFINITION],
                tool_choice={"type": "tool", "name": TOOL_NAME},
                messages=[{"role": "user", "content": user_message}],
            )
            if budget is not None:
                from .token_budget import extract_usage

                in_tok, out_tok = extract_usage(msg)
                budget.record(
                    input_tokens=in_tok, output_tokens=out_tok, label=case.case_id
                )
            return _extract_prediction(msg, case_id=case.case_id, model=model)
    except (AgentSolverError, NoPredictToolUseError, PredictToolValidationError):
        raise
    except Exception as exc:
        raise AgentSolverError(
            f"case {case.case_id!r}: API call failed — "
            f"{type(exc).__name__}: {exc}"
        ) from exc


async def solve_with_agent(
    client: "AsyncAnthropic",
    case_set: EvalCaseSet,
    plan: SimulationPlan,
    spec: WorkflowSpec,
    metric: MetricDefinition,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int | None = None,
    openai_client: "AsyncOpenAI | None" = None,
    budget: "TokenBudget | None" = None,
    per_workflow_instruction: str | None = None,
) -> list[ReplayResult]:
    """Run the agent on every case in the set; return ReplayResults.

    Output is shaped as `ReplayResult` so the existing `compute_metric`
    pathway scores it byte-identically to `replay_set` — only the source
    of `actual_value` changes (agent prediction vs sim ground truth).

    Cross-checks (case_set.workflow_spec_id, plan.workflow_spec_id,
    metric.workflow_spec_id) mirror `replay_set` + `compute_metric.check_against_spec`.
    The sim is rendered + exec'd once and the namespace is reused
    across all cases.

    Args:
        client: AsyncAnthropic client.
        case_set: Cases to predict.
        plan: SimulationPlan whose trajectory the cases reference.
        spec: WorkflowSpec the plan was rendered against.
        metric: A4.2 MetricDefinition — drives the gate-metric framing
            block injected into every per-case user message.
        model: Anthropic model. Default haiku.
        max_tokens: Output cap per call.
        budget: Optional A4.5 token-budget accumulator shared across
            cases. When the budget tips, the loop aborts at the case
            that crossed the cap (no further `predict_one` calls fire).

    Returns:
        `ReplayResult` per case, in the same order as `case_set.cases`.

    Raises:
        ValueError: case_set / plan / metric workflow_spec_id disagrees
            with `spec.id`.
        AgentSolverError / NoPredictToolUseError / PredictToolValidationError:
            from `predict_one`.
        TokenBudgetExceededError: `budget` was provided and a case's
            usage tipped the cumulative cap.
    """
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

    ns = exec_sim_module(plan, spec, caller="agent-solver")
    resolved_max_tokens = (
        max_tokens
        if max_tokens is not None
        else (DEFAULT_MAX_TOKENS_OPENAI if openai_client is not None else DEFAULT_MAX_TOKENS)
    )

    def _make_kwargs(case: "GeneratedEvalCase") -> dict:
        return dict(
            spec=spec,
            metric=metric,
            namespace=ns,
            model=model,
            max_tokens=resolved_max_tokens,
            openai_client=openai_client,
            budget=budget,
            per_workflow_instruction=per_workflow_instruction,
        )

    if budget is None:
        # No budget to enforce per-case — run all predictions concurrently.
        # Cases are independent API calls; asyncio.gather preserves order.
        predictions = await asyncio.gather(
            *[predict_one(client, case, **_make_kwargs(case)) for case in case_set.cases]
        )
        return [
            ReplayResult(
                case_id=pred.case_id,
                passed=pred.value == case.expected_value,
                actual_value=pred.value,
                expected_value=case.expected_value,
                rationale=pred.rationale,
                output_payload=pred.output_payload,
            )
            for pred, case in zip(predictions, case_set.cases)
        ]

    # Budget path: sequential so we can abort as soon as the cap is hit.
    results: list[ReplayResult] = []
    for case in case_set.cases:
        prediction = await predict_one(client, case, **_make_kwargs(case))
        results.append(
            ReplayResult(
                case_id=case.case_id,
                passed=prediction.value == case.expected_value,
                actual_value=prediction.value,
                expected_value=case.expected_value,
                rationale=prediction.rationale,
                output_payload=prediction.output_payload,
            )
        )
    return results


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MAX_TOKENS_OPENAI",
    "REDACTED_TOKEN",
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "TOOL_INPUT_SCHEMA",
    "SYSTEM_PROMPT",
    "AgentSolverError",
    "NoPredictToolUseError",
    "PredictToolValidationError",
    "AgentPrediction",
    "predict_one",
    "solve_with_agent",
]
