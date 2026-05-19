"""LLM-driven design-agent interviewer.

Owns the `pick_next_question` entry point the FastAPI route calls.
Behaviour, in order:

1. Decode the prior_answers list into a `covered` set of dimensions.
2. If every dimension in `DIMENSION_SPECS` has at least one answer or
   skip recorded, return `None` — the interview is done; the route
   surfaces `done=true`.
3. Otherwise, forced-tool-call an Anthropic model with `ask_question`
   as the only tool. The model receives:
     * The workflow description the operator wrote (verbatim).
     * The template id, if any.
     * The list of dimensions still open + each one's intent prose.
     * The prior Q&A trail.
   The model returns one question targeting one of the open dimensions,
   with 2–4 mutually-exclusive options each carrying a one-line pro
   and con, plus a recommendation + ELI rationale + stakes line.
4. The response is validated; on validation failure the call falls
   through to the hardcoded prompt library (the legacy path), so a
   transient LLM hiccup never blocks the operator.

`pick_next_question` is async because Anthropic's SDK is. The
hardcoded fallback is sync but wrapped in the same async function.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .dimensions import (
    DIMENSION_SPECS,
    DesignDimension,
    DimensionSpec,
    dimensions_remaining,
    spec_for,
)

# The interviewer is a smaller model than the agent solver — it makes
# one tool call per turn, no trajectory reasoning, so haiku is fine.
# Sonnet bumps quality on edge cases but doubles latency / cost. Bump
# via env var if dogfooding shows quality is the blocker.
DEFAULT_INTERVIEWER_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 2_000


# Vocabulary the model needs to see to recommend Operate-UI primitives.
# Mirrors `_PRIMITIVE_PAYLOAD_GUIDE` in eval_runner/agent_solver.py — if
# the primitive set changes there, update here too.
_PRIMITIVE_VOCABULARY: tuple[tuple[str, str], ...] = (
    ("MetricCards", "2-4 headline numbers — credit utilisation %, default count, etc."),
    ("TimeSeriesChart", "Trend lines over time — forecast curves, usage rates."),
    ("TableView", "One row per recommendation / account / SKU / ticket."),
    ("AlertList", "Severity-stamped items the operator should look at now."),
    ("KanbanBoard", "Work items grouped by state (review queue, escalation lanes)."),
    ("ScheduleGrid", "Day-x-shift staffing or capacity matrix."),
    ("ConversationView", "Threaded customer / counter-party exchange."),
    ("SideBySideView", "Before / after pair — current clause vs proposed redline."),
    ("DocumentReader", "Structured document with margin annotations."),
)


@dataclass(frozen=True)
class PriorAnswer:
    """One prior-round answer the interviewer takes as covered context."""

    dimension: str  # one of DesignDimension; may legacy-empty for old clients
    question: str
    chosen_option: str | None  # None when the operator answered free-form only
    free_text: str | None  # the operator's typed elaboration, when any

    def is_skip(self) -> bool:
        """Returns True when the operator marked this question as "not
        applicable" rather than answering. Skips still count as covered."""
        return self.chosen_option == "__skip__" and not (self.free_text or "").strip()


