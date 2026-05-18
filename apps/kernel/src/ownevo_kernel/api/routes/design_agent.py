"""`/api/design-agent/*` — authoring-time discovery interview.

Single endpoint today:

  * `POST /api/design-agent/next-question` — given a description, optional
    template id, and the answers collected so far, returns the next
    discovery question the design agent should ask (or `done=true` when
    the interview is complete).

The endpoint is stateless: the client owns the conversation state and
sends the full `prior_answers` list on every request. The kernel walks
the template's prompt registry and returns the lowest-index question
not yet present in `prior_answers`. Question identity is positional —
the index into `get_discovery_questions(template_id)` is the stable
handle the client echoes back.

No LLM call. The ambiguity-detection pass (which is LLM-driven and
reads the generated WorkflowSpec) ships in a follow-up slice as
`POST /api/design-agent/ambiguity-report`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ...design_agent.prompts import (
    DiscoveryQuestionKind,
    get_discovery_questions,
    known_template_ids,
)

router = APIRouter(prefix="/api/design-agent", tags=["design-agent"])

_DESCRIPTION_MAX_LEN = 4096
_ANSWER_MAX_LEN = 2048
_MAX_PRIOR_ANSWERS = 32
# Guard: if any template grows beyond _MAX_PRIOR_ANSWERS questions, a
# client can never complete that interview (the prior_answers list would
# exceed max_length before all indices are covered). Catch at startup.
assert all(
    len(get_discovery_questions(tid)) <= _MAX_PRIOR_ANSWERS
    for tid in known_template_ids()
), (
    f"_MAX_PRIOR_ANSWERS={_MAX_PRIOR_ANSWERS} is smaller than the largest "
    "template's question count — raise the cap or reduce the template."
)


class PriorAnswer(BaseModel):
    """One prior discovery answer the client echoes back on each request."""

    model_config = ConfigDict(extra="forbid")

    question_index: int = Field(ge=0)
    # `None` records a skipped question — the operator chose not to answer
    # but the design agent should not re-ask it. Empty string is a real
    # (if low-quality) answer and is allowed.
    answer: str | None = Field(default=None, max_length=_ANSWER_MAX_LEN)


class NextQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=50, max_length=_DESCRIPTION_MAX_LEN)
    template_id: str | None = Field(default=None, max_length=64)
    prior_answers: list[PriorAnswer] = Field(
        default_factory=list,
        max_length=_MAX_PRIOR_ANSWERS,
    )


class NextDiscoveryQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_index: int
    kind: DiscoveryQuestionKind
    question: str
    options: tuple[str, ...] | None
    rationale: str | None


class NextQuestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_question: NextDiscoveryQuestion | None
    done: bool
    total_questions: int
    answered_count: int


@router.post(
    "/next-question",
    response_model=NextQuestionResponse,
    response_model_exclude_none=True,
)
def next_question(req: NextQuestionRequest) -> NextQuestionResponse:
    questions = get_discovery_questions(req.template_id)
    total = len(questions)

    seen_indices: set[int] = set()
    for pa in req.prior_answers:
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

    answered_count = len(seen_indices)

    if next_idx is None:
        return NextQuestionResponse(
            next_question=None,
            done=True,
            total_questions=total,
            answered_count=answered_count,
        )

    q = questions[next_idx]
    return NextQuestionResponse(
        next_question=NextDiscoveryQuestion(
            question_index=next_idx,
            kind=q.kind,
            question=q.question,
            options=q.options,
            rationale=q.rationale,
        ),
        done=False,
        total_questions=total,
        answered_count=answered_count,
    )
