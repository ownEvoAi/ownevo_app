"""Schema + content tests for the design-agent prompt library (slice 1).

Pins the contract that 9.1.2 (`POST /api/design-agent/next-question`) and
the matching web UX will rely on:

  * every known template has at least one `metric` and one `ambiguity`
    question — together they make a 5-minute demo possible.
  * the registry covers exactly the three vertical-template slugs that
    web `templates.ts` ships today; adding a fourth template requires
    updating both sides in the same change.
  * the generic fallback fires for unknown / missing template ids.
  * each question round-trips through pydantic (frozen schema).
"""

from __future__ import annotations

import pytest
from ownevo_kernel.design_agent.prompts import (
    DISCOVERY_QUESTION_KINDS,
    GENERIC_DISCOVERY_QUESTIONS,
    DiscoveryQuestion,
    get_discovery_questions,
    known_template_ids,
)
from pydantic import ValidationError

EXPECTED_TEMPLATE_IDS = (
    "clinical-trial-site-selection",
    "credit-risk-recalibration",
    "retail-demand-planning",
)


def test_registry_covers_the_three_vertical_templates() -> None:
    assert known_template_ids() == EXPECTED_TEMPLATE_IDS


@pytest.mark.parametrize("template_id", EXPECTED_TEMPLATE_IDS)
def test_each_template_has_metric_and_ambiguity_question(template_id: str) -> None:
    questions = get_discovery_questions(template_id)
    kinds = {q.kind for q in questions}
    assert "metric" in kinds, f"{template_id} missing metric question"
    assert "ambiguity" in kinds, f"{template_id} missing ambiguity question"


def test_generic_fallback_exercises_every_discovery_question_kind() -> None:
    """Generic is the most expansive set — covers every kind in the Literal.

    Per-template prompt sets ship a focused subset (typically metric +
    ambiguity from the verbatim demo plans); generic carries at least
    one example of every kind so the wire format is exercised end-to-end
    before any per-template set opts into the additional kinds.
    """
    kinds = {q.kind for q in GENERIC_DISCOVERY_QUESTIONS}
    assert kinds == set(DISCOVERY_QUESTION_KINDS), (
        f"generic missing kinds: {set(DISCOVERY_QUESTION_KINDS) - kinds}"
    )


def test_discovery_question_kinds_enumerates_full_literal() -> None:
    """The runtime tuple must stay in sync with the Literal at module load."""
    assert DISCOVERY_QUESTION_KINDS == (
        "metric",
        "ambiguity",
        "trigger",
        "surface",
        "premise",
    )


@pytest.mark.parametrize("kind", DISCOVERY_QUESTION_KINDS)
def test_discovery_question_accepts_every_declared_kind(kind: str) -> None:
    q = DiscoveryQuestion(kind=kind, question="x", rationale="y")
    assert q.kind == kind


def test_discovery_question_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        DiscoveryQuestion(kind="bogus-kind", question="x")


@pytest.mark.parametrize(
    "template_id",
    [None, "unknown-template-slug", "retail-demand-PLANNING-typo"],
)
def test_unknown_or_missing_template_falls_back_to_generic(
    template_id: str | None,
) -> None:
    assert get_discovery_questions(template_id) is GENERIC_DISCOVERY_QUESTIONS


@pytest.mark.parametrize("template_id", EXPECTED_TEMPLATE_IDS)
def test_questions_round_trip_through_pydantic(template_id: str) -> None:
    for q in get_discovery_questions(template_id):
        roundtripped = DiscoveryQuestion.model_validate_json(q.model_dump_json())
        assert roundtripped == q


@pytest.mark.parametrize("template_id", EXPECTED_TEMPLATE_IDS)
def test_questions_have_non_empty_rationale(template_id: str) -> None:
    """Rationale is the 'consultative, not generic intake' guard.

    A question without a rationale tends to read like a form field, which
    breaks the design-agent posture. Enforce that every shipped question
    carries a one-sentence reason.
    """
    for q in get_discovery_questions(template_id):
        assert q.rationale and q.rationale.strip(), (
            f"{template_id} question has no rationale: {q.question!r}"
        )


def test_generic_fallback_questions_have_non_empty_rationale() -> None:
    for q in GENERIC_DISCOVERY_QUESTIONS:
        assert q.rationale and q.rationale.strip(), (
            f"generic question has no rationale: {q.question!r}"
        )


def test_discovery_question_options_none_is_valid() -> None:
    q = DiscoveryQuestion(kind="ambiguity", question="What cadence?", options=None)
    assert q.options is None
    rt = DiscoveryQuestion.model_validate_json(q.model_dump_json())
    assert rt == q
    assert rt.options is None


def test_discovery_question_is_immutable() -> None:
    q = DiscoveryQuestion(kind="metric", question="Which metric?")
    with pytest.raises((ValidationError, TypeError)):
        q.kind = "ambiguity"  # type: ignore[misc]


def test_design_agent_all_exports_are_importable() -> None:
    import ownevo_kernel.design_agent as pkg

    for name in pkg.__all__:
        assert hasattr(pkg, name), f"design_agent.__all__ lists {name!r} but it is not an attribute"


def test_design_agent_prompts_all_exports_are_importable() -> None:
    import ownevo_kernel.design_agent.prompts as pkg

    for name in pkg.__all__:
        assert hasattr(pkg, name), (
            f"design_agent.prompts.__all__ lists {name!r} but it is not an attribute"
        )


@pytest.mark.parametrize(
    "bad_kwargs",
    [
        {"kind": "metric", "question": ""},
        {"kind": "unknown_kind", "question": "Q?"},
        {"kind": "metric", "question": "Q?", "extra_field": "x"},
    ],
)
def test_discovery_question_rejects_invalid_inputs(bad_kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        DiscoveryQuestion(**bad_kwargs)
