"""Template-aware discovery prompt library.

Maps a vertical template id (matching the web `templates.ts` slugs) to a
tuple of `DiscoveryQuestion`s the design agent runs at authoring time.
Falls back to `generic` for free-form descriptions.
"""

from __future__ import annotations

from types import MappingProxyType

from . import clinical_trial, credit_risk, generic, retail_demand
from ._types import (
    DISCOVERY_QUESTION_KINDS,
    DiscoveryQuestion,
    DiscoveryQuestionKind,
)

_REGISTRY: MappingProxyType[str, tuple[DiscoveryQuestion, ...]] = MappingProxyType({
    "retail-demand-planning": retail_demand.DISCOVERY_QUESTIONS,
    "credit-risk-recalibration": credit_risk.DISCOVERY_QUESTIONS,
    "clinical-trial-site-selection": clinical_trial.DISCOVERY_QUESTIONS,
})

GENERIC_DISCOVERY_QUESTIONS: tuple[DiscoveryQuestion, ...] = generic.DISCOVERY_QUESTIONS


def get_discovery_questions(
    template_id: str | None,
) -> tuple[DiscoveryQuestion, ...]:
    """Return the prompt set for a template, or the generic fallback.

    `template_id` is the kebab-slug from the web `templates.ts` (e.g.
    `"retail-demand-planning"`). Unknown ids fall back to the generic set
    rather than raising — the design agent should always have something
    to ask, even on a slug the kernel does not recognise yet.
    """
    if template_id is None:
        return GENERIC_DISCOVERY_QUESTIONS
    return _REGISTRY.get(template_id, GENERIC_DISCOVERY_QUESTIONS)


def known_template_ids() -> tuple[str, ...]:
    """Stable-sorted tuple of template ids the kernel has prompts for."""
    return tuple(sorted(_REGISTRY.keys()))


__all__ = [
    "DISCOVERY_QUESTION_KINDS",
    "DiscoveryQuestion",
    "DiscoveryQuestionKind",
    "GENERIC_DISCOVERY_QUESTIONS",
    "get_discovery_questions",
    "known_template_ids",
]
