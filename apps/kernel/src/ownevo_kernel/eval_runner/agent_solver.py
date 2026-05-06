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

import json
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


DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 1_000
"""Bool + one-line rationale fits in <500 tokens; 1k is the cap."""
DEFAULT_MAX_TOKENS_OPENAI = 8_000
"""Higher cap for OpenAI-compat path: reasoning/thinking models emit a
long preamble before committing the tool call; 1k hits the wall too early."""

REDACTED_TOKEN = "<REDACTED>"
"""Sentinel value substituted into the target event's label field."""

TOOL_NAME = "predict_label"
TOOL_DESCRIPTION = (
    "Emit a single bool prediction for the redacted label field at the "
    "target step, plus a one-line plain-English rationale. Call this "
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
    },
    "required": ["value", "rationale"],
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
    "1. Call `predict_label` exactly once with `value: bool` and "
    "`rationale: str` (one short sentence).\n"
    "2. Use the past trajectory + the visible state at the target step. "
    "The hidden label was computed deterministically from earlier state, "
    "so there IS a learnable rule — your job is to find it.\n"
    "3. Do not invent fields the trajectory doesn't show. The trajectory "
    "is the entire context; the workflow description is framing.\n"
    "4. Read the gate-metric framing block and let it shape your tie-"
    "breaker on borderline cases. A `recall`-gated workflow penalizes "
    "False Negatives heavily — predict True under uncertainty when the "
    "evidence even moderately supports it. A `precision`-gated workflow "
    "penalizes False Positives — require strong evidence before True. "
    "`balanced_accuracy` and `f1` weight both errors; calibrate without "
    "a default lean. The metric is given to you per call; use it."
)


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
    cross-run comparability.
    """

    case_id: str
    value: bool
    rationale: str
    model: str




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


def _format_user_message(
    spec: WorkflowSpec,
    case: GeneratedEvalCase,
    trajectory: list[dict[str, Any]],
    metric: MetricDefinition,
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
      * Trajectory through the target step (target event redacted).
      * The decision ask, naming target_step_index + target_label_field.
    """
    redacted = _redact_target_event(trajectory, case.target_label_field)
    trajectory_json = json.dumps(redacted, indent=2, sort_keys=True, default=str)
    tools_block = _format_tools_for_context(spec)
    framing = _metric_framing(metric)
    return (
        f"{framing}\n\n"
        f"## Workflow\n"
        f"id: `{spec.id}`  ·  domain: `{spec.domain}`\n\n"
        f"## Tools (vocabulary only — do not call them)\n"
        f"{tools_block}\n\n"
        f"## Event trajectory through step {case.target_step_index}\n"
        f"(target event has `{case.target_label_field}` redacted)\n"
        f"```json\n{trajectory_json}\n```\n\n"
        f"## Decision\n"
        f"Predict the redacted value of `{case.target_label_field}` at "
        f"step {case.target_step_index}. Call `predict_label` exactly once."
    )


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
    return AgentPrediction(
        case_id=case_id,
        value=raw_input["value"],
        rationale=raw_input["rationale"],
        model=model,
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
    return AgentPrediction(
        case_id=case_id,
        value=raw_input["value"],
        rationale=raw_input["rationale"],
        model=model,
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
) -> AgentPrediction:
    """Run one agent prediction for one case.

    When `openai_client` is provided the call goes through the OpenAI
    `/v1/chat/completions` path (Ollama / LM Studio direct) instead of
    the Anthropic `/v1/messages` path. Tool definitions and response
    parsing are converted automatically; all other logic is identical.

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
    user_message = _format_user_message(spec, case, trajectory, metric)

    try:
        if openai_client is not None:
            response = await openai_client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
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
                system=SYSTEM_PROMPT,
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

    results: list[ReplayResult] = []
    for case in case_set.cases:
        prediction = await predict_one(
            client,
            case,
            spec=spec,
            metric=metric,
            namespace=ns,
            model=model,
            max_tokens=resolved_max_tokens,
            openai_client=openai_client,
            budget=budget,
        )
        results.append(
            ReplayResult(
                case_id=case.case_id,
                passed=prediction.value == case.expected_value,
                actual_value=prediction.value,
                expected_value=case.expected_value,
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
