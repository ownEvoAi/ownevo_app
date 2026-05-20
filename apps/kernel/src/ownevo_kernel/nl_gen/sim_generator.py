"""WorkflowSpec → SimulationPlan via Anthropic tool-use (A3.2).

Mirrors `workflow_spec_generator.generate_workflow_spec` (A3.1):
single-turn `messages.create` with a forced `tool_choice`, the tool's
`input_schema` is `SimulationPlan.model_json_schema()` wrapped under a
`plan` key, and the tool input is unwrapped + validated back into a
`SimulationPlan`.

Why a separate plan + render path rather than asking the LLM to emit the
full Python module:

  * **Determinism by construction** — the renderer wires `random.Random(seed)`
    as the only RNG. The LLM cannot skip this or reach for `time.time()`,
    `uuid.uuid4()`, etc.
  * **Auditability** — the plan is JSON; what changed across versions is a
    diff over structured fields, not over freeform Python.
  * **Safety** — `sim_render._ast_safety_check` rejects forbidden imports,
    forbidden builtins, and dunder access. Trusting the LLM to emit safe
    Python is a separate, larger problem we don't need to solve here.

The plan path also leaves room for re-rendering the same plan against an
updated workflow spec without re-querying the LLM (cheap iteration during
W4-W6 when the loop turns over many sim variants).

Lives in the `agent` extra (same as A3.1) — anthropic is not a kernel-runtime
dep.
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
from .sim_plan import ALLOWED_IMPORTS, SCHEMA_VERSION, SimulationPlan
from .spec import WorkflowSpec
from .workflow_spec_generator import NLGenError

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


DEFAULT_MODEL = os.environ.get("OWNEVO_NL_GEN_MODEL") or "claude-opus-4-7"
DEFAULT_MAX_TOKENS = 8_000

TOOL_NAME = "emit_simulation_plan"
TOOL_DESCRIPTION = (
    "Emit a structured SimulationPlan for the workflow spec. The plan is "
    "rendered into a deterministic Python simulator that produces a labeled "
    "trajectory the agent loop evaluates against. Call this tool exactly "
    "once with the complete plan."
)


SYSTEM_PROMPT = (
    "You are ownEvo's NL-gen simulator-plan generator. You receive a "
    "structured WorkflowSpec describing a workflow the customer wants their "
    "agent to run on. You produce a SimulationPlan whose function bodies, "
    "when rendered, generate a deterministic trajectory of synthetic events "
    "the agent will be evaluated against.\n\n"
    "Rules:\n"
    "1. The simulator is DETERMINISTIC. Use only `rng` (a `random.Random` "
    "instance the renderer passes you) and `step_index` (a 0-based int) for "
    "any sample. Do not import `random` yourself; use the passed `rng` "
    "(`rng.random()`, `rng.choice(...)`, `rng.gauss(mu, sigma)`, "
    "`rng.randint(a, b)`, etc.). Do not call `time.time()`, `datetime.now()`, "
    "`uuid.uuid4()`, or anything else that introduces non-determinism.\n"
    f"2. `imports` MUST be a subset of {sorted(ALLOWED_IMPORTS)}. Default to "
    "the empty list unless `math` (sin/cos for seasonality) or `statistics` "
    "(mean/stdev) is genuinely needed.\n"
    "3. `init_state_code` is the body of `def init_state(rng):` — it must "
    "`return` the state dict. Use it to seed entity catalogs (e.g. a list of "
    "10-20 SKUs with base demand levels, or 50 synthetic loan applications "
    "with hidden default labels). Do NOT generate the full trajectory here; "
    "that's `step`'s job.\n"
    "4. `step_code` is the body of `def step(rng, state, step_index):` — it "
    "must `return` an event dict whose keys are exactly the names in "
    "`event_fields`. Each event corresponds to one observation the agent will "
    "see (one weekly demand reading, one loan application, one contract "
    "clause). Include a hidden ground-truth label field that the eval can "
    "score against (e.g. `default_label`, `is_problematic`, "
    "`alert_correct_label`).\n"
    "5. `event_fields` declares the shape of the dict `step` returns. Every "
    "key your `step_code` writes must appear here; otherwise the renderer's "
    "shape check rejects the trajectory at runtime.\n"
    "6. `n_steps_default` should match the cadence implied by the workflow's "
    "personas — a daily-review workflow at ~30 events, a weekly review at "
    "~52, a per-incident workflow at ~100. Cap your default at 1000.\n"
    "7. NO imports inside the function bodies. NO `eval`, `exec`, `compile`, "
    "`open`, `__import__`, `globals`, `locals`. NO dunder attribute access "
    "(`x.__class__`, `x.__bases__`). The renderer's AST safety pass rejects "
    "these — generate clean code that doesn't trip them.\n"
    f"8. Set `schema_version` to {SCHEMA_VERSION!r}. Set `workflow_spec_id` to "
    "the workflow spec's id verbatim. Provide a one-line `description` of "
    "what the simulator generates.\n"
    "9. Make the simulator NON-TRIVIAL. The workflow's `environment.entities` "
    "and `seasonality` and `known_past_misses` should shape what the sim "
    "produces — e.g. for demand-prediction with weekly + holiday seasonality, "
    "your `step` should modulate base demand by a sinusoid keyed off "
    "`step_index`, and at least some events should match the past-miss "
    "patterns so eval cases can be seeded from them later.\n"
    "10. **`event_fields[].type` MUST be one of these 6 Python type names:** "
    "`int`, `float`, `str`, `bool`, `list`, `dict`. These are Python type "
    "names, NOT JSON-Schema names. Do NOT use `string` (use `str`), "
    "`integer`, `boolean`, `array`, `object`, `number`, or any JSON-Schema "
    "vocabulary. Note: this differs from WorkflowSpec.tools.outputs.type "
    "(which uses `string`/`int`/`float`/`bool`/`date`/`datetime`/`category`)."
)


class SimulationPlanValidationError(NLGenError):
    """Claude returned a tool input that failed SimulationPlan validation."""

    def __init__(
        self, message: str, *, raw_input: Any, pydantic_error: ValidationError
    ):
        super().__init__(message)
        self.raw_input = raw_input
        self.pydantic_error = pydantic_error


class NoSimToolUseError(NLGenError):
    """Claude responded without calling the emit_simulation_plan tool."""

    def __init__(
        self, message: str, *, stop_reason: str | None, content_preview: str
    ):
        super().__init__(message)
        self.stop_reason = stop_reason
        self.content_preview = content_preview


def _build_tool_definition() -> dict[str, Any]:
    """Anthropic tool definition.

    Same `{"plan": <SimulationPlan>}` wrapping pattern as A3.1 — small models
    nest deep object outputs under an outer field even when the schema is
    flat. `$defs` are hoisted to the input_schema root so `$ref` pointers
    resolve correctly.
    """
    plan_schema = SimulationPlan.model_json_schema()
    defs = plan_schema.pop("$defs", {})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"plan": plan_schema},
        "required": ["plan"],
    }
    if defs:
        input_schema["$defs"] = defs
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": input_schema,
    }


_TOOL_DEFINITION: dict[str, Any] = _build_tool_definition()
"""Computed once at import time — SimulationPlan schema is static."""


def _format_user_message(workflow_spec: WorkflowSpec) -> str:
    """Render the WorkflowSpec into a user message Claude reads.

    We send the spec as JSON (Pydantic's `model_dump_json`) rather than
    paraphrasing it — the LLM gets the same structural view downstream
    consumers will get, which makes prompt-engineering reproducible and
    makes the live tests snapshot the same shape the kernel sees.
    """
    payload = workflow_spec.model_dump_json(indent=2)
    return (
        "Here is the WorkflowSpec to generate a simulator for. Read the "
        "environment.entities, env_generators, personas, seasonality, and "
        "known_past_misses fields carefully — they tell you what kind of "
        "events the sim should emit and what hidden ground-truth labels the "
        "eval will need.\n\n"
        f"```json\n{payload}\n```"
    )


def _normalize_payload(payload: Any) -> Any:
    """Force-overwrite `schema_version` to the canonical literal.
    See `workflow_spec_generator._normalize_payload` for rationale —
    qwen3-family's `schema_version: "1.1"` training prior won't yield
    to in-prompt directives. No-op for cloud models that already emit
    the correct value."""
    if isinstance(payload, dict) and "schema_version" in payload:
        return {**payload, "schema_version": SCHEMA_VERSION}
    return payload


_RETRY_FEEDBACK = (
    "Reminders from the system prompt:\n"
    "- Stay within `ALLOWED_IMPORTS` for the sim module.\n"
    "- Every entity in the spec's `environment.entities` must have a "
    "matching generator in the plan.\n"
    "- Hidden-state fields cited by `success_criterion` must be produced "
    "by the simulation.\n"
    "- Do not invent extra top-level fields; pydantic rejects unknown keys."
)


async def generate_simulation_plan(
    client: AsyncAnthropic,
    workflow_spec: WorkflowSpec,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    design_brief_block: str | None = None,
) -> SimulationPlan:
    """Generate a typed SimulationPlan from a WorkflowSpec.

    Args:
        client: An AsyncAnthropic client (any /v1/messages-compatible endpoint).
        workflow_spec: The A3.1 artifact this sim is for.
        model: Anthropic model id. Default opus 4.7.
        max_tokens: Output cap. Default 8k — plans are smaller than specs
            (no UI block, no 8-tool surface).
        max_retries: On `ValidationError`, retry up to this many times
            with the pydantic error fed back as a `tool_result`. Default 4 (= 5 attempts total).

    Returns:
        A validated `SimulationPlan`. Render via
        `sim_render.render_simulation_module`.

    Raises:
        NoSimToolUseError: Claude stopped without calling the tool.
        SimulationPlanValidationError: All attempts produced inputs that
            failed `SimulationPlan.model_validate`.
    """
    user_message = _format_user_message(workflow_spec)
    if design_brief_block:
        user_message = f"{user_message}\n\n{design_brief_block}"
    try:
        plan, _raw = await call_with_validation_retry(
            client=client,
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            tool_definition=_TOOL_DEFINITION,
            tool_name=TOOL_NAME,
            initial_user_message=user_message,
            schema_class=SimulationPlan,
            envelope_key="plan",
            max_retries=max_retries,
            extra_feedback=_RETRY_FEEDBACK,
            normalize=_normalize_payload,
        )
        return plan
    except NoToolUseSignal as exc:
        raise NoSimToolUseError(
            f"Model {model} did not call {TOOL_NAME} (stop_reason={exc.stop_reason!r})",
            stop_reason=exc.stop_reason,
            content_preview=exc.content_preview,
        ) from exc
    except RetryExhaustedError as exc:
        preview = truncate_for_error(exc.raw_input)
        raise SimulationPlanValidationError(
            f"Tool input failed SimulationPlan validation after {exc.attempts} "
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
    "NoSimToolUseError",
    "SimulationPlanValidationError",
    "generate_simulation_plan",
]
