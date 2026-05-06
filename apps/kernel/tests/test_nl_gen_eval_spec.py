"""Schema-only tests for `nl_gen.eval_case_set` (A4.1).

Pins the EvalCaseSet contract:
  * round-trip through model_dump_json + model_validate
  * extra="forbid" for both EvalCaseSet and GeneratedEvalCase
  * size bounds (10..30)
  * step_index < n_steps validator
  * back-pointer match between workflow_spec_id + simulation_plan_workflow_id
  * unique case_ids
  * balanced classes (>=3 True, >=3 False)
  * SCHEMA_VERSION pinned to "0.1" (pre-A4-end freeze)
  * Fixture sets all parse and round-trip
"""

from __future__ import annotations

import json

import pytest
from ownevo_kernel.nl_gen import (
    EVAL_CASE_SET_SCHEMA_VERSION,
    EvalCaseSet,
    GeneratedEvalCase,
)
from ownevo_kernel.nl_gen.fixtures import EVAL_CASE_SET_FIXTURES
from ownevo_kernel.nl_gen.spec import Provenance
from pydantic import ValidationError


def _make_case(
    *,
    case_id: str = "c1",
    expected_value: bool = True,
    sim_seed: int = 42,
    n_steps: int = 50,
    target_step_index: int = 10,
    target_label_field: str = "alert_correct_label",
    is_test_fold: bool = False,
    provenance: Provenance | None = None,
) -> GeneratedEvalCase:
    return GeneratedEvalCase(
        case_id=case_id,
        provenance=provenance
        or Provenance(kind="inferred", source="x-pattern"),
        sim_seed=sim_seed,
        n_steps=n_steps,
        target_step_index=target_step_index,
        target_label_field=target_label_field,
        expected_value=expected_value,
        rationale="r",
        is_test_fold=is_test_fold,
    )


def _balanced_cases(n: int = 10) -> list[GeneratedEvalCase]:
    """Return n cases with >=3 True and >=3 False expected_values."""
    cases: list[GeneratedEvalCase] = []
    for i in range(n):
        cases.append(
            _make_case(
                case_id=f"c{i}",
                expected_value=(i % 2 == 0),
                target_step_index=i,
            )
        )
    return cases


def _eval_set(cases: list[GeneratedEvalCase] | None = None) -> EvalCaseSet:
    return EvalCaseSet(
        workflow_spec_id="wf-x",
        simulation_plan_workflow_id="wf-x",
        cases=cases or _balanced_cases(),
    )


# ---------------------------------------------------------------------------
# SCHEMA_VERSION
# ---------------------------------------------------------------------------


def test_schema_version_is_pre_freeze():
    """Pre-freeze pin — A4.1 ships at "0.1"; A4-end freeze bumps to "1.0"."""
    assert EVAL_CASE_SET_SCHEMA_VERSION == "0.1"
    assert _eval_set().schema_version == "0.1"


def test_schema_version_literal_rejects_other_values():
    payload = _eval_set().model_dump()
    payload["schema_version"] = "0.2"
    with pytest.raises(ValidationError):
        EvalCaseSet.model_validate(payload)


# ---------------------------------------------------------------------------
# Round-trip identity
# ---------------------------------------------------------------------------


def test_round_trip_model_dump_json():
    original = _eval_set()
    payload = original.model_dump_json()
    restored = EvalCaseSet.model_validate_json(payload)
    assert restored == original


def test_round_trip_via_dict():
    original = _eval_set()
    restored = EvalCaseSet.model_validate(json.loads(original.model_dump_json()))
    assert restored == original


# ---------------------------------------------------------------------------
# extra="forbid"
# ---------------------------------------------------------------------------


def test_extra_field_on_eval_case_set_rejected():
    payload = _eval_set().model_dump()
    payload["bonus"] = "claude invented this"
    with pytest.raises(ValidationError):
        EvalCaseSet.model_validate(payload)


def test_extra_field_on_generated_eval_case_rejected():
    payload = _eval_set().model_dump()
    payload["cases"][0]["bonus"] = "x"
    with pytest.raises(ValidationError):
        EvalCaseSet.model_validate(payload)


# ---------------------------------------------------------------------------
# Size bounds
# ---------------------------------------------------------------------------


def test_minimum_10_cases_required():
    with pytest.raises(ValidationError):
        _eval_set(cases=_balanced_cases(9))


def test_maximum_30_cases_enforced():
    with pytest.raises(ValidationError):
        _eval_set(cases=_balanced_cases(31))


def test_exactly_10_accepted():
    s = _eval_set(cases=_balanced_cases(10))
    assert len(s.cases) == 10


def test_exactly_30_accepted():
    s = _eval_set(cases=_balanced_cases(30))
    assert len(s.cases) == 30


# ---------------------------------------------------------------------------
# step_index validator
# ---------------------------------------------------------------------------


