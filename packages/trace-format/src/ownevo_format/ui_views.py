"""UI views — typed discriminated union for the workflow render layer.

Implements the 9-variant view set defined in `packages/trace-format/SPEC.md`
§ "Workflow render views".

The NL-gen pipeline (W3 Track A) emits a `WorkflowSpec.ui` block declaring
which views to render and how they're configured; the web app's render
layer reads the spec and picks the right component per view.

Same conventions as `agent_event.py`: discriminated union on `type`, frozen,
`extra="forbid"`, `UIViewAdapter` is the canonical dict→typed parser.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _UIViewBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MetricCards(_UIViewBase):
    """KPI tiles. Used for forecast accuracy, CSAT, $ at risk, etc."""

    type: Literal["MetricCards"]
    fields: list[str] = Field(min_length=1)


class TimeSeriesChart(_UIViewBase):
    """Time-series. Demand forecast, ticket volume, lift charts."""

    type: Literal["TimeSeriesChart"]
    x: str
    y: list[str] = Field(min_length=1)
    group_by: str | None = None


class TableView(_UIViewBase):
    """Tabular list. SKU list, ticket queue, line items."""

    type: Literal["TableView"]
    source: str
    columns: list[str] = Field(min_length=1)


class AlertList(_UIViewBase):
    """Markdown alerts, escalations, clause flags."""

    type: Literal["AlertList"]
    source: str
    severity_field: str = "severity"
    title_field: str = "title"


class KanbanBoard(_UIViewBase):
    """Tickets by status, deals by stage."""

    type: Literal["KanbanBoard"]
    source: str
    column_field: str
    card_title_field: str


class ScheduleGrid(_UIViewBase):
    """2-D resource × time grid with cell-level status.

    Shift schedules, content calendars, capacity boards, room booking,
    on-call rotations — anything keyed `resource × time` with a status
    badge per cell.
    """

    type: Literal["ScheduleGrid"]
    rows_source: str
    cols_source: str
    cells_source: str


class ConversationView(_UIViewBase):
    """Multi-turn agent transcript with citations."""

    type: Literal["ConversationView"]
    trace_source: str


class SideBySideView(_UIViewBase):
    """Contract clause + redline, document diff."""

    type: Literal["SideBySideView"]
    left_source: str
    right_source: str
    diff_mode: Literal["text", "json"] = "text"


class DocumentReader(_UIViewBase):
    """Contracts, policies, runbooks with margin annotations."""

    type: Literal["DocumentReader"]
    source: str
    annotations_source: str | None = None


UIView = Annotated[
    (
        MetricCards
        | TimeSeriesChart
        | TableView
        | AlertList
        | KanbanBoard
        | ScheduleGrid
        | ConversationView
        | SideBySideView
        | DocumentReader
    ),
    Field(discriminator="type"),
]
"""Discriminated union over the 9 workflow render views."""


UIViewAdapter: TypeAdapter[UIView] = TypeAdapter(UIView)
"""Canonical entry point for parsing dict → typed view."""


__all__ = [
    "MetricCards",
    "TimeSeriesChart",
    "TableView",
    "AlertList",
    "KanbanBoard",
    "ScheduleGrid",
    "ConversationView",
    "SideBySideView",
    "DocumentReader",
    "UIView",
    "UIViewAdapter",
]
