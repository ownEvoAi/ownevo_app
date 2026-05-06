"""LLM-as-judge: bundle → MetaEvalJudgment via Anthropic tool-use (A4.6).

Mirrors `metric_generator.generate_metric_definition`:
single-turn `messages.create` with a forced `tool_choice`, the tool's
`input_schema` is `MetaEvalJudgment.model_json_schema()` wrapped under
a `judgment` key, and the tool input is unwrapped + validated back
into a `MetaEvalJudgment`.

What the judge sees in the user message:

  1. The plain-English `description` (the original NL input).
  2. The `WorkflowSpec` JSON (entities + past-misses + success criterion).
  3. The `SimulationPlan` JSON (agents + steps + body — code body
     truncated to 4kB to keep the prompt bounded; the judge doesn't
     need to read the simulator's full body to decide whether it
     instantiates the description's entities).
  4. The `EvalCaseSet` JSON (10-30 cases with rationales).
  5. The `MetricDefinition` JSON (family + target + bounds).

Why these are sent as JSON, not rendered prose: deterministic — same
input → same prompt; the judge sees the same shape the audit trail
+ the runtime gate see; the prompt cost is bounded by the artifacts'
schemas, not by a free-form narrative.

The judge does NOT have access to:

  * The replay results (`actual_value`s) — those depend on the agent,
    not the bundle, and confound the meta-eval signal.
  * Live data — meta-eval is a static-artifact judgment.

Lives in the `agent` extra (anthropic dep). The judge eval set
(`META_EVAL_SET`) and runner are kernel-runtime; only the judge
itself is gated on the agent extra.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..eval_case_set import EvalCaseSet
from ..metric_def import MetricDefinition
from ..sim_plan import SimulationPlan
from ..spec import WorkflowSpec
from ..workflow_spec_generator import NLGenError
from .judgment import SCHEMA_VERSION, MetaEvalJudgment

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


DEFAULT_MODEL = "claude-opus-4-7"
"""Opus 4.7 by default — the meta-eval judge is the calibration anchor
for the W5 ≥0.7 agreement gate, so we use the strongest model for the
ground-truth runs. The runner accepts a `model=` override so cheaper
sub-runs (haiku for triage, sonnet for spot-checks) are easy."""

DEFAULT_MAX_TOKENS = 6_000
"""Wider than the metric generator's 4k — the judge writes three
per-dimension rationales + an overall_rationale, which can run a
few hundred chars each on edge cases."""


SIM_BODY_PREVIEW_CHARS = 4_000
"""Truncate `SimulationPlan.step_code` to this many chars before sending
to the judge. `step_code` is the long-tail field in the plan (init_state
is typically short); truncating only `step_code` keeps the rest of the
plan intact. The judge needs to see *what* the sim does (mostly
captured by description / event_fields / imports / init_state_code) —
the full step body contributes little to the three-dimension judgment
but blows up prompt cost on long sim bodies. Truncation is suffixed
with a marker so the judge knows it's a preview."""


TOOL_NAME = "emit_meta_eval_judgment"
TOOL_DESCRIPTION = (
    "Emit a structured MetaEvalJudgment for the given (description, "
    "workflow_spec, simulation_plan, eval_case_set, metric_definition) "
    "bundle. Score three orthogonal dimensions (sim_coverage, "
    "eval_case_coverage, metric_alignment) with pass/partial/fail + a "
    "rationale, then call an overall good/bad verdict. Call this tool "
    "exactly once."
)


