"""Generic fallback discovery prompts for free-form workflow descriptions.

Used when the operator skips a vertical template and writes a free-form
description. The questions are intentionally domain-agnostic and target
the two ambiguities that hurt every workflow regardless of domain.
"""

from __future__ import annotations

from ._types import DiscoveryQuestion

DISCOVERY_QUESTIONS: tuple[DiscoveryQuestion, ...] = (
    DiscoveryQuestion(
        kind="metric",
        question=(
            "When the agent has to choose, should it lean toward more "
            "flags (higher recall, more false positives reviewed) or "
            "fewer flags (higher precision, more misses)? In your "
            "operational context, what is the cost ratio between a "
            "missed case and a false flag?"
        ),
        options=(
            "More flags (recall over precision)",
            "Fewer flags (precision over recall)",
            "Balanced",
        ),
        rationale=(
            "Recall vs. precision is the dominant metric trade-off for "
            "any flagging or classification workflow. Surfacing it "
            "before generation avoids an implicit default."
        ),
    ),
    DiscoveryQuestion(
        kind="ambiguity",
        question=(
            "Walk me through the operational moment your reviewer sees "
            "the agent's output — is it a daily queue, a weekly batch, "
            "or an on-demand check? The cadence changes which evidence "
            "the eval set should pin against."
        ),
        rationale=(
            "Review cadence determines the temporal slice the eval "
            "cases should sample from. Without it NL-gen picks a "
            "default that may not match operations."
        ),
    ),
)
