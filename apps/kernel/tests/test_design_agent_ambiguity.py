"""Tests for `design_agent.ambiguity` — the post-generation scan."""

from __future__ import annotations

import pytest
from ownevo_format.ui_primitives import MetricCards
from ownevo_kernel.design_agent.ambiguity import (
    AmbiguityFinding,
    AmbiguityReport,
    analyze_workflow,
    find_description_conflicts,
    find_inferred_artifacts,
    find_metric_direction_conflicts,
)
from ownevo_kernel.nl_gen.fixtures import (
    CREDIT_RISK_SPEC,
    DEMAND_PREDICTION_METRIC,
    DEMAND_PREDICTION_SPEC,
)
from ownevo_kernel.nl_gen.spec import (
    AgentTool,
    DataSource,
    EnvGenerator,
    Persona,
    Provenance,
    ReviewerSpec,
    SuccessCriterionStub,
    UILayout,
    UITab,
    WorkflowEnvironment,
    WorkflowSpec,
)

# ---------------------------------------------------------------------------
# Pass A — inferred-artifact scan
# ---------------------------------------------------------------------------


def test_inferred_scan_finds_the_demand_prediction_inferred_artifact() -> None:
    """The demand-prediction fixture has one persona with `kind=inferred` —
    the scan should surface it as an inferred-artifact finding."""
    findings = find_inferred_artifacts(DEMAND_PREDICTION_SPEC)
    assert len(findings) >= 1
    inferred = [
        f
        for f in findings
        if f.location.startswith("environment.personas.")
        and f.kind == "inferred-artifact"
    ]
    assert len(inferred) >= 1


def test_inferred_scan_returns_empty_for_all_derived_spec() -> None:
    """A spec where every provenance is `derived` produces no findings."""
    spec = _build_minimal_spec(provenance_kind="derived")
    assert find_inferred_artifacts(spec) == ()


def test_inferred_scan_marks_inferred_reviewer_as_high_severity() -> None:
    """Reviewer is the most consequential artifact — flagging an inferred
    reviewer should be high severity (the rest are medium)."""
    spec = _build_minimal_spec(
        provenance_kind="inferred",
        reviewer_kind="inferred",
    )
    findings = find_inferred_artifacts(spec)
    reviewer_findings = [f for f in findings if f.location == "reviewer"]
    assert len(reviewer_findings) == 1
    assert reviewer_findings[0].severity == "high"


def test_inferred_scan_walks_every_artifact_class() -> None:
    """The scan covers tools, personas, data sources, env generators,
    and reviewer — one finding per inferred artifact."""
    spec = _build_minimal_spec(provenance_kind="inferred", reviewer_kind="inferred")
    findings = find_inferred_artifacts(spec)
    locations = {f.location for f in findings}
    assert any(loc.startswith("tools.") for loc in locations)
    assert any(loc.startswith("environment.personas.") for loc in locations)
    assert any(loc.startswith("environment.data_sources.") for loc in locations)
    assert any(loc.startswith("environment.env_generators.") for loc in locations)
    assert "reviewer" in locations


def test_inferred_scan_ignores_artifact_with_null_provenance() -> None:
    """provenance=None means NL-gen recorded no provenance at all.
    The scan's `is not None` guard must not treat it as inferred."""
    spec = _build_minimal_spec(provenance_kind=None, reviewer_kind=None)
    assert find_inferred_artifacts(spec) == ()


# ---------------------------------------------------------------------------
# Pass B — description / metric conflict scan
# ---------------------------------------------------------------------------


def test_recall_plus_zero_false_positives_flags_a_conflict() -> None:
    """The canonical PLAN.md acceptance example."""
    findings = find_description_conflicts(
        "Maximize recall while accepting zero false positives across the portfolio. "
        "The reviewer signs off weekly."
    )
    assert len(findings) >= 1
    conflict = next(f for f in findings if f.kind == "conflict")
    assert conflict.severity == "high"
    q = conflict.suggested_question.lower()
    assert "miss" in q or "alarm" in q