def test_target_step_index_must_be_less_than_n_steps():
    with pytest.raises(ValidationError):
        _make_case(n_steps=10, target_step_index=10)


def test_target_step_index_at_n_steps_minus_one_ok():
    case = _make_case(n_steps=10, target_step_index=9)
    assert case.target_step_index == 9


# ---------------------------------------------------------------------------
# Back-pointer agreement
# ---------------------------------------------------------------------------


def test_workflow_back_pointers_must_agree():
    with pytest.raises(ValidationError):
        EvalCaseSet(
            workflow_spec_id="wf-a",
            simulation_plan_workflow_id="wf-b",
            cases=_balanced_cases(),
        )


# ---------------------------------------------------------------------------
# Unique case_ids
# ---------------------------------------------------------------------------


def test_duplicate_case_ids_rejected():
    cases = _balanced_cases()
    cases[1] = _make_case(case_id=cases[0].case_id, expected_value=False)
    with pytest.raises(ValidationError):
        _eval_set(cases=cases)


def test_case_id_pattern_kebab_case():
    """case_id must be lowercase kebab-case — same pattern as workflow id."""
    with pytest.raises(ValidationError):
        _make_case(case_id="Case_With_Underscores")


# ---------------------------------------------------------------------------
# Balanced classes
# ---------------------------------------------------------------------------


def test_all_true_rejected():
    cases = [_make_case(case_id=f"c{i}", expected_value=True) for i in range(10)]
    with pytest.raises(ValidationError):
        _eval_set(cases=cases)


def test_all_false_rejected():
    cases = [_make_case(case_id=f"c{i}", expected_value=False) for i in range(10)]
    with pytest.raises(ValidationError):
        _eval_set(cases=cases)


def test_two_true_eight_false_rejected():
    """Less than 3 of either class — the validator's actual bar."""
    cases = (
        [_make_case(case_id=f"t{i}", expected_value=True) for i in range(2)]
        + [_make_case(case_id=f"f{i}", expected_value=False) for i in range(8)]
    )
    with pytest.raises(ValidationError):
        _eval_set(cases=cases)


def test_three_each_seven_other_accepted():
    """The minimum balanced suite at the floor of the size range."""
    cases = (
        [_make_case(case_id=f"t{i}", expected_value=True) for i in range(3)]
        + [_make_case(case_id=f"f{i}", expected_value=False) for i in range(7)]
    )
    s = _eval_set(cases=cases)
    assert len(s.cases) == 10


# ---------------------------------------------------------------------------
# Fixture round-trip + structural checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_id", list(EVAL_CASE_SET_FIXTURES.keys()))
def test_fixture_round_trips(fixture_id):
    case_set = EVAL_CASE_SET_FIXTURES[fixture_id]
    restored = EvalCaseSet.model_validate_json(case_set.model_dump_json())
    assert restored == case_set


@pytest.mark.parametrize("fixture_id", list(EVAL_CASE_SET_FIXTURES.keys()))
def test_fixture_size_in_range(fixture_id):
    case_set = EVAL_CASE_SET_FIXTURES[fixture_id]
    assert 10 <= len(case_set.cases) <= 30


@pytest.mark.parametrize("fixture_id", list(EVAL_CASE_SET_FIXTURES.keys()))
def test_fixture_balanced_classes(fixture_id):
    case_set = EVAL_CASE_SET_FIXTURES[fixture_id]
    t = sum(1 for c in case_set.cases if c.expected_value)
    f = len(case_set.cases) - t
    assert t >= 3 and f >= 3


@pytest.mark.parametrize("fixture_id", list(EVAL_CASE_SET_FIXTURES.keys()))
def test_fixture_has_test_fold_cases(fixture_id):
    """Held-out discipline — every fixture set has at least one test-fold case."""
    case_set = EVAL_CASE_SET_FIXTURES[fixture_id]
    assert sum(1 for c in case_set.cases if c.is_test_fold) >= 1


@pytest.mark.parametrize("fixture_id", list(EVAL_CASE_SET_FIXTURES.keys()))
def test_fixture_covers_all_known_past_misses(fixture_id):
    """Every `known_past_misses` phrase must seed at least one derived case.

    This is the load-bearing rule from the eval_generator system prompt.
    Hand-authored fixtures must demonstrate the rule, not just enforce it.
    """
    from ownevo_kernel.nl_gen.fixtures import FIXTURES

    case_set = EVAL_CASE_SET_FIXTURES[fixture_id]
    spec = FIXTURES[fixture_id]
    derived_sources = {
        c.provenance.source
        for c in case_set.cases
        if c.provenance.kind == "derived"
    }
    for past_miss in spec.known_past_misses:
        assert past_miss in derived_sources, (
            f"{fixture_id}: known_past_miss "
            f"{past_miss!r} is not covered by any derived eval case"
        )
