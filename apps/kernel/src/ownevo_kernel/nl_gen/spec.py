"""WorkflowSpec — typed schema for the NL-gen output (A3.1).

Source-of-truth for what the natural-language workflow generator produces from
a plain-English description. Stored as JSONB in `workflows.spec` (see
`apps/kernel/migrations/0001_substrate.sql:94`).

**Current schema version: "1.1"** (v1.0 frozen at A3.4 / end of W3 2026-05-04;
v1.1 added ScheduleGrid at W8 Track 0 2026-05-11). Structural changes are
caught by `tests/test_nl_gen_schema_freeze.py` against the snapshot at
`nl_gen/schemas/workflow_spec.v1.1.json`. Any diff requires an explicit
version bump (1.x → 2.0 if breaking, 1.x → 1.y if additive) and a W7 UI
re-test before the snapshot is regenerated.

The mock at `www/preview/s26-rk7p3/04-new-workflow-step2.html` is the rendering
target — every field here corresponds to a section the user sees on the
"Review what we'll build" page.

Downstream consumers:
  * A3.2 sim_generator       — reads environment.{entities, env_generators,
                                personas, data_sources}, tools
  * A4.1 eval_generator      — reads known_past_misses, entities, tools
  * A4.2 metric_generator    — reads success_criterion, tools
  * Web UI (W7)              — reads everything; renders ui block per
                                ui_primitives.UIPrimitive
"""

from __future__ import annotations

from typing import Literal

from ownevo_format import UIPrimitive
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.1"
"""Frozen at v1.0 — 2026-05-04.

v1.0 → v1.1 (2026-05-11): added `ScheduleGrid` to the
`UIPrimitive` discriminated union (9 primitives, was 8) to close the
parity gap with `www/preview/s26-rk7p3/27-primitives.html`. Additive
change — every v1.0 spec validates under v1.1; no `Literal` union was
narrowed.

Structural drift is detected by `tests/test_nl_gen_schema_freeze.py`
against the snapshot at `nl_gen/schemas/workflow_spec.v1.1.json`. To
intentionally change the schema, bump this constant + regenerate the
snapshot via `scripts/regen_nl_gen_schemas.py` and re-test the W7 UI."""

Domain = Literal[
    "supply-chain",
    "credit-risk",
    "legal",
    "support",
    "labour",
    "other",
]
"""Top-level domain. Drives prompt steering + UI primitive defaults."""

