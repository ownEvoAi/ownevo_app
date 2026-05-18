"""Retry-on-validation-error helper for forced-tool-use generators.

Cloud frontier models (opus 4.7) nearly always pass schema validation on
the first attempt. Local models — qwen3-family on LMS Anthropic-compat,
in particular — produce *structurally* valid tool inputs that violate
finer schema constraints (extra fields the system prompt forbids, wrong
literal values like `schema_version: "1.1"` instead of `"0.1"`).

The retry loop sends the pydantic `ValidationError` back as a
`tool_result` with `is_error=True` so the model can see what went
wrong and emit a corrected tool call. Each retry adds one round-trip;
cloud models cost zero extra round-trips (first attempt succeeds), so
the default of 2 retries is free for them and adds robustness for
local-LLM operators.

Scope: only ValidationError is retried — `NoToolUseError` (model didn't
call the tool at all) is a different failure mode and propagates after
the first attempt.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


T = TypeVar("T", bound=BaseModel)


DEFAULT_MAX_RETRIES = 2
"""Default 2 retries → up to 3 attempts total. Cloud models pass on
attempt 1; local models get two chances to correct based on the
ValidationError feedback."""


_MAX_REPORTED_ERRORS = 20
"""Cap the number of pydantic errors echoed back to the model so the
follow-up prompt doesn't bloat past the input window on schemas with
many violations. The model rarely needs more than a handful to course-
correct; the rest follow once the obvious ones are fixed."""


class NoToolUseSignal(Exception):
    """Raised by `call_with_validation_retry` when the model finished
    its turn without emitting the expected tool_use block. Distinct
    from validation failures — different domain.

    Callers translate this to their generator-specific `NoToolUseError`
    so the public exception surface is unchanged.
    """

    def __init__(self, *, stop_reason: str | None, content_preview: str) -> None:
        super().__init__(f"Model did not call the expected tool (stop_reason={stop_reason!r})")
        self.stop_reason = stop_reason
        self.content_preview = content_preview


class RetryExhaustedError(Exception):
    """All retries failed validation. Carries the final
    `ValidationError` plus the raw tool input from that final attempt,
    so callers can wrap both into their domain-specific exception.
    """

    def __init__(
        self,
        *,
        pydantic_error: ValidationError,
        raw_input: Any,
        attempts: int,
    ) -> None:
        super().__init__(
            f"Tool input failed validation after {attempts} attempts: "
            f"{pydantic_error.error_count()} errors"
        )
        self.pydantic_error = pydantic_error
        self.raw_input = raw_input
        self.attempts = attempts


def _dump_block(block: Any) -> dict[str, Any]:
    """Render an SDK content block as a dict for the next `messages.create`
    payload. The Anthropic SDK returns pydantic blocks (`TextBlock`,
    `ToolUseBlock`) with `.model_dump()`. Tests mock content with
    `SimpleNamespace`, which doesn't — fall back to attribute extraction
    so tests aren't burdened with constructing pydantic objects."""
    if hasattr(block, "model_dump"):
        return block.model_dump()
    if isinstance(block, dict):
        return block
    if hasattr(block, "__dict__"):
        return {k: v for k, v in vars(block).items() if not k.startswith("_")}
    raise TypeError(f"Cannot dump content block of type {type(block)!r}")


def _format_validation_feedback(exc: ValidationError, extra_rules: str = "") -> str:
    """Format a ValidationError into a tool_result body the model can
    actually use to correct its next attempt.

    Lists the first `_MAX_REPORTED_ERRORS` errors with `loc.path: msg`
    so the model sees both *where* and *why* each field failed. The
    extra_rules string is appended verbatim — generators pass the
    domain-specific rules from their system prompt (e.g., the
    `schema_version` literal, the allowed `type` enum, etc.) so the
    model has the constraint restated next to the error.
    """
    lines = [
        "Your previous tool call failed validation against the schema. ",
        "These are the specific errors:",
        "",
    ]
    for err in exc.errors()[:_MAX_REPORTED_ERRORS]:
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"- {loc}: {err['msg']} [type={err['type']}]")
    if exc.error_count() > _MAX_REPORTED_ERRORS:
        lines.append(f"... and {exc.error_count() - _MAX_REPORTED_ERRORS} more")
    lines.append("")
    lines.append(
        "Please call the same tool again with the input corrected. "
        "Address every error above, and stay strictly inside the schema — "
        "do not add fields that aren't in the schema, and use the exact "
        "literal values where the schema demands them."
    )
    if extra_rules:
        lines.append("")
        lines.append(extra_rules)
    return "\n".join(lines)


