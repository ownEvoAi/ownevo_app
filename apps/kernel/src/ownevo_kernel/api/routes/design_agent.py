"""`/api/design-agent/*` — authoring-time discovery interview.

Single endpoint today:

  * `POST /api/design-agent/next-question` — given a description,
    optional template id, and the answers collected so far, returns
    the next discovery question the design agent should ask (or
    `done=true` when every dimension is covered).

Two execution paths:

1. **LLM interviewer (default).** Reads the description + prior Q&A
   trail, picks the highest-leverage open dimension from
   `DIMENSION_SPECS`, and forced-tool-calls Sonnet for a structured
   `QuestionBrief` (question + ELI + stakes + options w/ pros/cons +
   recommendation + rationale). Coverage logic lives in the kernel,
   not in the LLM, so the interview always terminates.

2. **Hardcoded fallback.** When `ANTHROPIC_API_KEY` is missing OR the
   LLM call fails OR its response fails validation, the route falls
   back to the legacy `get_discovery_questions(template_id)` prompt
   library so the operator is never blocked. Fallback responses fill
   the new fields (`eli`, `stakes`, `recommendation_index`,
   `pro`/`con`) with sensible defaults derived from the hardcoded
   `DiscoveryQuestion`.

The endpoint is stateless: the client owns the conversation state and
sends the full `prior_answers` list on every request.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ...design_agent import (
    DESIGN_DIMENSIONS,
    DIMENSION_SPECS,
    DesignDimension,
    InterviewerError,
    PriorAnswer as InterviewerPriorAnswer,
    QuestionBrief,
    pick_next_question,
    spec_for,
)
from ...design_agent.prompts import (
    DiscoveryQuestion,
    DiscoveryQuestionKind,
    get_discovery_questions,
    known_template_ids,
)

router = APIRouter(prefix="/api/design-agent", tags=["design-agent"])

_DESCRIPTION_MAX_LEN = 4096
_ANSWER_MAX_LEN = 2048
_MAX_PRIOR_ANSWERS = 32

# Guard: legacy hardcoded templates must still fit in the answer cap so
# the fallback path never deadlocks. Mostly cosmetic now that the LLM
# path is the default.
assert all(
    len(get_discovery_questions(tid)) <= _MAX_PRIOR_ANSWERS
    for tid in known_template_ids()
), (
    f"_MAX_PRIOR_ANSWERS={_MAX_PRIOR_ANSWERS} is smaller than the largest "
    "template's question count — raise the cap or reduce the template."
)


class PriorAnswerIn(BaseModel):
    """One prior discovery answer the client echoes back on each request.

    `dimension` is the new wire-level field — the LLM interviewer needs
    to know which dimension each prior answer covered. Older clients
    that don't send it get treated as "unknown dimension" and the
    interviewer just sees the answer text without dimension attribution.

    `question_index` stays around so the legacy fallback path can still
    walk the hardcoded prompt library.
    """

    model_config = ConfigDict(extra="forbid")

    dimension: DesignDimension | None = Field(
        default=None,
        description=(
            "Dimension this prior answer covered. Required for the LLM "
            "path; legacy clients (hardcoded fallback) may omit."
        ),
    )
    question: str = Field(default="", max_length=_DESCRIPTION_MAX_LEN)
    question_index: int | None = Field(default=None, ge=0)
    chosen_option: str | None = Field(default=None, max_length=_ANSWER_MAX_LEN)
    free_text: str | None = Field(default=None, max_length=_ANSWER_MAX_LEN)
    # Legacy field kept so old web builds don't break. Equivalent to
    # `chosen_option` for new clients.
    answer: str | None = Field(default=None, max_length=_ANSWER_MAX_LEN)


class NextQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=50, max_length=_DESCRIPTION_MAX_LEN)
    template_id: str | None = Field(default=None, max_length=64)
    prior_answers: list[PriorAnswerIn] = Field(
        default_factory=list,
        max_length=_MAX_PRIOR_ANSWERS,
    )


class OptionOut(BaseModel):
    """One choosable option with its pros and cons."""

    model_config = ConfigDict(extra="forbid")

    label: str
    pro: str
    con: str


class NextDiscoveryQuestion(BaseModel):
    """Structured decision brief returned to the client.

    Shape mirrors gstack /office-hours: every question carries enough
    context that a domain expert can pick an option without follow-up.
    The legacy `kind` + `question_index` fields stay around for the
    hardcoded fallback path; new fields (`dimension`, `eli`, `stakes`,
    `options`, `recommendation_index`, `rationale`, `source`) are
    always populated.
    """

    model_config = ConfigDict(extra="forbid")

    # Stable, dimension-scoped identifier. Always populated for the
    # LLM path; legacy fallback fills it from the question's `kind`.
    dimension: DesignDimension
    # The interview source — clients can show a "powered by AI" badge
    # vs. a "template" badge if desired.
    source: str = Field(description="'llm' or 'fallback'.")
    question: str
    eli: str
    stakes: str
    options: list[OptionOut]
    recommendation_index: int = Field(ge=0)
    rationale: str

    # Legacy fields retained for compatibility with the existing web UI
    # until it migrates to the richer brief.
    question_index: int = Field(
        default=0,
        ge=0,
        description="Legacy positional handle for fallback-path answers.",
    )
    kind: DiscoveryQuestionKind | None = Field(default=None)


class NextQuestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_question: NextDiscoveryQuestion | None
    done: bool
    total_questions: int
    answered_count: int


def _legacy_kind_to_dimension(kind: DiscoveryQuestionKind) -> DesignDimension:
    """Map the older `DiscoveryQuestionKind` to a `DesignDimension`.

    The five legacy kinds (metric / ambiguity / trigger / surface /
    premise) don't perfectly align with the seven dimensions; we route
    them to the closest dimension so fallback responses still register
    coverage correctly:
      * metric    → success_metric
      * trigger   → trigger_and_cadence
      * surface   → operate_ui_primitives
      * ambiguity → goal_and_scope (ambiguity in the description IS a
                    scope question — needs clarifying before anything
                    else)
      * premise   → goal_and_scope (assumption pushback is scope work)
    """
    mapping: dict[str, DesignDimension] = {
        "metric": "success_metric",
        "trigger": "trigger_and_cadence",
        "surface": "operate_ui_primitives",
        "ambiguity": "goal_and_scope",
        "premise": "goal_and_scope",
    }
    return mapping.get(kind, "goal_and_scope")


def _fallback_question_to_brief(
    *,
    q: DiscoveryQuestion,
    index: int,
) -> NextDiscoveryQuestion:
    """Coerce a hardcoded `DiscoveryQuestion` into the richer brief shape.

    Hardcoded questions don't carry pros/cons or recommendation indexes,
    so we fill them with `(no detail)` placeholders + recommend option 0
    by default. Marked `source='fallback'` so the UI can show a softer
    badge.
    """
    options_raw = q.options or ("Yes", "No")
    options = [
        OptionOut(
            label=o,
            pro="(see rationale)",
            con="(tradeoff not surfaced in fallback mode)",
        )
        for o in options_raw
    ]
    return NextDiscoveryQuestion(
        dimension=_legacy_kind_to_dimension(q.kind),
        source="fallback",
        question=q.question,
        eli=q.rationale or q.question,
        stakes=(
            "Skipping this question lets NL-gen guess on this "
            "dimension — re-running discovery after a bad guess is the "
            "usual recovery."
        ),
        options=options,
        recommendation_index=0,
        rationale=(
            q.rationale
            or "Hardcoded template question; recommendation is the first option."
        ),
        question_index=index,
        kind=q.kind,
    )


def _convert_prior_for_interviewer(
    prior: list[PriorAnswerIn],
) -> list[InterviewerPriorAnswer]:
    out: list[InterviewerPriorAnswer] = []
    for pa in prior:
        dim = pa.dimension or ""
        # New clients send chosen_option; old ones send `answer`. Treat
        # them as equivalent so a partial migration doesn't break either.
        chosen = pa.chosen_option if pa.chosen_option is not None else pa.answer
        out.append(
            InterviewerPriorAnswer(
                dimension=dim,
                question=pa.question or "",
                chosen_option=chosen,
                free_text=pa.free_text,
            )
        )
    return out


def _llm_brief_to_response(brief: QuestionBrief) -> NextDiscoveryQuestion:
    return NextDiscoveryQuestion(
        dimension=brief.dimension,
        source="llm",
        question=brief.question,
        eli=brief.eli,
        stakes=brief.stakes,
        options=[
            OptionOut(label=o.label, pro=o.pro, con=o.con) for o in brief.options
        ],
        recommendation_index=brief.recommendation_index,
        rationale=brief.rationale,
        # The LLM path is stateless w.r.t. positional indices — keep 0
        # so old web clients that key off question_index don't crash.
        # The dimension field is what carries identity in the new path.
        question_index=0,
        kind=None,
    )


def _llm_client_or_none():
    """Construct an AsyncAnthropic when ANTHROPIC_API_KEY is set.

    Docker-compose passes `ANTHROPIC_BASE_URL=` as an empty string when
    the host env doesn't set it; the SDK then errors with
    `UnsupportedProtocol: Request URL is missing protocol`. We defensively
    drop the empty value so the SDK falls back to its built-in cloud
    endpoint. Same trick `nl_gen` uses (see `api/routes/nl_gen.py`).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    if os.environ.get("ANTHROPIC_BASE_URL") == "":
        del os.environ["ANTHROPIC_BASE_URL"]
    try:
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(api_key=api_key)
    except Exception:
        return None


