"""UI primitives — typed discriminated union for the workflow render layer.

Implements the 8-variant primitive set defined in `packages/trace-format/SPEC.md`
§ "Workflow render primitives" and `ownevo_docs/ownEvo_MVP.md` § "Two-layer
primitive architecture".

The NL-gen pipeline (W3 Track A) emits a `WorkflowSpec.ui` block declaring
which primitives to render and how they're configured; the web app's render
layer reads the spec and picks the right component per primitive.

Same conventions as `agent_event.py`: discriminated union on `type`, frozen,
`extra="forbid"`, `UIPrimitiveAdapter` is the canonical dict→typed parser.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _UIPrimitiveBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MetricCards(_UIPrimitiveBase):
    """KPI tiles. Used for forecast accuracy, CSAT, $ at risk, etc."""

    type: Literal["MetricCards"]
    fields: list[str] = Field(min_length=1)


class TimeSeriesChart(_UIPrimitiveBase):
    """Time-series. Demand forecast, ticket volume, lift charts."""

    type: Literal["TimeSeriesChart"]
    x: str
    y: list[str] = Field(min_length=1)
    group_by: str | None = None


class TableView(_UIPrimitiveBase):
    """Tabular list. SKU list, ticket queue, line items."""

    type: Literal["TableView"]
    source: str
    columns: list[str] = Field(min_length=1)


class AlertList(_UIPrimitiveBase):
    """Markdown alerts, escalations, clause flags."""

    type: Literal["AlertList"]
    source: str
    severity_field: str = "severity"
    title_field: str = "title"


class KanbanBoard(_UIPrimitiveBase):
    """Tickets by status, deals by stage."""

    type: Literal["KanbanBoard"]
    source: str
    column_field: str
    card_title_field: str


class ScheduleGrid(_UIPrimitiveBase):
    """2-D resource × time grid with cell-level status.

    Shift schedules, content calendars, capacity boards, room booking,
    on-call rotations — anything keyed `resource × time` with a status
    badge per cell.
    """

    type: Literal["ScheduleGrid"]
    rows_source: str
    cols_source: str
    cells_source: str


class ConversationView(_UIPrimitiveBase):
    """Multi-turn agent transcript with citations."""

    type: Literal["ConversationView"]
    trace_source: str


class SideBySideView(_UIPrimitiveBase):
    """Contract clause + redline, document diff."""

    type: Literal["SideBySideView"]
    left_source: str
    right_source: str
    diff_mode: Literal["text", "json"] = "text"


class DocumentReader(_UIPrimitiveBase):
    """Contracts, policies, runbooks with margin annotations."""

    type: Literal["DocumentReader"]
    source: str
    annotations_source: str | None = None


UIPrimitive = Annotated[
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
"""Discriminated union over the 9 workflow render primitives."""


UIPrimitiveAdapter: TypeAdapter[UIPrimitive] = TypeAdapter(UIPrimitive)
"""Canonical entry point for parsing dict → typed primitive."""


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
    "UIPrimitive",
    "UIPrimitiveAdapter",
]
