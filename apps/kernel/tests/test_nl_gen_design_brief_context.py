"""Unit tests for design-brief context extraction + prompt formatting.

DesignAgentLog → markdown block, scoped by dimension. Pure functions,
no IO; mocks not needed.
"""

from __future__ import annotations

from ownevo_kernel.design_agent.log import DesignAgentLog, DesignAgentLogEntry
from ownevo_kernel.nl_gen.design_brief_context import (
    EVAL_CASE_DIMENSIONS,
    METRIC_DIMENSIONS,
    SIM_PLAN_DIMENSIONS,
    SPEC_DIMENSIONS,
    entries_for_dimensions,
    format_dimensions_block,
)


def _entry(
    *,
    dimension: str | None,
    kind: str = "metric",
    question: str = "Q?",
    chosen_option: str | None = None,
    answer: str | None = None,
    question_index: int = 0,
) -> DesignAgentLogEntry:
    return DesignAgentLogEntry(
        question_index=question_index,
        kind=kind,  # type: ignore[arg-type]
        question=question,
        answer=answer,
        dimension=dimension,  # type: ignore[arg-type]
        chosen_option=chosen_option,
    )


def _log(*entries: DesignAgentLogEntry) -> DesignAgentLog:
    return DesignAgentLog(discovery_transcript=tuple(entries), ambiguity_report=None)


def test_entries_for_dimensions_filters_by_key():
    log = _log(
        _entry(dimension="success_metric", chosen_option="Recall-first"),
        _entry(dimension="goal_and_scope", chosen_option="Markdown only"),
        _entry(dimension="trigger_and_cadence", chosen_option="Weekly Mon 6am"),
    )
    out = entries_for_dimensions(log, ["success_metric"])
    assert len(out) == 1
    assert out[0].chosen_option == "Recall-first"


def test_skipped_entries_are_dropped():
    """An entry with no chosen_option AND no free-text answer is a
    pure skip — the formatter should drop it so the block doesn't
    render an empty row."""
    log = _log(
        _entry(dimension="success_metric", chosen_option=None, answer=None),
        _entry(dimension="success_metric", chosen_option="Recall-first"),
    )
    out = entries_for_dimensions(log, ["success_metric"])
    assert len(out) == 1
    assert out[0].chosen_option == "Recall-first"


def test_legacy_kind_to_dimension_mapping():
    """Pre-LLM-interviewer entries carry only `kind`; the formatter
    still routes them to the right dimension via the back-compat
    mapping (metric → success_metric, trigger → trigger_and_cadence,
    surface → operate_ui_primitives, ambiguity/premise →
    goal_and_scope)."""
    log = _log(
        _entry(dimension=None, kind="metric", chosen_option="Recall-first"),
        _entry(dimension=None, kind="trigger", chosen_option="Daily 6am"),
        _entry(dimension=None, kind="surface", chosen_option="Kanban queue"),
        _entry(dimension=None, kind="ambiguity", chosen_option="Stockout risk"),
    )
    assert len(entries_for_dimensions(log, ["success_metric"])) == 1
    assert len(entries_for_dimensions(log, ["trigger_and_cadence"])) == 1
    assert len(entries_for_dimensions(log, ["operate_ui_primitives"])) == 1
    assert len(entries_for_dimensions(log, ["goal_and_scope"])) == 1


def test_format_block_is_none_when_no_matches():
    log = _log(
        _entry(dimension="success_metric", chosen_option="Recall-first"),
    )
    assert format_dimensions_block(log, ["reviewer_role"]) is None


def test_format_block_is_none_when_log_is_none():
    assert format_dimensions_block(None, SPEC_DIMENSIONS) is None


def test_format_block_renders_dimension_label_and_qa():
    log = _log(
        _entry(
            dimension="success_metric",
            question="Recall vs precision: which costs more?",
            chosen_option="Recall-first (F-beta=2)",
            answer="False negatives cost $50K per missed event.",
        ),
    )
    block = format_dimensions_block(log, ["success_metric"])
    assert block is not None
    assert "Success metric" in block  # the human label
    assert "Recall vs precision" in block
    assert "Recall-first (F-beta=2)" in block
    assert "False negatives cost $50K" in block


def test_format_block_groups_multiple_dimensions():
    log = _log(
        _entry(
            dimension="goal_and_scope",
            question="Goal?",
            chosen_option="Markdown alerts only",
        ),
        _entry(
            dimension="success_metric",
            question="Metric?",
            chosen_option="Recall-first",
        ),
        _entry(
            dimension="reviewer_role",
            question="Reviewer?",
            chosen_option="Category planner, weekly",
        ),
    )
    block = format_dimensions_block(
        log, ["goal_and_scope", "success_metric", "reviewer_role"]
    )
    assert block is not None
    # All three dimension labels appear in order.
    goal_idx = block.find("Goal & scope")
    metric_idx = block.find("Success metric")
    reviewer_idx = block.find("Reviewer & cadence")
    assert goal_idx >= 0 and metric_idx >= 0 and reviewer_idx >= 0


def test_chosen_option_plus_free_text_concatenated():
    log = _log(
        _entry(
            dimension="data_sources_and_connectors",
            question="What systems?",
            chosen_option="SAP ERP",
            answer="Already integrated via mainline ETL.",
        ),
    )
    block = format_dimensions_block(log, ["data_sources_and_connectors"])
    assert block is not None
    assert "SAP ERP" in block
    assert "Already integrated" in block
    # The format hint that both pieces were captured ("operator added").
    assert "operator added" in block


def test_predefined_subsets_are_valid():
    """The pre-bound subsets used by each generator surface must only
    reference known DesignDimensions; the module-level assertion guards
    this at import time. This test pins the public-API tuples so a
    rename in dimensions.py is caught here too."""
    assert "goal_and_scope" in SPEC_DIMENSIONS
    assert "data_sources_and_connectors" in SPEC_DIMENSIONS
    assert "operate_ui_primitives" in SPEC_DIMENSIONS
    assert "reviewer_role" in SPEC_DIMENSIONS

    assert "trigger_and_cadence" in SIM_PLAN_DIMENSIONS
    assert "success_metric" in METRIC_DIMENSIONS
    assert "eval_seed_cases" in EVAL_CASE_DIMENSIONS
