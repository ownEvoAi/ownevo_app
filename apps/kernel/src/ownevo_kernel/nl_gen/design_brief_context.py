"""Render a persisted DesignAgentLog into prompt-shaped context blocks.

Each NL-gen generator (spec / sim_plan / metric / eval_case_set)
reads a subset of the seven design-shaping dimensions. This module
walks a `DesignAgentLog`, picks the entries matching a given
dimension subset, and renders them as a markdown block suitable for
injection into the generator's user message.

The format mirrors what the LLM interviewer asked the operator —
question + chosen option + free-text elaboration — so the generator
sees enough context to treat the answer as load-bearing, not as
loose framing.

Back-compat:
  * Legacy log entries (pre-LLM-interviewer) carry no `dimension`
    field. They get a best-effort mapping via `_kind_to_dimension`:
    `metric → success_metric`, `trigger → trigger_and_cadence`,
    `surface → operate_ui_primitives`, `ambiguity/premise →
    goal_and_scope`. Renamed since the legacy taxonomy doesn't
    perfectly cover the new one — the mapping is best-effort, not
    canonical.
  * Empty subsets / skipped questions return `None` so the caller
    can skip the entire section header rather than emit a stub.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from ..design_agent.dimensions import DESIGN_DIMENSIONS, DesignDimension, spec_for
from ..design_agent.log import DesignAgentLog, DesignAgentLogEntry

if TYPE_CHECKING:
    pass


# Best-effort mapping for legacy entries that only carry the older
# `DiscoveryQuestionKind`. The new dimensions are richer (7) than the
# old kinds (5); ambiguity / premise both squash to scope. This is the
# same mapping the API route uses for the inverse direction.
_KIND_TO_DIMENSION: dict[str, DesignDimension] = {
    "metric": "success_metric",
    "trigger": "trigger_and_cadence",
    "surface": "operate_ui_primitives",
    "ambiguity": "goal_and_scope",
    "premise": "goal_and_scope",
}


def _entry_dimension(entry: DesignAgentLogEntry) -> DesignDimension | None:
    """Return the canonical dimension for an entry, walking the back-compat
    path when the new field is absent."""
    if entry.dimension is not None:
        return entry.dimension
    return _KIND_TO_DIMENSION.get(entry.kind)


def _is_answered(entry: DesignAgentLogEntry) -> bool:
    """An entry is "answered" if either a chosen option or free text was
    captured. Pure skips return False so the prompt block skips them."""
    has_option = bool((entry.chosen_option or "").strip())
    has_text = bool((entry.answer or "").strip())
    return has_option or has_text


def _format_answer(entry: DesignAgentLogEntry) -> str:
    """Format the operator's response for prompt injection.

    Prefers `chosen option · "free text"` when both are present, falls
    back to whichever is set. Skipped → `[skipped]`.
    """
    option = (entry.chosen_option or "").strip()
    text = (entry.answer or "").strip()
    if option and text:
        return f'{option} — operator added: "{text}"'
    if option:
        return option
    if text:
        return text
    return "[skipped]"


def entries_for_dimensions(
    log: DesignAgentLog | None,
    keys: Iterable[DesignDimension],
) -> list[DesignAgentLogEntry]:
    """Pick the answered entries that target any of `keys`.

    Order is preserved (transcript order). Pure skips are dropped so
    callers don't render empty `[skipped]` rows that add no signal.
    """
    if log is None:
        return []
    key_set = set(keys)
    out: list[DesignAgentLogEntry] = []
    for entry in log.discovery_transcript:
        if not _is_answered(entry):
            continue
        dim = _entry_dimension(entry)
        if dim is not None and dim in key_set:
            out.append(entry)
    return out


def format_dimensions_block(
    log: DesignAgentLog | None,
    keys: Sequence[DesignDimension],
    *,
    header: str = "Design-agent answers from the operator",
) -> str | None:
    """Render the entries targeting `keys` as a markdown block.

    Returns `None` when there are no answered entries — callers should
    conditionally include the result in their prompt rather than emit
    a header followed by nothing.

    Shape:
      ## <header>
      The operator answered the design-agent's interview before
      generation. Treat each answer below as a decision the operator
      has made — these constraints should be reflected in your output
      verbatim or as direct consequences (e.g. an answer locking
      recall-first → MetricDefinition.direction='maximize',
      family='recall').

      - **<DimensionLabel>**
        Q: <question>
        A: <chosen option · "free text">
      ...
    """
    entries = entries_for_dimensions(log, keys)
    if not entries:
        return None

    lines: list[str] = [f"## {header}"]
    lines.append(
        "The operator answered the design-agent's interview before "
        "generation. Each answer below is a decision the operator has "
        "made — treat the chosen option as a hard constraint and the "
        "free-text elaboration as fine-grained guidance. Reflect them "
        "in your tool call directly; do not re-litigate the choice."
    )
    lines.append("")
    for entry in entries:
        dim = _entry_dimension(entry)
        spec = spec_for(dim) if dim else None
        label = spec.label if spec else (dim or entry.kind).replace("_", " ").title()
        lines.append(f"- **{label}**")
        lines.append(f"  Q: {entry.question.strip()}")
        lines.append(f"  A: {_format_answer(entry)}")
    return "\n".join(lines)


# Pre-bound dimension subsets per generator surface.
# Each subset reflects the slice of the brief the generator can
# meaningfully encode in its output. Generators are free to read any
# subset; these defaults map to the canonical assignment in
# `design_agent.dimensions.DimensionSpec.informs`.

SPEC_DIMENSIONS: tuple[DesignDimension, ...] = (
    "goal_and_scope",
    "data_sources_and_connectors",
    "operate_ui_primitives",
    "reviewer_role",
)
"""Dimensions consumed by `generate_workflow_spec`. The spec is the
artifact that carries the workflow's goal, tools, data sources, UI
primitives, and reviewer — every dimension that shapes the spec lands
here."""

SIM_PLAN_DIMENSIONS: tuple[DesignDimension, ...] = (
    "goal_and_scope",
    "trigger_and_cadence",
)
"""Sim plan reads goal (to frame the simulation purpose) + trigger
cadence (to set step rhythm). The spec already carries data sources;
sim_plan doesn't need to re-read them."""

METRIC_DIMENSIONS: tuple[DesignDimension, ...] = (
    "goal_and_scope",
    "success_metric",
)
"""Metric definition reads the operator's metric choice + the goal it
should optimise for."""

EVAL_CASE_DIMENSIONS: tuple[DesignDimension, ...] = (
    "goal_and_scope",
    "eval_seed_cases",
    "success_metric",
)
"""Eval-case generation reads the operator's seed-case nominations,
the goal, and the metric direction (recall-gated workflows need more
positive cases than precision-gated ones)."""


# Sanity check at import time: every dimension referenced above is a
# known dimension. Catches a typo at module load rather than at
# runtime when the generator is mid-call.
for _subset in (
    SPEC_DIMENSIONS,
    SIM_PLAN_DIMENSIONS,
    METRIC_DIMENSIONS,
    EVAL_CASE_DIMENSIONS,
):
    for _key in _subset:
        if _key not in DESIGN_DIMENSIONS:
            raise AssertionError(
                f"design_brief_context references unknown dimension {_key!r}; "
                f"valid set: {sorted(DESIGN_DIMENSIONS)}"
            )


__all__ = [
    "EVAL_CASE_DIMENSIONS",
    "METRIC_DIMENSIONS",
    "SIM_PLAN_DIMENSIONS",
    "SPEC_DIMENSIONS",
    "entries_for_dimensions",
    "format_dimensions_block",
]
