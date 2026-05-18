"""Generic fallback discovery prompts for free-form workflow descriptions.

Used when the operator skips a vertical template and writes a free-form
description. The questions are intentionally domain-agnostic and target
the five decision surfaces of the design-agent posture (`metric`,
`ambiguity`, `trigger`, `surface`, `premise`) so the generic set
exercises every `DiscoveryQuestionKind` variant — per-template prompt
sets can opt into the additional kinds as the posture extends.
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
    DiscoveryQuestion(
        kind="trigger",
        question=(
            "What kicks the workflow off — a fixed schedule, an "
            "upstream event (new data lands, a threshold is crossed), "
            "or an operator clicking 'run' on demand? The trigger "
            "shapes which trace slices the regression gate replays."
        ),
        options=(
            "Recurring schedule",
            "Upstream event",
            "On-demand by reviewer",
        ),
        rationale=(
            "Trigger semantics decide the temporal granularity of the "
            "eval set. A scheduled monthly workflow needs different "
            "regression coverage than an event-driven one."
        ),
    ),
    DiscoveryQuestion(
        kind="surface",
        question=(
            "Where does the reviewer act on the agent's output today — "
            "an inbox queue, a dashboard, an emailed report, or "
            "directly inside another system (ERP, CRM, EMR)? The "
            "surface defines what 'done' looks like for the operator."
        ),
        options=(
            "Inbox queue",
            "Dashboard",
            "Report or email",
            "Inside another system",
        ),
        rationale=(
            "Output surface determines which acceptance signal the "
            "eval cases should pin to (queue clear, dashboard tile "
            "green, downstream system updated)."
        ),
    ),
    DiscoveryQuestion(
        kind="premise",
        question=(
            "Name one assumption baked into your description that you "
            "would defend if I pushed back on it. Teams often discover "
            "the assumption is the real leverage point of the workflow, "
            "not the metric."
        ),
        rationale=(
            "Eliciting the premise explicitly records it in the audit "
            "chain. The design agent treats every stated premise as "
            "negotiable before the loop locks the WorkflowSpec."
        ),
    ),
)
