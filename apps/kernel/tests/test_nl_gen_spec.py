"""Tests for the NL-gen workflow-spec schema (A3.1).

No DB, no LLM, no network. Covers:
  * Schema-only round-trip identity (Pydantic → JSON → JSON-mode dict → Pydantic)
  * `extra="forbid"` on every model — schema-freeze depends on it
  * Discriminator coverage — every UIPrimitive variant must round-trip
  * The 3 hand-authored fixtures validate against the frozen schema
  * Structural-shape assertions on each fixture (mock-parity ground truth)
"""

from __future__ import annotations

import json

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
from ownevo_kernel.nl_gen import (
    SCHEMA_VERSION,
    AgentTool,
    EntityField,
    Provenance,
    SuccessCriterionStub,
    UILayout,
    UITab,
    WorkflowSpec,
)
from ownevo_kernel.nl_gen.fixtures import (
    CONTRACT_REVIEW_SPEC,
    CREDIT_RISK_SPEC,
    DEMAND_PREDICTION_SPEC,
    FIXTURES,
)
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Schema-version invariant
# ---------------------------------------------------------------------------


def test_schema_version_is_frozen_at_one_zero():
    assert SCHEMA_VERSION == "1.0", (
        "Frozen at A3.4 (2026-W3). Structural drift is caught by "
        "test_nl_gen_schema_freeze.py against the snapshot at "
        "src/ownevo_kernel/nl_gen/schemas/workflow_spec.v1.0.json."
    )
    for spec in FIXTURES.values():
        assert spec.schema_version == "1.0"


# ---------------------------------------------------------------------------
# `extra="forbid"` enforcement on every nested model
# ---------------------------------------------------------------------------


def test_workflow_spec_rejects_extra_fields():
    payload = json.loads(DEMAND_PREDICTION_SPEC.model_dump_json())
    payload["unexpected_field"] = "should reject"
    with pytest.raises(ValidationError):
        WorkflowSpec.model_validate(payload)


def test_provenance_rejects_extra_fields():
    with pytest.raises(ValidationError):
        Provenance.model_validate(
            {"kind": "derived", "source": "x", "extra": "nope"}
        )


def test_entity_field_rejects_extra_fields():
    with pytest.raises(ValidationError):
        EntityField.model_validate(
            {"name": "x", "type": "string", "junk": True}
        )


def test_provenance_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        Provenance.model_validate({"kind": "guessed", "source": "x"})


def test_success_criterion_rejects_unknown_direction():
    with pytest.raises(ValidationError):
        SuccessCriterionStub.model_validate(
            {"direction": "sideways", "target_metric_name": "x", "description": "y"}
        )


# ---------------------------------------------------------------------------
# Field constraints
# ---------------------------------------------------------------------------


def test_workflow_id_must_be_kebab_slug():
    bad_ids = ["UpperCase", "with space", "with_underscore", "-leading-dash", ""]
    for bad in bad_ids:
        payload = json.loads(DEMAND_PREDICTION_SPEC.model_dump_json())
        payload["id"] = bad
        with pytest.raises(ValidationError):
            WorkflowSpec.model_validate(payload)


def test_workflow_must_have_at_least_one_tool():
    payload = json.loads(DEMAND_PREDICTION_SPEC.model_dump_json())
    payload["tools"] = []
    with pytest.raises(ValidationError):
        WorkflowSpec.model_validate(payload)


def test_ui_layout_must_have_at_least_one_tab():
    with pytest.raises(ValidationError):
        UILayout.model_validate({"layout": "tabs", "tabs": []})


def test_ui_tab_must_have_at_least_one_primitive():
    with pytest.raises(ValidationError):
        UITab.model_validate({"name": "x", "primitives": []})


def test_metric_cards_must_have_fields():
    with pytest.raises(ValidationError):
        MetricCards.model_validate({"type": "MetricCards", "fields": []})


def test_schema_version_must_be_dotted():
    payload = json.loads(DEMAND_PREDICTION_SPEC.model_dump_json())
    payload["schema_version"] = "v1"
    with pytest.raises(ValidationError):
        WorkflowSpec.model_validate(payload)


# ---------------------------------------------------------------------------
# Round-trip identity — Pydantic → JSON → dict → Pydantic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_id", list(FIXTURES.keys()))
def test_fixture_round_trips_through_json(fixture_id):
    original = FIXTURES[fixture_id]
    serialized = original.model_dump_json()
    rebuilt = WorkflowSpec.model_validate_json(serialized)
    assert rebuilt == original

    # Also exercise the mode through which Postgres JSONB will land back —
    # parsed JSON → dict → model_validate.
    as_dict = json.loads(serialized)
    rebuilt2 = WorkflowSpec.model_validate(as_dict)
    assert rebuilt2 == original


# ---------------------------------------------------------------------------
# Discriminator coverage — every UIPrimitive variant must round-trip
# ---------------------------------------------------------------------------


_ALL_VARIANTS = [
    MetricCards(type="MetricCards", fields=["a", "b"]),
    TimeSeriesChart(type="TimeSeriesChart", x="week", y=["forecast"]),
    TableView(type="TableView", source="x", columns=["a"]),
    AlertList(type="AlertList", source="x"),
    KanbanBoard(
        type="KanbanBoard",
        source="x",
        column_field="status",
        card_title_field="title",
    ),
    ConversationView(type="ConversationView", trace_source="x"),
    SideBySideView(type="SideBySideView", left_source="a", right_source="b"),
    DocumentReader(type="DocumentReader", source="x"),
]