SYSTEM_PROMPT = (
    "You are ownEvo's NL-gen meta-eval judge. You receive an original "
    "plain-English workflow description and the four NL-gen artifacts "
    "generated from it (WorkflowSpec, SimulationPlan, EvalCaseSet, "
    "MetricDefinition). Your job is to score the bundle on three "
    "orthogonal dimensions and emit a binary overall verdict.\n\n"
    "Dimensions:\n\n"
    "1. `sim_coverage` — Does the SimulationPlan instantiate every "
    "entity, condition, and objective the description mentioned?\n"
    "   - `pass`: every description-named entity has a counterpart in "
    "the sim's agents / data_sources / personas / env_generators. The "
    "sim is in the same domain.\n"
    "   - `partial`: a minor entity is missing or named differently, "
    "but the load-bearing pieces are present.\n"
    "   - `fail`: a load-bearing entity is absent, OR the sim is in a "
    "different domain entirely (e.g. description is about retail "
    "demand and the sim simulates fraud detection).\n\n"
    "2. `eval_case_coverage` — Do the eval cases cover the described "
    "behaviors?\n"
    "   - `pass`: every documented past-miss in the WorkflowSpec's "
    "`known_past_misses` has at least one eval case exercising it, "
    "AND the True/False class balance reflects the description's "
    "framing (e.g. a recall-focused workflow has enough True cases).\n"
    "   - `partial`: past-miss coverage is partial (some misses "
    "exercised, others not), OR class balance is skewed but workable.\n"
    "   - `fail`: cases are off-topic (test a different decision than "
    "the workflow's), OR don't exercise the workflow's key decision "
    "at all, OR the rationales contradict the expected_value labels.\n\n"
    "3. `metric_alignment` — Is the metric bounded and aligned with "
    "the description?\n"
    "   - `pass`: family matches the past-miss asymmetry (recall when "
    "the past-miss is missing positives, precision when it's false "
    "alerts, balanced_accuracy when there's class imbalance, f1 for "
    "balanced asymmetry, specificity when negatives are costly), AND "
    "the target threshold is achievable + non-trivial.\n"
    "   - `partial`: family is defensible but suboptimal (e.g. f1 "
    "where recall would be sharper), OR the threshold is overly "
    "aggressive (>0.9) or too lax (<0.4).\n"
    "   - `fail`: family contradicts the past-miss framing (e.g. "
    "precision when the past-miss is 'we missed the spike'), OR "
    "the threshold is 1.0 (unreachable) or 0.0 (trivially passable), "
    "OR the direction contradicts the success criterion.\n\n"
    "Overall verdict:\n"
    "- `good` — safe to feed to the agent loop. All three dimensions "
    "pass, OR a `partial` is clearly benign.\n"
    "- `bad` — at least one dimension fails badly enough that the "
    "agent loop's outcomes won't be interpretable.\n"
    "Do NOT mechanically derive overall from dimension counts — your "
    "calibration on borderline (pass, pass, partial) cases is what "
    "the W5 agreement gate measures.\n\n"
    "Rules:\n"
    "1. Quote the description's verbatim phrases in rationales where "
    "possible — this is what makes the judgment auditable.\n"
    "2. Each rationale is one line (≤600 chars). overall_rationale "
    "ties the overall verdict to the per-dimension verdicts (≤800 "
    "chars).\n"
    f"3. Set the top-level `schema_version` field to {SCHEMA_VERSION!r}. "
    "Do NOT add a `schema_version` field to the dimension sub-objects — "
    "they only carry `verdict` and `rationale`. Set `workflow_spec_id` "
    "to the WorkflowSpec's id verbatim.\n"
    "4. Be calibrated: not every bundle deserves `good` and not every "
    "imperfect one is `bad`. The eval set is balanced; if you call "
    "everything `good` or everything `bad`, your judgments are "
    "useless to the gate."
)


class MetaEvalJudgmentValidationError(NLGenError):
    """Claude returned a tool input that failed MetaEvalJudgment validation."""

    def __init__(
        self,
        message: str,
        *,
        raw_input: Any,
        pydantic_error: ValidationError,
    ) -> None:
        super().__init__(message)
        self.raw_input = raw_input
        self.pydantic_error = pydantic_error


class NoMetaEvalToolUseError(NLGenError):
    """Claude responded without calling the emit_meta_eval_judgment tool."""

    def __init__(
        self,
        message: str,
        *,
        stop_reason: str | None,
        content_preview: str,
    ) -> None:
        super().__init__(message)
        self.stop_reason = stop_reason
        self.content_preview = content_preview


