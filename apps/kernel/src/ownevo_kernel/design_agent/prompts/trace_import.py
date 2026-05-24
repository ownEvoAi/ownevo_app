"""Trace-import discovery prompts.

Used when the design agent runs at trace-import time rather than at
authoring time. The starting material is fundamentally different:

  * authoring-time → operator writes a free-form description of what
    they want; questions phrased as "describe X" / "which trade-off
    dominates".
  * trace-import → an imported trace + (optionally) an exported agent
    definition is already on the table. The agent has been running
    somewhere already. Questions are phrased "I see this agent does X;
    what should success look like" rather than "describe the workflow".

The five `DiscoveryQuestionKind`s carry over unchanged — what differs
is the conversational stance. The opening framing is observational
("I see…", "your trace shows…") rather than interrogative.

Per the trace-import design surface, this set must include at least
one metric-negotiation question and one ambiguity-surfacing question;
the generic kinds (`trigger`, `surface`, `premise`) are covered too so
the conversation can reach the same decision surface as the
authoring-time interview without forcing the operator to repeat what
the trace already shows.
"""

from __future__ import annotations

from ._types import DiscoveryQuestion

DISCOVERY_QUESTIONS: tuple[DiscoveryQuestion, ...] = (
    DiscoveryQuestion(
        kind="metric",
        question=(
            "I see this agent runs and produces outputs your reviewers "
            "act on. What does success look like for one of those runs — "
            "is it more about catching every relevant case (recall), "
            "keeping false flags low (precision), or hitting a specific "
            "downstream business number (e.g. fewer stockouts, fewer "
            "missed reviews)? If you can name the dominant cost — the "
            "thing that hurts most when the agent gets it wrong — I can "
            "weight the eval set to track that signal first."
        ),
        options=(
            "Catch every relevant case (recall)",
            "Keep false flags low (precision)",
            "Hit a specific downstream business number",
            "Balanced",
        ),
        rationale=(
            "Trace-import skips the description step, so the metric "
            "trade-off has to be elicited explicitly — otherwise the "
            "generated eval set inherits whatever implicit signal the "
            "agent's prior author baked in."
        ),
    ),
    DiscoveryQuestion(
        kind="ambiguity",
        question=(
            "Looking at the imported trace, I can see the agent takes "
            "action — but I cannot tell from the trace alone whether "
            "the action means 'flag for human review', 'auto-apply the "
            "change', or 'notify and wait'. Which one matches the "
            "operational moment you want the eval cases to pin against?"
        ),
        options=(
            "Flag for human review",
            "Auto-apply the change",
            "Notify and wait",
        ),
        rationale=(
            "Trace events show what the agent did, not what 'done' "
            "meant operationally. Disambiguating the operator-facing "
            "semantic up front prevents the eval set from pinning to "
            "the wrong trajectory step."
        ),
    ),
    DiscoveryQuestion(
        kind="premise",
        question=(
            "If I summarised what this agent appears to do today as a "
            "single sentence — 'agent X does Y when Z' — what part of "
            "that sentence would you push back on? Teams importing an "
            "existing agent often discover the implementation drifted "
            "from intent; naming the drift explicitly is the cheapest "
            "way to catch it before the loop starts proposing fixes."
        ),
        rationale=(
            "The reverse-discovery summary is auditable but only useful "
            "if the reviewer is invited to correct it. Treating the "
            "summary as negotiable from the first turn keeps the audit "
            "chain honest about what the imported agent actually does."
        ),
    ),
    DiscoveryQuestion(
        kind="trigger",
        question=(
            "Your trace covers a window of runs. Were those runs kicked "
            "off on a fixed schedule, by an upstream event (new data "
            "lands, a threshold is crossed), or on-demand by a reviewer? "
            "The trigger pattern determines which trace slices the "
            "regression gate replays the proposed fix against."
        ),
        options=(
            "Recurring schedule",
            "Upstream event",
            "On-demand by reviewer",
            "Mixed",
        ),
        rationale=(
            "Trigger semantics decide the temporal granularity of the "
            "replay slice. Scheduled runs and event-driven runs need "
            "different regression-coverage shapes even on identical "
            "traces."
        ),
    ),
    DiscoveryQuestion(
        kind="surface",
        question=(
            "Where does the reviewer see this agent's output today — "
            "inside the platform that emitted the trace (Copilot Studio, "
            "LangSmith), an inbox queue, a dashboard, or directly inside "
            "another system (ERP, CRM, EMR)? The surface defines what "
            "'done' looks like for the eval cases and where an approved "
            "fix has to be shipped back to."
        ),
        options=(
            "The source platform (Copilot Studio / LangSmith)",
            "Inbox queue",
            "Dashboard",
            "Report or email",
            "Inside another system",
        ),
        rationale=(
            "Trace import already tells us the upstream platform; "
            "asking the operator where the output is consumed makes "
            "the downstream-fix delivery path explicit (push_prompt vs. "
            "plain-language diff vs. manual)."
        ),
    ),
)