@pytest.mark.parametrize("variant", _ALL_VARIANTS)
def test_ui_primitive_variant_round_trips(variant):
    payload = json.loads(variant.model_dump_json())
    parsed = UIPrimitiveAdapter.validate_python(payload)
    assert type(parsed) is type(variant)
    assert parsed == variant


def test_ui_primitive_rejects_unknown_type():
    with pytest.raises(ValidationError):
        UIPrimitiveAdapter.validate_python(
            {"type": "ChartOfTheDay", "source": "x"}
        )


# ---------------------------------------------------------------------------
# Fixtures: structural-shape assertions
#
# Mock parity: every fixture must satisfy what 04-new-workflow-step2.html
# renders — ≥1 tool, ≥1 persona, ≥1 env_generator, the success_criterion stub
# names a metric, `ui` exercises domain-appropriate primitives.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_id", list(FIXTURES.keys()))
def test_fixture_satisfies_minimum_shape(fixture_id):
    spec = FIXTURES[fixture_id]
    assert len(spec.tools) >= 3
    assert len(spec.environment.personas) >= 1
    assert len(spec.environment.env_generators) >= 1
    assert spec.success_criterion.target_metric_name
    assert spec.ui.tabs
    assert spec.ui.tabs[0].primitives


def test_demand_prediction_uses_supply_chain_primitives():
    types = {p.type for p in DEMAND_PREDICTION_SPEC.ui.tabs[0].primitives}
    assert "TimeSeriesChart" in types
    assert "TableView" in types
    assert "AlertList" in types


def test_credit_risk_uses_portfolio_primitives():
    types = {p.type for p in CREDIT_RISK_SPEC.ui.tabs[0].primitives}
    assert "MetricCards" in types
    assert "TableView" in types


def test_contract_review_uses_legal_primitives():
    types = {p.type for p in CONTRACT_REVIEW_SPEC.ui.tabs[0].primitives}
    assert "DocumentReader" in types
    assert "SideBySideView" in types


@pytest.mark.parametrize("fixture_id", list(FIXTURES.keys()))
def test_fixture_known_past_misses_non_empty(fixture_id):
    """A4.1 will turn known_past_misses into eval cases; an empty list means
    the spec carried no signal from the user's description."""
    spec = FIXTURES[fixture_id]
    assert spec.known_past_misses, "expected at least one past-miss phrase"


@pytest.mark.parametrize("fixture_id", list(FIXTURES.keys()))
def test_fixture_tools_carry_provenance(fixture_id):
    """Demo's load-bearing claim: every tool traces back to a phrase or a
    domain pattern. Hand-authored fixtures must satisfy this — we use them
    as the structural ground truth for live-API snapshot tests."""
    spec = FIXTURES[fixture_id]
    for tool in spec.tools:
        assert tool.provenance is not None, f"tool {tool.name} missing provenance"
        assert tool.provenance.source


def test_demand_prediction_description_is_verbatim_from_mock():
    """Pin the description to the mock's textarea content. The description
    itself lives on `DEMAND_PREDICTION_DESCRIPTION` (not in the spec); the
    fixture's `known_past_misses` carries the failure-mode phrases the
    description names."""
    from ownevo_kernel.nl_gen.fixtures import DEMAND_PREDICTION_DESCRIPTION
    assert "8,400 SKU catalog across 142 stores" in DEMAND_PREDICTION_DESCRIPTION
    assert (
        "missed the 2025 Pacific NW winter boot spike by 4 weeks"
        in DEMAND_PREDICTION_DESCRIPTION
    )
    assert (
        "underweight promotional uplift on bundled SKUs"
        in DEMAND_PREDICTION_DESCRIPTION
    )
    # Cross-check those past-miss phrases land in the spec itself.
    misses = " | ".join(DEMAND_PREDICTION_SPEC.known_past_misses)
    assert "Pacific NW winter boot" in misses
    assert "promotional uplift" in misses


# ---------------------------------------------------------------------------
# JSON-Schema export — used by workflow_spec_generator for tool_use input
# ---------------------------------------------------------------------------


def test_workflow_spec_emits_json_schema():
    """The generator wires this into Anthropic's tool_use input_schema."""
    schema = WorkflowSpec.model_json_schema()
    assert schema["type"] == "object"
    assert "properties" in schema
    # Top-level required fields the generator must populate. `description`
    # is intentionally NOT in the spec — it lives on workflows.description.
    required = set(schema.get("required", []))
    for f in ("id", "domain", "environment", "tools",
              "reviewer", "success_criterion", "ui"):
        assert f in required, f"'{f}' missing from required: {required}"
    assert "description" not in required
    assert "description" not in schema["properties"]


def test_provenance_kind_is_a_locked_literal_in_schema():
    """The discriminator on Provenance.kind must surface in JSON schema as
    an enum; otherwise Claude is free to invent values."""
    schema = WorkflowSpec.model_json_schema()
    defs = schema.get("$defs", schema.get("definitions", {}))
    prov = defs.get("Provenance")
    assert prov, "Provenance not in JSON schema $defs"
    kind_schema = prov["properties"]["kind"]
    assert kind_schema.get("enum") == ["derived", "inferred"]
