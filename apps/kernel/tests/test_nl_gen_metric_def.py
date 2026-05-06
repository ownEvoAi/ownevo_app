"""Schema tests for `nl_gen.metric_def.MetricDefinition` (A4.2).

Mirrors `test_nl_gen_eval_spec.py` (A4.1):

  * Round-trip identity — the canonical contract for every NL-gen schema.
  * `extra="forbid"` — catches generator drift where the model invents
    a field.
  * Per-field validators — bounds ordering, target inside bounds, kebab-
    case ids, schema_version pin.
  * Fixture round-trip — proves the hand-authored MetricDefinitions parse
    cleanly and re-serialize byte-identical.

Cross-spec validation (direction matches the workflow's success_criterion,
workflow_spec_id matches spec.id) lives in `metric_compute._check_against_spec`
and is exercised by the generator + compute test files.
"""

from __future__ import annotations

import json

import pytest
from ownevo_kernel.nl_gen import (
    METRIC_DEFINITION_SCHEMA_VERSION,
    MetricDefinition,
    MetricFamily,
)
from ownevo_kernel.nl_gen.fixtures import METRIC_FIXTURES
from ownevo_kernel.nl_gen.spec import Provenance
from pydantic import ValidationError


def _payload(**overrides):
    """A minimal valid MetricDefinition payload for surgical mutation tests."""
    base = {
        "schema_version": "0.1",
        "workflow_spec_id": "demo-workflow",
        "name": "demo-metric",
        "family": "f1",
        "direction": "maximize",
        "lower_bound": 0.0,
        "upper_bound": 1.0,
        "target_value": 0.8,
        "description": "Demo metric for the unit-test suite.",
        "rationale": "Picked f1 to exercise both error modes.",
        "provenance": {"kind": "inferred", "source": "binary classification pattern"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Schema-version pin
# ---------------------------------------------------------------------------


def test_schema_version_constant_is_pinned_at_0_1():
    """A4.2 schema is pre-freeze. Bumping must be a deliberate edit."""
    assert METRIC_DEFINITION_SCHEMA_VERSION == "0.1"


def test_default_schema_version_matches_constant():
    md = MetricDefinition.model_validate(_payload())
    assert md.schema_version == METRIC_DEFINITION_SCHEMA_VERSION


def test_wrong_schema_version_rejected():
    with pytest.raises(ValidationError, match="schema_version"):
        MetricDefinition.model_validate(_payload(schema_version="0.2"))


# ---------------------------------------------------------------------------
# Round-trip identity
# ---------------------------------------------------------------------------


def test_round_trip_via_json_is_byte_identical():
    md = MetricDefinition.model_validate(_payload())
    again = MetricDefinition.model_validate_json(md.model_dump_json())
    assert again == md


def test_round_trip_via_dict_is_byte_identical():
    md = MetricDefinition.model_validate(_payload())
    again = MetricDefinition.model_validate(md.model_dump())
    assert again == md


# ---------------------------------------------------------------------------
# extra="forbid"
# ---------------------------------------------------------------------------


def test_extra_field_rejected():
    payload = _payload()
    payload["bonus_field"] = "claude invented this"
    with pytest.raises(ValidationError, match="bonus_field"):
        MetricDefinition.model_validate(payload)


def test_extra_field_on_provenance_rejected():
    payload = _payload()
    payload["provenance"] = {
        "kind": "inferred",
        "source": "x",
        "extra": "y",
    }
    with pytest.raises(ValidationError, match="extra"):
        MetricDefinition.model_validate(payload)


# ---------------------------------------------------------------------------
# Required-field shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "drop_field",
    [
        "workflow_spec_id",
        "name",
        "family",
        "direction",
        "lower_bound",
        "upper_bound",
        "target_value",
        "description",
        "rationale",
        "provenance",
    ],
)
def test_missing_required_field_rejected(drop_field):
    payload = _payload()
    payload.pop(drop_field)
    with pytest.raises(ValidationError):
        MetricDefinition.model_validate(payload)


# ---------------------------------------------------------------------------
# Family / direction enums
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "family",
    ["pass_rate", "precision", "recall", "f1", "balanced_accuracy", "specificity"],
)
def test_every_supported_family_accepted(family):
    md = MetricDefinition.model_validate(_payload(family=family))
    assert md.family == family


def test_unknown_family_rejected():
    with pytest.raises(ValidationError, match="family"):
        MetricDefinition.model_validate(_payload(family="auc_roc"))


def test_unknown_direction_rejected():
    with pytest.raises(ValidationError, match="direction"):
        MetricDefinition.model_validate(_payload(direction="optimize"))


# ---------------------------------------------------------------------------
# Bounds ordering + target containment
# ---------------------------------------------------------------------------


def test_lower_bound_equal_to_upper_rejected():
    with pytest.raises(ValidationError, match="strictly less than"):
        MetricDefinition.model_validate(
            _payload(lower_bound=0.5, upper_bound=0.5, target_value=0.5)
        )


def test_lower_bound_greater_than_upper_rejected():
    with pytest.raises(ValidationError, match="strictly less than"):
        MetricDefinition.model_validate(
            _payload(lower_bound=0.9, upper_bound=0.1, target_value=0.5)
        )


