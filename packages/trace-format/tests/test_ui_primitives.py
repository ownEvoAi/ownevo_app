"""Tests for the UI primitive discriminated union.

Mirrors test_agent_event.py — every variant round-trips, the discriminator
rejects unknown types, defaults are populated, frozen models reject mutation.
"""

from __future__ import annotations

import pytest
from ownevo_format import (
    AlertList,
    ConversationView,
    DocumentReader,
    KanbanBoard,
    MetricCards,
    SideBySideView,
    TableView,
    TimeSeriesChart,
    UIPrimitiveAdapter,
)
from pydantic import ValidationError


def test_metric_cards_round_trip():
    p = MetricCards(type="MetricCards", fields=["accuracy", "count"])
    rebuilt = UIPrimitiveAdapter.validate_python(p.model_dump())
    assert isinstance(rebuilt, MetricCards)
    assert rebuilt == p


def test_time_series_chart_with_group_by():
    p = TimeSeriesChart(
        type="TimeSeriesChart", x="week", y=["forecast", "actual"], group_by="region"
    )
    rebuilt = UIPrimitiveAdapter.validate_python(p.model_dump())
    assert isinstance(rebuilt, TimeSeriesChart)
    assert rebuilt.group_by == "region"


def test_alert_list_uses_default_severity_and_title_fields():
    p = AlertList(type="AlertList", source="alerts")
    assert p.severity_field == "severity"
    assert p.title_field == "title"


def test_kanban_board_requires_column_and_title_fields():
    with pytest.raises(ValidationError):
        KanbanBoard.model_validate({"type": "KanbanBoard", "source": "x"})


def test_side_by_side_default_diff_mode_is_text():
    p = SideBySideView(type="SideBySideView", left_source="a", right_source="b")
    assert p.diff_mode == "text"


def test_document_reader_annotations_optional():
    p = DocumentReader(type="DocumentReader", source="contracts")
    assert p.annotations_source is None


def test_conversation_view_requires_trace_source():
    with pytest.raises(ValidationError):
        ConversationView.model_validate({"type": "ConversationView"})


def test_discriminator_rejects_unknown_type():
    with pytest.raises(ValidationError):
        UIPrimitiveAdapter.validate_python(
            {"type": "MysteryWidget", "source": "x"}
        )


def test_table_view_requires_columns():
    with pytest.raises(ValidationError):
        TableView.model_validate({"type": "TableView", "source": "x", "columns": []})


def test_metric_cards_rejects_extra_field():
    with pytest.raises(ValidationError):
        MetricCards.model_validate(
            {"type": "MetricCards", "fields": ["x"], "junk": True}
        )


def test_models_are_frozen():
    p = MetricCards(type="MetricCards", fields=["x"])
    with pytest.raises(ValidationError):
        p.fields = ["y"]  # type: ignore[misc]
