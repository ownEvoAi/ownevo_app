"""Token-budget guardrail for agent-driven eval replays (A4.5).

Karpathy pattern: pre-declare a fixed token budget per eval replay; abort
cleanly when the cumulative spend crosses the cap so a runaway agent or
a regressed prompt doesn't quietly burn the day's API budget.

Why a guardrail at all (PLAN.md A4.5): A4.4's `make nl-gen-smoketest`
costs ~16 calls per workflow on a 12-case fixture. A regression that
loops the agent or a fixture that explodes case count can 10x the spend
silently. The budget makes that a typed failure, not a surprise on the
billing dashboard.

How it works:

  1. The smoketest CLI (or any caller of `solve_with_agent` /
     `run_with_agent`) constructs a `TokenBudget(max_tokens=N)`.
  2. The budget threads through the agent solver. After every
     `client.messages.create` call, we read `msg.usage.input_tokens`
     and `msg.usage.output_tokens` and call `budget.record(...)`.
  3. `record` raises `TokenBudgetExceededError` (an `AgentSolverError`
     so existing callers' error handling doesn't grow special cases)
     the moment cumulative usage > cap.

The check is deliberately post-call — we need the API response to
read `usage`. That means the actual spend can exceed the cap by at
most one call's worth of tokens; the goal is bounded surprise, not
cap-to-the-token precision. Subclassing `AgentSolverError` keeps the
typed-exception hierarchy: orchestrators that already handle solver
errors aren't surprised by a new failure category.

Counts `input_tokens + output_tokens`. Cache-read tokens are not
deducted — the cap is the gross-token surface (the thing that scales
with prompt size), not the billable-token surface (which depends on
caching). For a guardrail, the gross surface is the right primitive.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)

from .agent_solver import AgentSolverError


class TokenBudgetExceededError(AgentSolverError):
    """Cumulative tokens exceeded the budget cap.

    Subclass of `AgentSolverError` so callers that already catch the
    solver's typed errors handle this without a new branch. Carries the
    final accumulator state so the caller can surface what the agent
    spent before the abort fired.
    """

    def __init__(
        self,
        message: str,
        *,
        max_tokens: int,
        used_input: int,
        used_output: int,
        n_calls: int,
        last_label: str | None,
    ) -> None:
        super().__init__(message)
        self.max_tokens = max_tokens
        self.used_input = used_input
        self.used_output = used_output
        self.n_calls = n_calls
        self.last_label = last_label

    @property
    def used_total(self) -> int:
        return self.used_input + self.used_output


@dataclass
class TokenBudget:
    """Accumulator + cap for one agent-driven replay.

    `max_tokens` is a hard cap on `used_input + used_output`. Cache-read
    tokens are not subtracted — the cap is the gross prompt+completion
    surface, not the billable surface.

    `n_calls` and `last_label` exist so the abort message can name the
    case where the cap tipped, which is what an operator wants when
    they look at the smoketest output.
    """

    max_tokens: int
    used_input: int = 0
    used_output: int = 0
    n_calls: int = 0
    last_label: str | None = field(default=None)

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError(
                f"TokenBudget.max_tokens must be > 0; got {self.max_tokens}"
            )

    @property
    def used_total(self) -> int:
        return self.used_input + self.used_output

    @property
    def remaining(self) -> int:
        """Tokens left before the cap. May be negative immediately
        after the call that tipped the budget — the abort fires
        before the next call, so a negative value here is transient
        and only ever observed by the exception that follows."""
        return self.max_tokens - self.used_total

    def record(
        self, *, input_tokens: int, output_tokens: int, label: str
    ) -> None:
        """Add one call's usage to the accumulator; abort if over cap.

        Args:
            input_tokens: From `msg.usage.input_tokens`.
            output_tokens: From `msg.usage.output_tokens`.
            label: Human-readable label for the call (case_id, stage
                name, etc.). Surfaces in the abort message.

        Raises:
            TokenBudgetExceededError: Cumulative `input_tokens +
                output_tokens` (across all `record` calls) exceeded
                `max_tokens`.
        """
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError(
                f"TokenBudget.record received negative usage: "
                f"input={input_tokens} output={output_tokens} (label={label!r})"
            )
        self.used_input += input_tokens
        self.used_output += output_tokens
        self.n_calls += 1
        self.last_label = label
        if self.used_total > self.max_tokens:
            raise TokenBudgetExceededError(
                f"token budget exceeded after {self.n_calls} call(s): "
                f"used {self.used_total} > cap {self.max_tokens} "
                f"(input={self.used_input}, output={self.used_output}, "
                f"last_label={label!r})",
                max_tokens=self.max_tokens,
                used_input=self.used_input,
                used_output=self.used_output,
                n_calls=self.n_calls,
                last_label=self.last_label,
            )


def extract_usage(msg: object) -> tuple[int, int]:
    """Pull `(input_tokens, output_tokens)` off an Anthropic Message.

    Returns `(0, 0)` when the message has no `usage` block — a defensive
    default for scripted test clients that don't fake the usage shape.
    Real `AsyncAnthropic` responses always carry `usage`; the zero
    fallback keeps tests where usage doesn't matter from having to
    pretend.
    """
    usage = getattr(msg, "usage", None)
    if usage is None:
        return 0, 0
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    if input_tokens == 0 and output_tokens == 0:
        _log.warning(
            "extract_usage: usage object present but both input_tokens and "
            "output_tokens resolved to 0 — the SDK may have renamed these "
            "fields. Token budget will not accumulate for this call."
        )
    return input_tokens, output_tokens


__all__ = [
    "TokenBudget",
    "TokenBudgetExceededError",
    "extract_usage",
]