class MetaEvalSpecIdMismatchError(NLGenError):
    """Generated judgment's workflow_spec_id doesn't match the input bundle.

    The judge is supposed to copy the spec id verbatim. If it picks a
    different id, downstream audit-log joins break silently — surface
    it as a typed error rather than letting it slip through.
    """

    def __init__(
        self,
        message: str,
        *,
        judgment: MetaEvalJudgment,
        expected_id: str,
    ) -> None:
        super().__init__(message)
        self.judgment = judgment
        self.expected_id = expected_id


def _build_tool_definition() -> dict[str, Any]:
    """Anthropic tool definition.

    Same `{"judgment": <MetaEvalJudgment>}` wrapping pattern as
    A3.1/A3.2/A4.1/A4.2 — small models nest deep object outputs under
    an outer field even when the schema is flat. `$defs` are hoisted
    to the input_schema root so `$ref` pointers resolve correctly.
    """
    j_schema = MetaEvalJudgment.model_json_schema()
    defs = j_schema.pop("$defs", {})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"judgment": j_schema},
        "required": ["judgment"],
    }
    if defs:
        input_schema["$defs"] = defs
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": input_schema,
    }


_TOOL_DEFINITION: dict[str, Any] = _build_tool_definition()


def _truncate_sim_body(plan: SimulationPlan) -> dict[str, Any]:
    """Dump the SimulationPlan with `step_code` clipped to SIM_BODY_PREVIEW_CHARS.

    Returns a dict, not a SimulationPlan — the schema enforces minimum
    lengths but no maximum, and we want to bypass schema re-validation
    on the truncated payload anyway (the truncation marker comment is
    safe in a JSON string but isn't valid Python). `init_state_code`
    is left intact: empirically short, and dropping it costs the judge
    context about the simulator's seed setup.
    """
    payload = plan.model_dump(mode="json")
    step_code = payload.get("step_code", "")
    if isinstance(step_code, str) and len(step_code) > SIM_BODY_PREVIEW_CHARS:
        clipped = step_code[:SIM_BODY_PREVIEW_CHARS]
        payload["step_code"] = (
            f"{clipped}\n\n# … truncated for judge prompt "
            f"({len(step_code) - SIM_BODY_PREVIEW_CHARS} more chars) …"
        )
    return payload


def _format_user_message(
    description: str,
    spec: WorkflowSpec,
    plan: SimulationPlan,
    case_set: EvalCaseSet,
    metric: MetricDefinition,
) -> str:
    """Assemble the judge's user message.

    All artifacts go in as JSON (sorted keys via Pydantic's default
    dump). The description goes in verbatim — the judge needs it as
    written, not normalized."""
    spec_payload = spec.model_dump_json(indent=2)
    plan_payload = json.dumps(_truncate_sim_body(plan), indent=2, sort_keys=False)
    case_payload = case_set.model_dump_json(indent=2)
    metric_payload = metric.model_dump_json(indent=2)
    return (
        "Here is the bundle to score. Quote the description's verbatim "
        "phrasing in your rationales where possible.\n\n"
        f"Original description:\n```\n{description}\n```\n\n"
        f"WorkflowSpec:\n```json\n{spec_payload}\n```\n\n"
        f"SimulationPlan:\n```json\n{plan_payload}\n```\n\n"
        f"EvalCaseSet:\n```json\n{case_payload}\n```\n\n"
        f"MetricDefinition:\n```json\n{metric_payload}\n```"
    )