class OptionBrief(BaseModel):
    """One choosable option in a decision brief."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    pro: str = Field(min_length=10, max_length=400)
    con: str = Field(min_length=10, max_length=400)


class QuestionBrief(BaseModel):
    """Structured decision brief the design agent surfaces.

    Mirrors the gstack /office-hours decision-brief shape: every
    question carries enough context that a domain expert who has never
    used ownEvo can pick an option without asking a follow-up.
    """

    model_config = ConfigDict(extra="forbid")

    dimension: DesignDimension
    question: str = Field(min_length=10, max_length=800)
    eli: str = Field(
        min_length=10,
        max_length=600,
        description=(
            "Plain-English framing — what the question really asks, "
            "in language a domain expert who has never seen a spec "
            "before can follow."
        ),
    )
    stakes: str = Field(
        min_length=10,
        max_length=400,
        description=(
            "One sentence on what breaks downstream if the wrong "
            "option is chosen — e.g. 'wrong metric direction means "
            "the gate rejects every improvement'."
        ),
    )
    options: list[OptionBrief] = Field(min_length=2, max_length=4)
    recommendation_index: int = Field(
        ge=0,
        description=(
            "Zero-based index into `options` the interviewer recommends. "
            "Always present, including for 'taste calls' — the "
            "recommendation lets the UI surface a default choice."
        ),
    )
    rationale: str = Field(
        min_length=10,
        max_length=1200,
        description=(
            "Why the interviewer recommends this option in particular. "
            "References the description, prior answers, or a domain "
            "norm — never just 'safer default'."
        ),
    )


class InterviewerError(Exception):
    """Raised when the LLM response cannot be coerced into a QuestionBrief.

    Callers SHOULD catch this and fall through to the hardcoded prompt
    library so the operator is never blocked by a transient hiccup.
    """


# --- Tool definition (forced-tool-use forces Sonnet to call this) ---

ASK_QUESTION_TOOL = {
    "name": "ask_question",
    "description": (
        "Emit the next decision brief for the design-agent interview. "
        "Target exactly one of the open dimensions. Give the operator "
        "2-4 mutually-exclusive options, each with one pro and one "
        "con. Pick a recommendation and explain why it fits THIS "
        "operator's description / prior answers (not a generic best "
        "practice)."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "dimension": {
                "type": "string",
                "enum": [d.key for d in DIMENSION_SPECS],
                "description": "One of the open dimensions; closed dimensions are forbidden.",
            },
            "question": {
                "type": "string",
                "minLength": 10,
                "description": "The question the operator sees first. One sentence.",
            },
            "eli": {
                "type": "string",
                "minLength": 10,
                "description": "Plain-English framing for an operator who has never seen a spec.",
            },
            "stakes": {
                "type": "string",
                "minLength": 10,
                "description": "What breaks if the wrong option is chosen.",
            },
            "options": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "label": {"type": "string", "minLength": 1},
                        "pro": {"type": "string", "minLength": 10},
                        "con": {"type": "string", "minLength": 10},
                    },
                    "required": ["label", "pro", "con"],
                },
            },
            "recommendation_index": {
                "type": "integer",
                "minimum": 0,
                "description": "0-based index into options for the recommended choice.",
            },
            "rationale": {
                "type": "string",
                "minLength": 10,
                "maxLength": 1200,
                "description": "Why this recommendation, grounded in the operator's description. Aim for 2–4 sentences; quote the description once if helpful.",
            },
        },
        "required": [
            "dimension",
            "question",
            "eli",
            "stakes",
            "options",
            "recommendation_index",
            "rationale",
        ],
    },
}


SYSTEM_PROMPT = (
    "You are ownEvo's design-agent interviewer — an expert systems analyst "
    "helping a domain expert (a supply-chain VP, chief risk officer, "
    "labour-relations counsel, or similar) define an AI workflow before "
    "the kernel generates its WorkflowSpec / SimulationPlan / EvalCaseSet "
    "/ MetricDefinition / UI layout. You are NOT a chatbot; you are a "
    "decision facilitator.\n\n"
    "Your one job per turn: pick the highest-leverage open dimension and "
    "produce a single decision brief on it. Use the `ask_question` tool "
    "exactly once. Never call it twice; never reply in prose.\n\n"
    "Tone:\n"
    "- Direct. Never sycophantic. Take a position on every recommendation.\n"
    "- Specific. Reference the operator's description verbatim where "
    "possible. Generic 'consider both options' answers are forbidden — "
    "the operator gets that from any consultant.\n"
    "- Domain-aware. A retail planner asks about overstock cost; a credit "
    "officer asks about default coverage; a clinical-trial designer asks "
    "about site enrolment lag. Match the vocabulary to the description.\n"
    "- Honest about tradeoffs. Every option has at least one downside.\n\n"
    "Hard rules:\n"
    "1. Target a dimension from the OPEN list the user message provides. "
    "Closed dimensions are off-limits.\n"
    "2. Options must be mutually exclusive. 'Yes / no / depends' is "
    "almost always wrong — replace 'depends' with a real third option.\n"
    "3. Recommendation must point at a real option index. The rationale "
    "must reference the operator's description or a prior answer, never "
    "a generic best practice.\n"
    "4. Stakes line must name a concrete downstream failure mode, not a "
    "vague risk."
)


def _build_user_message(
    *,
    description: str,
    template_id: str | None,
    prior_answers: Sequence[PriorAnswer],
    open_dimensions: tuple[DimensionSpec, ...],
) -> str:
    parts: list[str] = []
    parts.append(f"## Operator's workflow description\n```\n{description.strip()}\n```")
    if template_id:
        parts.append(f"## Vertical template\n`{template_id}`")
    if prior_answers:
        lines = []
        for i, pa in enumerate(prior_answers, 1):
            dim_label = (spec_for(pa.dimension).label if spec_for(pa.dimension) else pa.dimension) or "(no dimension)"
            if pa.is_skip():
                ans = "[skipped]"
            else:
                bits: list[str] = []
                if pa.chosen_option:
                    bits.append(f"chose: {pa.chosen_option}")
                if pa.free_text and pa.free_text.strip():
                    bits.append(f"elaboration: {pa.free_text.strip()}")
                ans = " · ".join(bits) if bits else "[empty]"
            lines.append(f"  {i}. [{dim_label}] Q: {pa.question}\n     A: {ans}")
        parts.append("## Prior Q&A trail\n" + "\n".join(lines))
    else:
        parts.append("## Prior Q&A trail\n(none — this is the first question)")

    parts.append("## Open dimensions (pick exactly one)")
    open_lines = []
    for d in open_dimensions:
        open_lines.append(f"- `{d.key}` ({d.label}): {d.intent}")
    parts.append("\n".join(open_lines))

    primitives = "\n".join(f"  - {name}: {hint}" for name, hint in _PRIMITIVE_VOCABULARY)
    parts.append(
        "## Operate-UI primitive vocabulary (only relevant for the "
        "`operate_ui_primitives` dimension)\n" + primitives
    )

    parts.append(
        "## Your task\nCall `ask_question` exactly once. Target ONE open "
        "dimension. Produce 2-4 mutually-exclusive options with concrete "
        "labels (e.g. `'Daily 6am refresh'`, NOT `'Option A'`), a "
        "recommendation, and a rationale that quotes or paraphrases the "
        "operator's description."
    )
    return "\n\n".join(parts)


def _is_anthropic_client(obj: Any) -> bool:
    """Duck-type guard so the type hint stays loose (kernel uses
    AsyncAnthropic via composition; tests pass mocks). True when `obj`
    exposes a `.messages.create` coroutine factory."""
    msgs = getattr(obj, "messages", None)
    return msgs is not None and callable(getattr(msgs, "create", None))


async def pick_next_question(
    *,
    description: str,
    template_id: str | None,
    prior_answers: Sequence[PriorAnswer],
    client: Any | None = None,
    model: str = DEFAULT_INTERVIEWER_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> QuestionBrief | None:
    """Pick the next interviewer question, or None when the interview is done.

    Returns `None` when every dimension is covered (the route surfaces
    `done=true`). Raises `InterviewerError` when the LLM call fails or
    its response is malformed — callers should catch and fall through
    to the hardcoded prompt library.
    """
    if client is None or not _is_anthropic_client(client):
        raise InterviewerError(
            "No Anthropic client provided; the interviewer cannot run."
        )

    covered: set[str] = set()
    for pa in prior_answers:
        if pa.dimension:
            covered.add(pa.dimension)
    open_dims = dimensions_remaining(covered)
    if not open_dims:
        return None

    user_message = _build_user_message(
        description=description,
        template_id=template_id,
        prior_answers=prior_answers,
        open_dimensions=open_dims,
    )

    try:
        msg = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            tools=[ASK_QUESTION_TOOL],
            tool_choice={"type": "tool", "name": "ask_question"},
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        raise InterviewerError(
            f"interviewer LLM call failed: {type(exc).__name__}: {exc}"
        ) from exc

    tool_blocks = [
        b for b in getattr(msg, "content", [])
        if getattr(b, "type", None) == "tool_use"
        and getattr(b, "name", None) == "ask_question"
    ]
    if not tool_blocks:
        raise InterviewerError(
            "interviewer LLM did not call ask_question — "
            f"stop_reason={getattr(msg, 'stop_reason', None)!r}"
        )

    raw = tool_blocks[0].input
    if not isinstance(raw, dict):
        raise InterviewerError(
            f"ask_question input is not a dict: {type(raw).__name__}"
        )

    open_keys = {d.key for d in open_dims}
    if raw.get("dimension") not in open_keys:
        raise InterviewerError(
            f"interviewer targeted a closed/unknown dimension: "
            f"{raw.get('dimension')!r}; expected one of "
            f"{sorted(open_keys)}"
        )

    try:
        brief = QuestionBrief.model_validate(raw)
    except ValidationError as exc:
        raise InterviewerError(
            f"ask_question input failed validation: {exc}"
        ) from exc

    if brief.recommendation_index >= len(brief.options):
        raise InterviewerError(
            f"recommendation_index={brief.recommendation_index} is out of "
            f"range for options of length {len(brief.options)}"
        )

    return brief


__all__ = [
    "ASK_QUESTION_TOOL",
    "DEFAULT_INTERVIEWER_MODEL",
    "DEFAULT_MAX_TOKENS",
    "InterviewerError",
    "OptionBrief",
    "PriorAnswer",
    "QuestionBrief",
    "SYSTEM_PROMPT",
    "pick_next_question",
]


# Cosmetic — kept out of __all__: a small string sample for tests /
# logging that doesn't depend on the LLM.
def render_brief_for_debug(brief: QuestionBrief) -> str:
    lines = [
        f"[{brief.dimension}] {brief.question}",
        f"  ELI: {brief.eli}",
        f"  Stakes: {brief.stakes}",
    ]
    for i, opt in enumerate(brief.options):
        marker = "►" if i == brief.recommendation_index else " "
        lines.append(f"  {marker} {i}. {opt.label}")
        lines.append(f"      ✅ {opt.pro}")
        lines.append(f"      ❌ {opt.con}")
    lines.append(f"  Rationale: {brief.rationale}")
    return "\n".join(lines)


def _payload_to_dict(payload: Any) -> dict[str, Any]:
    """Test helper: coerce a plausible JSON-ish input to a dict.

    Real Anthropic SDK already returns a dict for tool input; tests that
    pass a JSON string get coerced here for ergonomic call sites.
    """
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        return json.loads(payload)
    raise TypeError(f"expected dict or JSON string, got {type(payload).__name__}")