def test_no_change_clause_flags_a_premise_conflict() -> None:
    """`don't change the model` contradicts the improvement loop's pitch."""
    findings = find_description_conflicts(
        "Forecast weekly demand at SKU-store level. Do not change the model. "
        "The category planner reviews flags weekly."
    )
    assert len(findings) >= 1
    summaries = [f.summary.lower() for f in findings]
    assert any("change" in s for s in summaries)


def test_benign_description_produces_no_conflicts() -> None:
    findings = find_description_conflicts(
        "Forecast weekly demand at SKU-store level for the next four weeks. "
        "Flag SKUs likely to need markdown. The category planner reviews flags weekly."
    )
    assert findings == ()


def test_precision_only_does_not_flag_conflict() -> None:
    """A description with only a precision ask (no recall ask) must not fire."""
    findings = find_description_conflicts(
        "Only flag confirmed markdown candidates. Zero false positives across all SKUs."
    )
    assert findings == (), (
        "precision-only description should not produce a conflict; got: "
        + str([f.summary for f in findings])
    )


def test_recall_only_does_not_flag_conflict() -> None:
    """A description with only a recall ask (no precision ask) must not fire."""
    findings = find_description_conflicts(
        "Maximize recall — never miss a true markdown candidate."
    )
    assert findings == (), (
        "recall-only description should not produce a conflict; got: "
        + str([f.summary for f in findings])
    )


def test_find_description_conflicts_takes_only_description() -> None:
    """Regression guard: metric_definition was removed as a dead parameter."""
    import inspect
    sig = inspect.signature(find_description_conflicts)
    assert list(sig.parameters.keys()) == ["description"]


@pytest.mark.parametrize(
    "description",
    [
        "The prompt must stay the same.",
        "Never modify the agent.",
        "Don't alter the model under any circumstances.",
        "You must not touch the agent.",
    ],
)
def test_no_change_patterns_canonical_phrasings(description: str) -> None:
    findings = find_description_conflicts(description)
    assert any(f.kind == "conflict" for f in findings), (
        f"no-change description {description!r} did not produce a conflict finding"
    )


@pytest.mark.parametrize(
    "description",
    [
        "Maximize recall, no false positives.",
        "Maximise recall, 0 false alarms.",
        "Catch every fraudulent transaction; only flag confirmed cases.",
        "Never miss a default; zero wrong flags.",
    ],
)
def test_conflict_scan_catches_canonical_phrasings(description: str) -> None:
    findings = find_description_conflicts(description)
    assert any(f.kind == "conflict" for f in findings), (
        f"description {description!r} did not produce a conflict finding"
    )


# ---------------------------------------------------------------------------
# Metric direction cross-check
# ---------------------------------------------------------------------------


def test_metric_direction_match_produces_no_findings() -> None:
    """The demand-prediction fixture is a self-consistent pairing —
    metric.direction == spec.success_criterion.direction."""
    findings = find_metric_direction_conflicts(
        DEMAND_PREDICTION_SPEC,
        DEMAND_PREDICTION_METRIC,
    )
    assert findings == ()


def test_metric_direction_mismatch_produces_high_severity_finding() -> None:
    spec = _build_minimal_spec(provenance_kind="derived", direction="maximize")
    metric = DEMAND_PREDICTION_METRIC.model_copy(
        update={"direction": "minimize", "workflow_spec_id": spec.id},
    )
    findings = find_metric_direction_conflicts(spec, metric)
    assert len(findings) == 1
    assert findings[0].location == "metric.direction"
    assert findings[0].severity == "high"


def test_no_metric_definition_skips_the_cross_check() -> None:
    spec = _build_minimal_spec(provenance_kind="derived")
    assert find_metric_direction_conflicts(spec, None) == ()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def test_analyze_workflow_combines_every_pass() -> None:
    """Spec with an inferred reviewer + description carrying a conflict
    yields findings from both passes, sorted high-severity first."""
    spec = _build_minimal_spec(
        provenance_kind="inferred",
        reviewer_kind="inferred",
    )
    report = analyze_workflow(
        description="Maximize recall and zero false positives.",
        spec=spec,
        metric_definition=None,
    )
    assert isinstance(report, AmbiguityReport)
    assert report.workflow_spec_id == spec.id
    # At least 1 conflict (high) + 1 reviewer inferred (high) +
    # several other inferred medium. High-severity must come first.
    high_count = report.high_severity_count
    assert high_count >= 2
    severities = [f.severity for f in report.findings]
    assert severities == sorted(
        severities, key=lambda s: {"high": 0, "medium": 1, "low": 2}[s]
    )


