"""Ambiguity-detection pass over a generated WorkflowSpec.

Two-pass analysis the design agent runs after NL-gen returns, before
the loop starts spending tokens on iterations:

  * **Pass A — inferred-artifact scan.** Walk the spec's per-artifact
    `Provenance` records. Every artifact with `kind == "inferred"` is a
    NL-gen guess — the design agent flags it so the operator can confirm
    or correct before the eval set crystallises.

  * **Pass B — description / metric conflict scan.** A small rule-set
    over the workflow description + `MetricDefinition` that catches the
    canonical contradictions buyers state under pressure
    ("maximize recall, zero false positives", "maximize the score but
    do not change the model"). The LLM judge variant from PLAN.md 9.1.3
    is its own slice — the rule-set today is a deterministic floor that
    catches the cases the LLM judge will later expand on.

Both passes return `AmbiguityFinding`s carrying the same shape so the
web layer can render them uniformly as additional questions in the
design-agent conversation.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..nl_gen.metric_def import MetricDefinition
from ..nl_gen.spec import WorkflowSpec

AmbiguityKind = Literal["inferred-artifact", "conflict"]
AmbiguitySeverity = Literal["low", "medium", "high"]

# Stable severity ordering for sort — high first.
_SEVERITY_RANK: dict[AmbiguitySeverity, int] = {"high": 0, "medium": 1, "low": 2}


class AmbiguityFinding(BaseModel):
    """One actionable ambiguity the design agent should surface."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: AmbiguityKind
    severity: AmbiguitySeverity
    location: str = Field(
        min_length=1,
        description=(
            "Human-readable path into the spec or metric — e.g. "
            "`tools.flag_markdown_risk`, `reviewer`, `metric.direction`. "
            "Surfaced in the audit chain so a future reader can find "
            "what was flagged."
        ),
    )
    summary: str = Field(
        min_length=1,
        description="One-line headline. Reads as a card title in the UI.",
    )
    suggested_question: str = Field(
        min_length=1,
        description=(
            "What the design agent should ask the operator to resolve "
            "the finding. Surfaces verbatim as the next question if the "
            "operator opts to address ambiguities before generating."
        ),
    )