async def call_with_validation_retry(
    *,
    client: AsyncAnthropic,
    model: str,
    max_tokens: int,
    system: str,
    tool_definition: dict[str, Any],
    tool_name: str,
    initial_user_message: str,
    schema_class: type[T],
    envelope_key: str | None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    extra_feedback: str = "",
) -> tuple[T, Any]:
    """Run a forced-tool-use call with retry on `ValidationError`.

    Args:
        client: AsyncAnthropic client (any `/v1/messages`-compatible endpoint).
        model: Anthropic model id.
        max_tokens: Per-turn output cap.
        system: System prompt for every attempt (cached by the SDK).
        tool_definition: The single tool to expose. `tool_choice` is forced.
        tool_name: Name of that tool — used to filter the response and
            to wire the `tool_result` follow-up.
        initial_user_message: First user turn text.
        schema_class: Pydantic class to validate the unwrapped payload against.
        envelope_key: When the tool wraps its payload under a named key
            (e.g., `spec`, `plan`), pass that key; we unwrap when the
            tool input is `{key: {...}}`. Pass `None` if no wrapping.
        max_retries: Number of retry attempts after the first call.
            Total calls = `max_retries + 1`. Default 2.
        extra_feedback: Domain-specific rules appended to the feedback
            tool_result on retry. Lets each generator restate its
            non-obvious constraints (allowed enums, forbidden fields,
            schema_version literal).

    Returns:
        Tuple of `(validated_model, raw_input_from_successful_attempt)`.

    Raises:
        NoToolUseSignal: Model finished without calling the tool. Not retried.
        ValidationError: Final attempt still failed validation. The
            caller wraps this in its domain-specific `*ValidationError`.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": initial_user_message}]
    last_exc: ValidationError | None = None
    last_raw: Any = None

    for attempt in range(max_retries + 1):
        msg = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=[tool_definition],
            tool_choice={"type": "tool", "name": tool_name},
            messages=messages,
        )

        tool_blocks = [
            b for b in msg.content
            if getattr(b, "type", None) == "tool_use"
            and getattr(b, "name", None) == tool_name
        ]
        if not tool_blocks:
            text_blocks = [b for b in msg.content if getattr(b, "type", None) == "text"]
            preview = (text_blocks[0].text if text_blocks else "")[:300]
            raise NoToolUseSignal(stop_reason=msg.stop_reason, content_preview=preview)

        tool_use_block = tool_blocks[0]
        raw_input = tool_use_block.input
        last_raw = raw_input

        if (
            envelope_key is not None
            and isinstance(raw_input, dict)
            and envelope_key in raw_input
            and len(raw_input) == 1
        ):
            payload = raw_input[envelope_key]
        else:
            payload = raw_input

        try:
            return schema_class.model_validate(payload), raw_input
        except ValidationError as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            # Feedback turn — model sees its own tool_use then a
            # tool_result carrying the validation error, and is
            # asked to retry.
            messages = [
                *messages,
                {
                    "role": "assistant",
                    "content": [_dump_block(b) for b in msg.content],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_block.id,
                            "is_error": True,
                            "content": _format_validation_feedback(exc, extra_feedback),
                        }
                    ],
                },
            ]

    assert last_exc is not None  # we only break out of the loop after a ValidationError
    raise RetryExhaustedError(
        pydantic_error=last_exc,
        raw_input=last_raw,
        attempts=max_retries + 1,
    )


def truncate_for_error(raw_input: Any, limit: int = 500) -> str:
    """Render `raw_input` for an error message, truncated to `limit`
    chars. Falls back to `repr()` if the input isn't JSON-serializable
    (e.g., it embeds a pydantic block that didn't dump cleanly)."""
    try:
        return json.dumps(raw_input)[:limit]
    except (TypeError, ValueError):
        return repr(raw_input)[:limit]


__all__ = [
    "DEFAULT_MAX_RETRIES",
    "NoToolUseSignal",
    "RetryExhaustedError",
    "call_with_validation_retry",
    "truncate_for_error",
]
