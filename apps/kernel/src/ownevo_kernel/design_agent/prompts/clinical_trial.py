"""Clinical trial site selection — chief medical officer discovery prompts."""

from __future__ import annotations

from ._types import DiscoveryQuestion

DISCOVERY_QUESTIONS: tuple[DiscoveryQuestion, ...] = (
    DiscoveryQuestion(
        kind="metric",
        question=(
            "Recruitment speed vs. patient diversity — which weights "
            "more? FDA increasingly scrutinizes lack of diversity in "
            "Phase III oncology specifically. Three encodings: diversity "
            "as a hard floor (exclude sites below threshold), diversity "
            "as a weighted component of the score, or diversity as a "
            "tie-breaker only. Most modern protocols pick the hard floor."
        ),
        options=(
            "Diversity as a hard floor",
            "Diversity as a weighted component",
            "Diversity as a tie-breaker only",
        ),
        rationale=(
            "The speed-vs-diversity trade-off is where clinical "
            "operations and regulatory affairs collide. Encoding the "
            "resolution in the metric produces an audit trail for the "
            "deliberate choice."
        ),
    ),
    DiscoveryQuestion(
        kind="ambiguity",
        question=(
            "You said 'flag sites likely to under-recruit within 90 "
            "days' — under-recruit relative to your study protocol "
            "target, or under-recruit relative to historical recruitment "
            "for similar oncology Phase III studies at that site? These "
            "are very different eval sets. The first is contractual; the "
            "second is empirical. Most clinical operations teams want the "
            "empirical signal for site selection and the contractual "
            "signal for ongoing monitoring. Want both, or pick one for "
            "this workflow?"
        ),
        options=(
            "Protocol target (contractual)",
            "Historical baseline (empirical)",
            "Both, with different thresholds",
        ),
        rationale=(
            "Under-recruit baseline shifts the eval set between "
            "contractual and empirical regimes; the choice changes "
            "which sites the agent flags."
        ),
    ),
)