class AmbiguityReport(BaseModel):
    """The collected output of the ambiguity-detection pass."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_spec_id: str = Field(min_length=1)
    findings: tuple[AmbiguityFinding, ...] = Field(default_factory=tuple)
    high_severity_count: int = Field(default=0, init=False)

    @model_validator(mode="after")
    def _compute_high_severity_count(self) -> "AmbiguityReport":
        object.__setattr__(
            self,
            "high_severity_count",
            sum(1 for f in self.findings if f.severity == "high"),
        )
        return self


# ---------------------------------------------------------------------------
# Pass A — inferred-artifact scan (deterministic)
# ---------------------------------------------------------------------------

# `inferred` provenance means NL-gen named a domain pattern rather than
# quoting the user. That is not wrong — just unconfirmed — so the default
# severity is medium, not high.
_INFERRED_SEVERITY: AmbiguitySeverity = "medium"


def find_inferred_artifacts(spec: WorkflowSpec) -> tuple[AmbiguityFinding, ...]:
    """Walk the spec's provenance fields and flag every `inferred` artifact.

    Pure function — no IO. Order matches the spec's walk order so the
    same input always produces the same finding sequence (load-bearing
    for the test snapshot).
    """
    findings: list[AmbiguityFinding] = []

    # Tools
    for tool in spec.tools:
        if tool.provenance is not None and tool.provenance.kind == "inferred":
            findings.append(
                AmbiguityFinding(
                    kind="inferred-artifact",
                    severity=_INFERRED_SEVERITY,
                    location=f"tools.{tool.name}",
                    summary=(
                        f"Tool '{tool.name}' was inferred from a domain "
                        f"pattern, not quoted from the description"
                    ),
                    suggested_question=(
                        f"I added the tool '{tool.name}' because the "
                        f"description matches the {tool.provenance.source} "
                        f"pattern. Is that tool actually in scope here?"
                    ),
                )
            )

    # Personas
    for persona in spec.environment.personas:
        if persona.provenance is not None and persona.provenance.kind == "inferred":
            findings.append(
                AmbiguityFinding(
                    kind="inferred-artifact",
                    severity=_INFERRED_SEVERITY,
                    location=f"environment.personas.{persona.role}",
                    summary=(
                        f"Simulated persona '{persona.role}' was inferred "
                        f"from a domain pattern"
                    ),
                    suggested_question=(
                        f"I am modelling a '{persona.role}' interacting "
                        f"with the agent because the description matches "
                        f"the {persona.provenance.source} pattern. Does "
                        f"that persona exist in your operation?"
                    ),
                )
            )

    # Data sources
    for ds in spec.environment.data_sources:
        if ds.provenance is not None and ds.provenance.kind == "inferred":
            findings.append(
                AmbiguityFinding(
                    kind="inferred-artifact",
                    severity=_INFERRED_SEVERITY,
                    location=f"environment.data_sources.{ds.id}",
                    summary=(
                        f"Data source '{ds.id}' was inferred, not quoted "
                        f"from the description"
                    ),
                    suggested_question=(
                        f"I assumed the agent reads from '{ds.id}' "
                        f"because of the {ds.provenance.source} pattern. "
                        f"Is that the actual source, or something else?"
                    ),
                )
            )

    # Env generators
    for gen in spec.environment.env_generators:
        if gen.provenance is not None and gen.provenance.kind == "inferred":
            findings.append(
                AmbiguityFinding(
                    kind="inferred-artifact",
                    severity=_INFERRED_SEVERITY,
                    location=f"environment.env_generators.{gen.name}",
                    summary=(
                        f"Env generator '{gen.name}' was inferred from "
                        f"a domain pattern"
                    ),
                    suggested_question=(
                        f"The simulator will generate '{gen.name}' "
                        f"because the description matches the "
                        f"{gen.provenance.source} pattern. Is that "
                        f"realistic for your data?"
                    ),
                )
            )

    # Reviewer
    if (
        spec.reviewer.provenance is not None
        and spec.reviewer.provenance.kind == "inferred"
    ):
        findings.append(
            AmbiguityFinding(
                kind="inferred-artifact",
                severity="high",
                location="reviewer",
                summary=(
                    f"Reviewer role '{spec.reviewer.role}' was inferred, "
                    f"not quoted from the description"
                ),
                suggested_question=(
                    f"I assumed '{spec.reviewer.role}' reviews the "
                    f"agent's output because of the "
                    f"{spec.reviewer.provenance.source} pattern. Who "
                    f"actually owns this review step in your operation?"
                ),
            )
        )

    return tuple(findings)


# ---------------------------------------------------------------------------
# Pass B — description / metric conflict scan (rule-based)
# ---------------------------------------------------------------------------

# Phrasings that name a hard-recall ask. The buyer is asking for "do not
# miss". When combined with a hard-precision ask the metric needs an
# explicit trade-off.
_HARD_RECALL_PATTERNS = (
    r"\bmax(imi[sz]e)?\s+recall\b",
    r"\b(no|zero|0)\s+(false\s+negatives|missed\s+(cases?|positives?|detections?|classifications?|events?))\b",
    r"\b(never\s+miss(es|ed)?\s+a)\b",
    r"\bcatch\s+every\s+(\w+\s+)?(case|fraud|default|incident|transaction|alert|event|miss|positives?|negatives?)\b",
)

# Phrasings that name a hard-precision ask: "do not bother the operator
# with a false alarm".
_HARD_PRECISION_PATTERNS = (
    r"\bmax(imi[sz]e)?\s+precision\b",
    r"\b(no|zero|0)\s+(false\s+positives|false\s+alarms|wrong\s+flags)\b",
    # Negative lookahead prevents matching "only flag real-time" (hyphenated compound).
    r"\bonly\s+flag\s+(real|true|confirmed)(?!-)\b",
)

# "Don't change the model" vs "maximize the score" is the second canonical
# contradiction — the loop's whole pitch is changing the model.
_NO_CHANGE_PATTERNS = (
    r"\b(do\s+not|don'?t|never|must\s+not)\s+(change|modify|alter|edit|touch)\s+the\s+(model|prompt|agent)\b",
    # Anchor to model/prompt/agent to avoid firing on schema/format stability constraints.
    r"\b(the\s+)?(model|prompt|agent|instructions?)\s+must\s+stay\s+the\s+same\b",
)


def _matches_any(text: str, patterns: tuple[str, ...]) -> str | None:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def find_description_conflicts(
    description: str,
) -> tuple[AmbiguityFinding, ...]:
    """Run the rule-based conflict scan over the description text.

    Returns findings without firing on metric internals — the metric
    cross-checks live in `find_metric_direction_conflicts`. Splitting
    the two keeps each rule's blast radius small and the test failures
    legible.
    """
    findings: list[AmbiguityFinding] = []

    recall_hit = _matches_any(description, _HARD_RECALL_PATTERNS)
    precision_hit = _matches_any(description, _HARD_PRECISION_PATTERNS)
    if recall_hit and precision_hit:
        findings.append(
            AmbiguityFinding(
                kind="conflict",
                severity="high",
                location="description",
                summary=(
                    "Description asks for both 'no misses' and 'no false "
                    "alarms' — those are competing goals, pick one"
                ),
                suggested_question=(
                    f"You said both '{recall_hit}' and '{precision_hit}'. "
                    f"In your operation, which mistake is more costly: "
                    f"missing a true case, or surfacing a false alarm? "
                    f"The metric needs to weight one over the other."
                ),
            )
        )

    no_change_hit = _matches_any(description, _NO_CHANGE_PATTERNS)
    if no_change_hit is not None:
        findings.append(
            AmbiguityFinding(
                kind="conflict",
                severity="high",
                location="description",
                summary=(
                    "Description says the agent should not change — but "
                    "the improvement loop only works by proposing changes"
                ),
                suggested_question=(
                    f"You said '{no_change_hit}'. The improvement loop "
                    f"proposes edits to the agent's instructions or "
                    f"skills. Is the 'no change' constraint about the "
                    f"underlying model weights (which we never touch), "
                    f"or about the prompt and skills (which we do)?"
                ),
            )
        )

    return tuple(findings)


def find_metric_direction_conflicts(
    spec: WorkflowSpec,
    metric_definition: MetricDefinition | None,
) -> tuple[AmbiguityFinding, ...]:
    """Cross-check the metric's `direction` against the spec's
    `success_criterion.direction`.

    The kernel validates this at metric-compute time too, but surfacing
    it as an ambiguity here lets the design agent ask before the loop
    runs rather than after it crashes.
    """
    if metric_definition is None:
        return ()
    if metric_definition.direction == spec.success_criterion.direction:
        return ()
    return (
        AmbiguityFinding(
            kind="conflict",
            severity="high",
            location="metric.direction",
            summary=(
                f"Metric direction is '{metric_definition.direction}' but "
                f"the spec's success_criterion says "
                f"'{spec.success_criterion.direction}'"
            ),
            suggested_question=(
                f"The metric '{metric_definition.name}' is set to "
                f"{metric_definition.direction}, but the workflow's "
                f"success criterion says we should "
                f"{spec.success_criterion.direction}. Which one is "
                f"correct? The gate uses the metric direction; if it "
                f"is wrong the loop will optimise the wrong way."
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def analyze_workflow(
    description: str,
    spec: WorkflowSpec,
    metric_definition: MetricDefinition | None = None,
) -> AmbiguityReport:
    """Run every pass and return the combined report.

    Deterministic and fast — no IO, no LLM call. Suitable for invoking
    inline from the API endpoint that serves the design-agent UX.
    """
    findings: list[AmbiguityFinding] = []
    findings.extend(find_inferred_artifacts(spec))
    findings.extend(find_description_conflicts(description))
    findings.extend(find_metric_direction_conflicts(spec, metric_definition))

    findings.sort(key=lambda f: _SEVERITY_RANK[f.severity])

    return AmbiguityReport(
        workflow_spec_id=spec.id,
        findings=tuple(findings),
    )


