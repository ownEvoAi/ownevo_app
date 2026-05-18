"""Typed shape for design-agent discovery questions.

A `DiscoveryQuestion` is a single conversational beat the design agent
runs against the operator before generation. Five kinds cover the
decision surfaces the design-agent posture targets:

  * `metric`    — surface a metric trade-off the operator owns an
                  opinion on (e.g. overstock cost vs. stockout cost).
                  Resolves what NL-gen would otherwise assume.
  * `ambiguity` — flag a semantic ambiguity in the description that
                  would generate different eval sets depending on
                  interpretation (e.g. "flag SKUs likely to need
                  markdown" — likely for stockout, slow-sell, or
                  seasonal-end?).
  * `trigger`   — when does the workflow run? Cadence (daily / weekly
                  / monthly), upstream event (new data, threshold
                  crossed), or on-demand. Shapes which trace slices
                  the regression gate replays against.
  * `surface`   — where does the operator see the output and act on
                  it? Queue, dashboard, ad-hoc report, or inside
                  another system (ERP/CRM/EMR). Determines what
                  "done" looks like for the eval cases.
  * `premise`   — push back on a stated assumption. Naming the
                  assumption explicitly makes it auditable; teams
                  often discover the assumption is the leverage point
                  of the workflow, not the metric.

The two original kinds (`metric`, `ambiguity`) carry the verbatim
demo-plan beats per template; the other three are exercised by the
generic fallback and become available to per-template prompt sets as
the design-agent posture extends.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

DiscoveryQuestionKind = Literal[
    "metric",
    "ambiguity",
    "trigger",
    "surface",
    "premise",
]

# Exported runtime tuple for tests + clients that need to enumerate the
# Literal. Stays in sync with the Literal automatically via get_args.
DISCOVERY_QUESTION_KINDS: tuple[str, ...] = get_args(DiscoveryQuestionKind)


class DiscoveryQuestion(BaseModel):
    """A single discovery-interview question rendered by the design agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: DiscoveryQuestionKind
    question: str = Field(min_length=1)
    options: tuple[str, ...] | None = None
    rationale: str | None = Field(
        default=None,
        description=(
            "One-sentence reason the agent is asking. Surfaced to the "
            "operator so the question reads as consultative rather than "
            "a generic form field."
        ),
    )