async def judge_artifacts(
    client: AsyncAnthropic,
    description: str,
    spec: WorkflowSpec,
    plan: SimulationPlan,
    case_set: EvalCaseSet,
    metric: MetricDefinition,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> MetaEvalJudgment:
    """Judge one (description, bundle) pair and return the verdict.

    Args:
        client: An AsyncAnthropic client (any /v1/messages-compatible endpoint).
        description: The plain-English NL input that drove the
            generation. Sent verbatim.
        spec: The A3.1 WorkflowSpec.
        plan: The A3.2 SimulationPlan. `body` is truncated to
            SIM_BODY_PREVIEW_CHARS in the prompt.
        case_set: The A4.1 EvalCaseSet.
        metric: The A4.2 MetricDefinition.
        model: Anthropic model id. Default opus 4.7 (calibration anchor).
        max_tokens: Output cap. Default 6k.

    Returns:
        A validated `MetaEvalJudgment`.

    Raises:
        NoMetaEvalToolUseError: Claude stopped without calling the tool.
        MetaEvalJudgmentValidationError: Claude called the tool but the
            input failed `MetaEvalJudgment.model_validate`.
        MetaEvalSpecIdMismatchError: The generated judgment's
            workflow_spec_id doesn't match `spec.id`.
    """
    msg = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        tools=[_TOOL_DEFINITION],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[
            {
                "role": "user",
                "content": _format_user_message(
                    description, spec, plan, case_set, metric
                ),
            }
        ],
    )

    tool_blocks = [
        b for b in msg.content
        if getattr(b, "type", None) == "tool_use"
        and getattr(b, "name", None) == TOOL_NAME
    ]
    if not tool_blocks:
        text_blocks = [b for b in msg.content if getattr(b, "type", None) == "text"]
        preview = (text_blocks[0].text if text_blocks else "")[:300]
        raise NoMetaEvalToolUseError(
            f"Model {model} did not call {TOOL_NAME} (stop_reason={msg.stop_reason!r})",
            stop_reason=msg.stop_reason,
            content_preview=preview,
        )

    raw_input = tool_blocks[0].input
    if (
        isinstance(raw_input, dict)
        and "judgment" in raw_input
        and len(raw_input) == 1
    ):
        payload = raw_input["judgment"]
    else:
        payload = raw_input

    # Defensive parsing #1: opus 4.7 sometimes returns the wrapped
    # value as a JSON-encoded string instead of a dict (observed in
    # the A4.6 live smoke 2026-05-06). Try one round of JSON-decoding
    # if we see a string where a dict was expected; non-JSON strings
    # fall through to the normal validation path which raises the
    # typed error.
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            pass  # let model_validate raise the typed error below

    # Defensive parsing #2: opus 4.7 sometimes propagates the top-level
    # `schema_version` field into each dimension sub-object (observed in
    # the A4.6 live smoke). `MetaEvalDimension` is `extra='forbid'` so
    # this would otherwise fail validation. Strip the spurious key only
    # — every other unexpected key still fails loudly so a real schema
    # regression doesn't slip through.
    if isinstance(payload, dict):
        for dim_key in ("sim_coverage", "eval_case_coverage", "metric_alignment"):
            dim_val = payload.get(dim_key)
            if isinstance(dim_val, dict) and "schema_version" in dim_val:
                dim_val.pop("schema_version", None)

    try:
        judgment = MetaEvalJudgment.model_validate(payload)
    except ValidationError as exc:
        try:
            preview = json.dumps(raw_input)[:500]
        except (TypeError, ValueError):
            preview = repr(raw_input)[:500]
        raise MetaEvalJudgmentValidationError(
            f"Tool input failed MetaEvalJudgment validation: {exc.error_count()} "
            f"errors. Input preview: {preview}",
            raw_input=raw_input,
            pydantic_error=exc,
        ) from exc

    if judgment.workflow_spec_id != spec.id:
        raise MetaEvalSpecIdMismatchError(
            f"judgment.workflow_spec_id={judgment.workflow_spec_id!r} does not "
            f"match spec.id={spec.id!r} — judge copied the wrong id; downstream "
            f"audit joins would break silently",
            judgment=judgment,
            expected_id=spec.id,
        )

    return judgment


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "SIM_BODY_PREVIEW_CHARS",
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "SYSTEM_PROMPT",
    "MetaEvalJudgmentValidationError",
    "NoMetaEvalToolUseError",
    "MetaEvalSpecIdMismatchError",
    "judge_artifacts",
]