def test_analyze_workflow_returns_empty_report_for_clean_spec() -> None:
    """Fully-derived spec + benign description + no metric → zero findings."""
    spec = _build_minimal_spec(provenance_kind="derived")
    report = analyze_workflow(
        description=(
            "Forecast weekly demand at SKU-store level for the next four weeks. "
            "Flag SKUs likely to need markdown. The category planner reviews flags weekly."
        ),
        spec=spec,
        metric_definition=None,
    )
    assert report.findings == (), (
        f"expected no findings for a clean spec; got: {[f.summary for f in report.findings]}"
    )
    assert report.workflow_spec_id == spec.id


def test_analyze_workflow_on_credit_risk_fixture_runs_clean() -> None:
    """The packaged credit-risk fixture is hand-authored to be
    self-consistent. Sanity check that the scan does not invent
    findings on a well-formed spec."""
    report = analyze_workflow(
        description=(
            "Recalibrate probability-of-default models monthly using new "
            "portfolio performance data. The chief risk officer reviews "
            "the proposed adjustments before sign-off."
        ),
        spec=CREDIT_RISK_SPEC,
        metric_definition=None,
    )
    # The fixture may carry inferred artifacts — but no conflict
    # findings should fire on the benign description.
    assert all(f.kind != "conflict" for f in report.findings)


def test_report_is_frozen() -> None:
    """AmbiguityReport rejects mutation — pin the immutability so
    downstream consumers can hold references safely."""
    report = AmbiguityReport(workflow_spec_id="x", findings=())
    with pytest.raises(ValueError):
        report.findings = ()  # type: ignore[misc]


def test_finding_round_trips_through_json() -> None:
    f = AmbiguityFinding(
        kind="conflict",
        severity="high",
        location="description",
        summary="x",
        suggested_question="y?",
    )
    rt = AmbiguityFinding.model_validate_json(f.model_dump_json())
    assert rt == f


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_minimal_spec(
    *,
    provenance_kind: str | None,
    reviewer_kind: str | None = None,
    direction: str = "maximize",
) -> WorkflowSpec:
    """Hand-rolled WorkflowSpec for unit-test scenarios.

    `provenance_kind` sets the kind for every non-reviewer artifact
    (tool / persona / data source / env generator). `None` means no
    provenance recorded (provenance=None on the artifact).
    `reviewer_kind` independently sets the reviewer's provenance kind.
    """
    prov = (
        Provenance(kind=provenance_kind, source="domain pattern")  # type: ignore[arg-type]
        if provenance_kind is not None
        else None
    )
    reviewer_prov = (
        Provenance(kind=reviewer_kind, source="domain pattern")  # type: ignore[arg-type]
        if reviewer_kind is not None
        else None
    )
    return WorkflowSpec(
        id="test-spec",
        domain="supply-chain",  # type: ignore[arg-type]
        environment=WorkflowEnvironment(
            data_sources=[
                DataSource(id="erp", description="x", provenance=prov),
            ],
            env_generators=[
                EnvGenerator(name="seasonality", description="x", provenance=prov),
            ],
            personas=[
                Persona(role="planner", description="x", provenance=prov),
            ],
        ),
        tools=[
            AgentTool(name="run_forecast", description="x", provenance=prov),
        ],
        reviewer=ReviewerSpec(
            role="ops lead",
            description="x",
            provenance=reviewer_prov,
        ),
        success_criterion=SuccessCriterionStub(
            direction=direction,  # type: ignore[arg-type]
            target_metric_name="weighted-recall",
            description="x",
        ),
        ui=UILayout(
            tabs=[
                UITab(
                    name="Overview",
                    primitives=[
                        MetricCards(type="MetricCards", fields=["score"]),
                    ],
                ),
            ],
        ),
    )