def test_target_below_lower_bound_rejected():
    with pytest.raises(ValidationError, match="must lie inside"):
        MetricDefinition.model_validate(_payload(target_value=-0.1))


def test_target_above_upper_bound_rejected():
    with pytest.raises(ValidationError, match="must lie inside"):
        MetricDefinition.model_validate(_payload(target_value=1.1))


def test_target_at_lower_bound_accepted():
    md = MetricDefinition.model_validate(_payload(target_value=0.0))
    assert md.target_value == 0.0


def test_target_at_upper_bound_accepted():
    md = MetricDefinition.model_validate(_payload(target_value=1.0))
    assert md.target_value == 1.0


def test_lower_bound_below_zero_rejected():
    with pytest.raises(ValidationError):
        MetricDefinition.model_validate(_payload(lower_bound=-0.1))


def test_upper_bound_above_one_rejected():
    with pytest.raises(ValidationError):
        MetricDefinition.model_validate(_payload(upper_bound=1.1))


# ---------------------------------------------------------------------------
# Kebab-case + non-empty contracts
# ---------------------------------------------------------------------------


def test_workflow_spec_id_kebab_case_enforced():
    with pytest.raises(ValidationError):
        MetricDefinition.model_validate(_payload(workflow_spec_id="Demo_Workflow"))


def test_metric_name_kebab_case_enforced():
    with pytest.raises(ValidationError):
        MetricDefinition.model_validate(_payload(name="DemoMetric"))


def test_empty_description_rejected():
    with pytest.raises(ValidationError):
        MetricDefinition.model_validate(_payload(description=""))


def test_empty_rationale_rejected():
    with pytest.raises(ValidationError):
        MetricDefinition.model_validate(_payload(rationale=""))


# ---------------------------------------------------------------------------
# Frozen
# ---------------------------------------------------------------------------


def test_definition_is_frozen():
    md = MetricDefinition.model_validate(_payload())
    with pytest.raises(ValidationError):
        md.target_value = 0.5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# JSON-schema export shape
# ---------------------------------------------------------------------------


def test_json_schema_root_shape():
    schema = MetricDefinition.model_json_schema()
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    required = set(schema["required"])
    for f in (
        "workflow_spec_id",
        "name",
        "family",
        "direction",
        "lower_bound",
        "upper_bound",
        "target_value",
        "description",
        "rationale",
        "provenance",
    ):
        assert f in required


def test_json_schema_family_enum_pinned():
    """Closed-family contract — the export must list every supported value."""
    schema = MetricDefinition.model_json_schema()
    family_field = schema["properties"]["family"]
    assert set(family_field["enum"]) == {
        "pass_rate", "precision", "recall", "f1", "balanced_accuracy", "specificity"
    }


def test_metric_family_literal_matches_schema_enum():
    """MetricFamily Literal stays in sync with the JSON-schema enum export."""
    from typing import get_args

    assert set(get_args(MetricFamily)) == {
        "pass_rate", "precision", "recall", "f1", "balanced_accuracy", "specificity"
    }


# ---------------------------------------------------------------------------
# Fixtures round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_id", list(METRIC_FIXTURES.keys()))
def test_fixture_round_trip(fixture_id):
    md = METRIC_FIXTURES[fixture_id]
    again = MetricDefinition.model_validate_json(md.model_dump_json())
    assert again == md


@pytest.mark.parametrize("fixture_id", list(METRIC_FIXTURES.keys()))
def test_fixture_target_inside_bounds(fixture_id):
    md = METRIC_FIXTURES[fixture_id]
    assert md.lower_bound <= md.target_value <= md.upper_bound


@pytest.mark.parametrize("fixture_id", list(METRIC_FIXTURES.keys()))
def test_fixture_direction_is_maximize(fixture_id):
    """All currently supported families are naturally maximize — pin that
    fixture-time invariant so a future minimize-family fixture lands
    intentionally."""
    md = METRIC_FIXTURES[fixture_id]
    assert md.direction == "maximize"


def test_fixture_set_covers_distinct_families():
    """Three fixtures, three families — exercises the dispatch breadth."""
    families = {md.family for md in METRIC_FIXTURES.values()}
    assert len(families) == 3
    assert families <= {
        "pass_rate", "precision", "recall", "f1",
        "balanced_accuracy", "specificity",
    }


def test_fixture_keys_match_workflow_fixture_keys():
    from ownevo_kernel.nl_gen.fixtures import FIXTURES
    assert set(METRIC_FIXTURES.keys()) == set(FIXTURES.keys())


def test_fixture_workflow_spec_ids_match_corresponding_specs():
    """workflow_spec_id on each fixture matches the source WorkflowSpec.id."""
    from ownevo_kernel.nl_gen.fixtures import FIXTURES
    for fixture_id, metric in METRIC_FIXTURES.items():
        assert metric.workflow_spec_id == FIXTURES[fixture_id].id


def test_fixture_provenance_shape():
    for md in METRIC_FIXTURES.values():
        assert isinstance(md.provenance, Provenance)
        assert md.provenance.source


def test_fixture_serializes_to_clean_json():
    """Fixtures must be safe for the audit trail — no NaN, no surprises."""
    for md in METRIC_FIXTURES.values():
        payload = json.loads(md.model_dump_json())
        assert payload["schema_version"] == "0.1"
        assert isinstance(payload["target_value"], float)
