"""Typed shape for design-agent discovery questions.

A `DiscoveryQuestion` is a single conversational beat the design agent
runs against the operator before generation. The two kinds carry the
two beats from the demo plans:

  * `metric`    — surface a metric trade-off the operator owns an
                  opinion on (e.g. overstock cost vs. stockout cost).
                  Resolves what NL-gen would otherwise assume.
  * `ambiguity` — flag a semantic ambiguity in the description that
                  would generate different eval sets depending on
                  interpretation (e.g. "flag SKUs likely to need
                  markdown" — likely for stockout, slow-sell, or
                  seasonal-end?).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DiscoveryQuestionKind = Literal["metric", "ambiguity"]


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