FieldType = Literal["string", "int", "float", "bool", "date", "datetime", "category"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Provenance(_Base):
    """How an artifact links back to the user's description.

    Mock parity: `04-new-workflow-step2.html` shows
    "derived from <em>"<phrase>"</em>" (kind="derived") or
    "inferred from <em>supply chain forecasting domain pattern</em>"
    (kind="inferred"). The demo's load-bearing feature — every artifact
    traces back to either a quoted phrase from the user or a named domain
    pattern.
    """

    kind: Literal["derived", "inferred"]
    source: str = Field(min_length=1)


class EntityField(_Base):
    """One named, typed field on an Entity."""

    name: str = Field(min_length=1)
    type: FieldType
    description: str = ""


class Entity(_Base):
    """A first-class thing the agent reads or manipulates (sku, ticket, contract)."""

    name: str = Field(min_length=1)
    description: str = ""
    fields: list[EntityField] = Field(default_factory=list)


class DataSource(_Base):
    """An external system the agent pulls from (SAP ERP, NOAA, Salesforce).

    In production these are live integrations; in the simulator (A3.2) they're
    deterministic replayers.
    """

    id: str = Field(min_length=1)
    description: str = ""
    entity: str | None = None
    provenance: Provenance | None = None


class EnvGenerator(_Base):
    """A data-generating component of the simulator.

    Distinct from DataSource: data sources are external systems with fixed
    schemas; env generators synthesize realistic data (synthetic catalogs,
    supplier behaviour, weather anomalies). The sim generator (A3.2) emits
    one Python module per env_generator.
    """

    name: str = Field(min_length=1)
    description: str = ""
    provenance: Provenance | None = None


class Persona(_Base):
    """A simulated human interacting with the agent.

    Mock parity: 04-new-workflow-step2.html § "User behaviour (simulated)" —
    e.g., "Supply chain analyst · Monday morning markdown review",
    "Supply Chain VP · daily alert triage". `name` is optional because the
    user's description usually says "the supply chain VP" not "Maria"; the
    demo workspace populates names elsewhere (see ownEvo_MVP_mocks.md).

    `cadence` is free-form prose ("daily", "Monday 9:00 PT", "per-incident");
    A3.2 reads it to schedule sim interactions.
    """

    role: str = Field(min_length=1)
    name: str | None = None
    cadence: str = ""
    description: str = ""
    provenance: Provenance | None = None


class WorkflowEnvironment(_Base):
    """What the agent operates on.

    The sim generator (A3.2) emits one Python module per element: an
    entity-store stub, a data-source replayer per data_source, a generator
    module per env_generator, a behaviour script per persona.
    """

    entities: list[Entity] = Field(default_factory=list)
    data_sources: list[DataSource] = Field(default_factory=list)
    env_generators: list[EnvGenerator] = Field(default_factory=list)
    personas: list[Persona] = Field(default_factory=list)
    seasonality: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_names(self) -> WorkflowEnvironment:
        for label, items, key in [
            ("entities", self.entities, lambda e: e.name),
            ("data_sources", self.data_sources, lambda d: d.id),
            ("env_generators", self.env_generators, lambda g: g.name),
        ]:
            names = [key(i) for i in items]  # type: ignore[operator]
            if len(names) != len(set(names)):
                dupes = {n for n in names if names.count(n) > 1}
                raise ValueError(f"{label} contains duplicate names: {dupes}")
        return self


class ToolParam(_Base):
    name: str = Field(min_length=1)
    type: FieldType
    description: str = ""
    required: bool = True


class AgentTool(_Base):
    """A tool the agent calls. Becomes a tool_use definition at runtime
    and a Python function stub in the generated sim module.
    """

    name: str = Field(min_length=1)
    description: str = ""
    inputs: list[ToolParam] = Field(default_factory=list)
    outputs: list[ToolParam] = Field(default_factory=list)
    provenance: Provenance | None = None


class ReviewerSpec(_Base):
    """Who reviews the agent's outputs and on what cadence.

    Drives the approval-queue UI framing and the audit-trail attribution
    ("approved by <reviewer.role>"). Distinct from Persona because the
    reviewer is a real human approver, not a simulated user.

    `provenance` is optional and consistent with other artifact types —
    the reviewer is usually named directly in the user's description
    ("the supply chain VP reviews ..."), and tracking that lineage shows
    up in the same demo surface.
    """

    role: str = Field(min_length=1)
    cadence: str = ""
    description: str = ""
    provenance: Provenance | None = None


class SuccessCriterionStub(_Base):
    """A3.1 emits this stub. A4.2 reads it and generates the full metric
    definition (formula, weights, per-case computation).

    `target_metric_name` is a placeholder — A4.2 may rename when it
    composes the actual metric. `direction` is locked here because changing
    it later would invert the gate's improvement check.
    """

    direction: Literal["maximize", "minimize"]
    target_metric_name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class UITab(_Base):
    """One tab in the workflow's operate-view."""

    name: str = Field(min_length=1)
    primitives: list[UIPrimitive] = Field(min_length=1)


class UILayout(_Base):
    """Operate-view layout. Mock parity: `06-workflow-operate.html`."""

    layout: Literal["tabs", "single"] = "tabs"
    tabs: list[UITab] = Field(min_length=1)


class WorkflowSpec(_Base):
    """The frozen-schema artifact stored in `workflows.spec` JSONB.

    Round-trip identity is the contract: a spec written by the generator
    must JSON-serialize, store as JSONB, and round-trip back to an
    identical Python object.

    The user's original description is NOT carried here — it lives on the
    `workflows.description` column (the source) while `spec` is the
    generated structural artifact. Asking the LLM to also echo the
    description verbatim is the kind of redundancy small models silently
    paraphrase, so we don't ask. Downstream consumers that need the
    description read it off the workflow row.
    """

    schema_version: Literal["1.1"] = SCHEMA_VERSION
    id: str = Field(min_length=1, pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
    domain: Domain
    environment: WorkflowEnvironment
    tools: list[AgentTool] = Field(min_length=1)
    known_past_misses: list[str] = Field(default_factory=list)
    reviewer: ReviewerSpec
    success_criterion: SuccessCriterionStub
    ui: UILayout

    @model_validator(mode="after")
    def _unique_tool_names(self) -> WorkflowSpec:
        names = [t.name for t in self.tools]
        if len(names) != len(set(names)):
            dupes = {n for n in names if names.count(n) > 1}
            raise ValueError(f"tools contains duplicate names: {dupes}")
        return self


__all__ = [
    "SCHEMA_VERSION",
    "Domain",
    "FieldType",
    "Provenance",
    "EntityField",
    "Entity",
    "DataSource",
    "EnvGenerator",
    "Persona",
    "WorkflowEnvironment",
    "ToolParam",
    "AgentTool",
    "ReviewerSpec",
    "SuccessCriterionStub",
    "UITab",
    "UILayout",
    "WorkflowSpec",
]