@router.post(
    "/next-question",
    response_model=NextQuestionResponse,
    response_model_exclude_none=True,
)
async def next_question(req: NextQuestionRequest) -> NextQuestionResponse:
    answered_count = len(req.prior_answers)
    total_questions = len(DIMENSION_SPECS)

    # --- LLM path ---
    client = _llm_client_or_none()
    if client is not None:
        try:
            brief = await pick_next_question(
                description=req.description,
                template_id=req.template_id,
                prior_answers=_convert_prior_for_interviewer(req.prior_answers),
                client=client,
            )
        except InterviewerError:
            brief = None
            client = None  # fall through to hardcoded fallback below
        else:
            if brief is None:
                return NextQuestionResponse(
                    next_question=None,
                    done=True,
                    total_questions=total_questions,
                    answered_count=answered_count,
                )
            return NextQuestionResponse(
                next_question=_llm_brief_to_response(brief),
                done=False,
                total_questions=total_questions,
                answered_count=answered_count,
            )

    # --- Hardcoded fallback ---
    # Legacy template walk: pick the lowest unanswered positional index.
    questions = get_discovery_questions(req.template_id)
    total = len(questions)
    if total == 0:
        return NextQuestionResponse(
            next_question=None,
            done=True,
            total_questions=0,
            answered_count=answered_count,
        )

    seen_indices: set[int] = set()
    for pa in req.prior_answers:
        if pa.question_index is None:
            continue
        if pa.question_index >= total:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"prior_answers.question_index={pa.question_index} is out "
                    f"of range for template_id={req.template_id!r} "
                    f"(total_questions={total})"
                ),
            )
        seen_indices.add(pa.question_index)

    next_idx: int | None = None
    for i in range(total):
        if i not in seen_indices:
            next_idx = i
            break

    if next_idx is None:
        return NextQuestionResponse(
            next_question=None,
            done=True,
            total_questions=total,
            answered_count=len(seen_indices),
        )
    return NextQuestionResponse(
        next_question=_fallback_question_to_brief(
            q=questions[next_idx], index=next_idx
        ),
        done=False,
        total_questions=total,
        answered_count=len(seen_indices),
    )


__all__ = [
    "DESIGN_DIMENSIONS",
    "NextDiscoveryQuestion",
    "NextQuestionRequest",
    "NextQuestionResponse",
    "OptionOut",
    "PriorAnswerIn",
    "next_question",
    "router",
]
