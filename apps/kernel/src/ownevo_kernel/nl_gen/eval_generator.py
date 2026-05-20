"""WorkflowSpec + SimulationPlan → EvalCaseSet via Anthropic tool-use (A4.1).

Mirrors `sim_generator.generate_simulation_plan` (A3.2):
single-turn `messages.create` with a forced `tool_choice`, the tool's
`input_schema` is `EvalCaseSet.model_json_schema()` wrapped under an
`eval_case_set` key, and the tool input is unwrapped + validated back
into an `EvalCaseSet`.

Why pinning eval cases against `(seed, step_index, label_field)` triples
rather than emitting full input/output blobs:

  * **Determinism** — the rendered sim is byte-identical at a given seed,
    so a case scoped to `(seed, n_steps, step_index)` reproduces an
    identical event every replay. The LLM never has to emit the event
    payload itself; it only emits the targeting + expected label.
  * **Auditability** — eval cases survive in the DB as small JSON blobs;
    the audit trail shows what the case asserts (the hidden label at a
    specific event), not a paraphrase that might drift from the sim.
  * **Train/test discipline** — `is_test_fold` is a per-case flag the
    gate runner respects without further interpretation.

Lives in the `agent` extra (same as A3.1 / A3.2) — anthropic is not a
kernel-runtime dep.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ._validation_retry import (
    DEFAULT_MAX_RETRIES,
    NoToolUseSignal,
    RetryExhaustedError,
    call_with_validation_retry,
    truncate_for_error,
)
from .eval_case_set import SCHEMA_VERSION, EvalCaseSet
from .sim_plan import SimulationPlan
from .spec import WorkflowSpec
from .workflow_spec_generator import NLGenError

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


DEFAULT_MODEL = os.environ.get("OWNEVO_NL_GEN_MODEL") or "claude-opus-4-7"
DEFAULT_MAX_TOKENS = 12_000

TOOL_NAME = "emit_eval_case_set"
TOOL_DESCRIPTION = (
    "Emit a structured EvalCaseSet of 10-30 deterministic test cases for "
    "the workflow. Each case pins one event in the simulator's trajectory "
    "and asserts what its hidden ground-truth label should be. Call this "
    "tool exactly once with the complete case set."
)


SYSTEM_PROMPT = (
    "You are ownEvo's NL-gen eval-case generator. You receive a "
    "WorkflowSpec describing the workflow plus the SimulationPlan whose "
    "rendered trajectory the cases will replay against. You produce an "
    "EvalCaseSet — 10-30 test cases the regression gate uses to decide "
    "whether a proposed skill change is an improvement or a regression.\n\n"
    "Each case targets ONE event in the trajectory. The replay helper "
    "runs `run_simulation(sim_seed, n_steps)` and reads "
    "`trajectory[target_step_index][target_label_field]` — pass if it "
    "equals `expected_value`, fail otherwise.\n\n"
    "Rules:\n"
    "1. `target_label_field` MUST be the name of one of the SimulationPlan's "
    "`event_fields` whose `type` is `bool`. These are the hidden ground-truth "
    "keys the sim emits at every step (e.g. `alert_correct_label`, "
    "`default_label`, `is_problematic`). If the SimulationPlan exposes "
    "multiple bool labels, you may use any of them; pick the one most "
    "relevant to each case's rationale.\n"
    "2. `sim_seed` MUST be one of the seeds you actually want to replay "
    "with. Default to the SimulationPlan's `seed_default`, but use 2-3 "
    "alternate seeds across the suite so a deterministic seed flake "
    "doesn't dominate the cases.\n"
    "3. `n_steps` MUST be high enough that `target_step_index` falls inside "
    "the trajectory. Use the SimulationPlan's `n_steps_default` unless a "
    "case genuinely needs more steps to surface the targeted event; do not "
    "exceed 2x the default.\n"
    "4. `target_step_index` MUST be < `n_steps`. Distribute step indices "
    "across the trajectory — early, middle, late events all surface "
    "different physics in a seasonal sim.\n"
    "5. Both `expected_value` classes MUST be represented at least 3 times "
    "across the suite. A one-class suite lets a 'the agent always says X' "
    "skill silently pass the gate.\n"
    "6. For EVERY phrase in `WorkflowSpec.known_past_misses`, emit at least "
    "one case whose `provenance.kind=\"derived\"` and whose "
    "`provenance.source` is that verbatim phrase. These are the user's "
    "highest-value test asks — they MUST be covered.\n"
    "7. For cases not derived from a past-miss phrase, use "
    "`provenance.kind=\"inferred\"` with `provenance.source` set to a "
    "named domain pattern (e.g., `\"supply chain seasonal markdown "
    "pattern\"`, `\"credit-risk DTI threshold pattern\"`).\n"
    "8. Mark ~20% of cases (round to at least 2) `is_test_fold=true`. "
    "These are held-out for evaluation; the gate refuses to use them for "
    "training. Spread held-out cases across both expected_value classes.\n"
    "9. `case_id` MUST be kebab-case and unique within the set. Make it "
    "descriptive (e.g., `markdown-alert-fired-correctly-week-49`, not "
    "`case-1`).\n"
    "10. `rationale` is one line of plain English explaining what makes "
    "this case interesting — quote the past-miss phrase or name the "
    "domain pattern. Surfaces in the audit trail.\n"
    f"11. Set `schema_version` to {SCHEMA_VERSION!r}. Set both "
    "`workflow_spec_id` and `simulation_plan_workflow_id` to the "
    "WorkflowSpec's id verbatim.\n"
    "12. Emit between 10 and 30 cases. Suites smaller than 10 give the "
    "gate too little signal; suites larger than 30 cap replay cost when "
    "the loop turns over many sim variants."
)


class EvalCaseSetValidationError(NLGenError):
    """Claude returned a tool input that failed EvalCaseSet validation."""

    def __init__(
        self, message: str, *, raw_input: Any, pydantic_error: ValidationError
    ):
        super().__init__(message)
        self.raw_input = raw_input
        self.pydantic_error = pydantic_error


class NoEvalToolUseError(NLGenError):
    """Claude responded without calling the emit_eval_case_set tool."""

    def __init__(
        self, message: str, *, stop_reason: str | None, content_preview: str
    ):
        super().__init__(message)
        self.stop_reason = stop_reason
        self.content_preview = content_preview


def _build_tool_definition() -> dict[str, Any]:
    """Anthropic tool definition.

    Same `{"eval_case_set": <EvalCaseSet>}` wrapping pattern as A3.1/A3.2 —
    small models nest deep object outputs under an outer field even when
    the schema is flat. `$defs` are hoisted to the input_schema root so
    `$ref` pointers resolve correctly.
    """
    set_schema = EvalCaseSet.model_json_schema()
    defs = set_schema.pop("$defs", {})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"eval_case_set": set_schema},
        "required": ["eval_case_set"],
    }
    if defs:
        input_schema["$defs"] = defs
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": input_schema,
    }


_TOOL_DEFINITION: dict[str, Any] = _build_tool_definition()
"""Computed once at import time — EvalCaseSet schema is static."""


def _format_user_message(
    workflow_spec: WorkflowSpec, simulation_plan: SimulationPlan
) -> str:
    """Render the WorkflowSpec + SimulationPlan as the user message.

    Both artifacts are sent as JSON (Pydantic's `model_dump_json`) — the
    same pattern as `sim_generator._format_user_message`. Reproducible
    structure beats paraphrased prose.
    """
    spec_payload = workflow_spec.model_dump_json(indent=2)
    plan_payload = simulation_plan.model_dump_json(indent=2)
    return (
        "Here is the WorkflowSpec and SimulationPlan to generate eval cases "
        "for. Read the spec's `known_past_misses` (each phrase MUST get its "
        "own case) and the plan's `event_fields` (the bool-typed fields are "
        "the hidden ground-truth labels you'll target).\n\n"
        f"WorkflowSpec:\n```json\n{spec_payload}\n```\n\n"
        f"SimulationPlan:\n```json\n{plan_payload}\n```"
    )


def _normalize_payload(payload: Any) -> Any:
    """Force-overwrite `schema_version` to the canonical literal.
    See `workflow_spec_generator._normalize_payload` for rationale."""
    if isinstance(payload, dict) and "schema_version" in payload:
        return {**payload, "schema_version": SCHEMA_VERSION}
    return payload


_RETRY_FEEDBACK = (
    "Reminders:\n"
    "- `workflow_spec_id` and `simulation_plan_id` MUST match the source "
    "artifacts exactly.\n"
    "- Each case's hidden-state fields must reference fields actually "
    "produced by the simulation_plan.\n"
    "- Each case's expected output must match the workflow_spec's tool "
    "output schema.\n"
    "- Don't invent extra top-level fields; pydantic rejects unknown keys."
)


async def generate_eval_case_set(
    client: AsyncAnthropic,
    workflow_spec: WorkflowSpec,
    simulation_plan: SimulationPlan,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    design_brief_block: str | None = None,
) -> EvalCaseSet:
    """Generate a typed EvalCaseSet from a WorkflowSpec + SimulationPlan.

    Args:
        client: An AsyncAnthropic client (any /v1/messages-compatible endpoint).
        workflow_spec: The A3.1 artifact this case set is for.
        simulation_plan: The A3.2 artifact whose trajectory cases replay
            against. Its `workflow_spec_id` must match `workflow_spec.id`.
        model: Anthropic model id. Default opus 4.7.
        max_tokens: Output cap. Default 12k — case sets are larger than
            sim plans (10-30 structured cases each).
        max_retries: On `ValidationError`, retry up to this many times
            with the pydantic error fed back as a `tool_result`. Default 4 (= 5 attempts total).

    Returns:
        A validated `EvalCaseSet`. Persist via
        `eval_persistence.persist_eval_case_set`.

    Raises:
        ValueError: simulation_plan.workflow_spec_id != workflow_spec.id.
        NoEvalToolUseError: Claude stopped without calling the tool.
        EvalCaseSetValidationError: All attempts produced inputs that
            failed `EvalCaseSet.model_validate`.
    """
    if simulation_plan.workflow_spec_id != workflow_spec.id:
        raise ValueError(
            f"simulation_plan.workflow_spec_id="
            f"{simulation_plan.workflow_spec_id!r} does not match "
            f"workflow_spec.id={workflow_spec.id!r}"
        )

    user_message = _format_user_message(workflow_spec, simulation_plan)
    if design_brief_block:
        user_message = f"{user_message}\n\n{design_brief_block}"
    try:
        case_set, _raw = await call_with_validation_retry(
            client=client,
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            tool_definition=_TOOL_DEFINITION,
            tool_name=TOOL_NAME,
            initial_user_message=user_message,
            schema_class=EvalCaseSet,
            envelope_key="eval_case_set",
            max_retries=max_retries,
            extra_feedback=_RETRY_FEEDBACK,
            normalize=_normalize_payload,
        )
        return case_set
    except NoToolUseSignal as exc:
        raise NoEvalToolUseError(
            f"Model {model} did not call {TOOL_NAME} (stop_reason={exc.stop_reason!r})",
            stop_reason=exc.stop_reason,
            content_preview=exc.content_preview,
        ) from exc
    except RetryExhaustedError as exc:
        preview = truncate_for_error(exc.raw_input)
        raise EvalCaseSetValidationError(
            f"Tool input failed EvalCaseSet validation after {exc.attempts} "
            f"attempts: {exc.pydantic_error.error_count()} errors. "
            f"Input preview: {preview}",
            raw_input=exc.raw_input,
            pydantic_error=exc.pydantic_error,
        ) from exc


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "SYSTEM_PROMPT",
    "NoEvalToolUseError",
    "EvalCaseSetValidationError",
    "generate_eval_case_set",
]
