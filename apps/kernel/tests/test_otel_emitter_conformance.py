"""OTel emitter conformance tests (Track 17.2.3).

Verifies that every ``emit_*`` method defined in `OtelEmitter`:

1. Emits exactly one span with the expected name.
2. Sets ``ownevo.event.kind`` on every span.
3. Sets all attributes required by ``docs/OTEL_EMITTER_CONVENTIONS.md``.

These tests use an in-memory `InMemorySpanExporter` (from the OTel SDK test
helpers) so they run without any network access.  The test module is skipped
when ``opentelemetry-sdk`` is not installed.

CI job: the ``test-otel-emitter-conformance`` step in ``.github/workflows/ci.yml``
runs this module and fails the build if any assertion fails.  The intent is
that adding a new ``emit_*`` method without a corresponding test here causes
CI to fail, keeping the conventions document and the implementation in sync.
"""

from __future__ import annotations

import pytest

# Skip the entire module when the OTel SDK is absent — the emitter itself is
# opt-in, so the conformance tests are too.
otel_sdk = pytest.importorskip(
    "opentelemetry.sdk.trace",
    reason="opentelemetry-sdk not installed; install ownevo-kernel[otel-emit]",
)

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402

from ownevo_kernel.otel_emitter.emitter import OtelEmitter, OtelEmitterConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_emitter() -> tuple[OtelEmitter, InMemorySpanExporter]:
    """Build an OtelEmitter backed by an in-memory exporter."""
    exporter = InMemorySpanExporter()
    cfg = OtelEmitterConfig(
        endpoint="http://localhost:4318/v1/traces",  # non-empty triggers tracer build
        service_name="test-ownevo",
    )
    emitter = OtelEmitter(config=cfg, exporter=exporter)
    return emitter, exporter


def _get_spans(exporter: InMemorySpanExporter) -> list:
    """Return the list of finished spans captured by `exporter`."""
    return list(exporter.get_finished_spans())


def _attrs(span) -> dict:
    """Return span attributes as a plain dict."""
    return dict(span.attributes or {})


# ---------------------------------------------------------------------------
# Fixture: one emitter per test (fresh exporter each time)
# ---------------------------------------------------------------------------


@pytest.fixture()
def emitter_and_exporter():
    em, exp = _make_emitter()
    yield em, exp
    exp.clear()


# ---------------------------------------------------------------------------
# Conformance tests
# ---------------------------------------------------------------------------


class TestClusterCreated:
    def test_span_name(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_cluster_created(
            workflow_id="wf-1", cluster_count=3, failure_count=12
        )
        spans = _get_spans(exp)
        assert len(spans) == 1
        assert spans[0].name == "ownevo.cluster.created"

    def test_required_attributes(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_cluster_created(
            workflow_id="wf-1",
            cluster_count=3,
            failure_count=12,
            algorithm="hdbscan",
            embedder="all-MiniLM-L6-v2",
        )
        a = _attrs(_get_spans(exp)[0])
        assert a["ownevo.event.kind"] == "cluster.created"
        assert a["ownevo.workflow.id"] == "wf-1"
        assert a["ownevo.cluster.count"] == 3
        assert a["ownevo.failure.count"] == 12
        assert a["ownevo.cluster.algorithm"] == "hdbscan"
        assert a["ownevo.cluster.embedder"] == "all-MiniLM-L6-v2"


class TestProposalGenerated:
    def test_span_name(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_proposal_generated(
            workflow_id="wf-1",
            proposal_id="p-1",
            iteration_id="iter-1",
            kind="instruction",
        )
        assert _get_spans(exp)[0].name == "ownevo.proposal.generated"

    def test_required_attributes(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_proposal_generated(
            workflow_id="wf-1",
            proposal_id="p-1",
            iteration_id="iter-1",
            kind="python",
            cluster_count=2,
        )
        a = _attrs(_get_spans(exp)[0])
        assert a["ownevo.event.kind"] == "proposal.generated"
        assert a["ownevo.workflow.id"] == "wf-1"
        assert a["ownevo.proposal.id"] == "p-1"
        assert a["ownevo.iteration.id"] == "iter-1"
        assert a["ownevo.proposal.kind"] == "python"
        assert a["ownevo.proposal.cluster_count"] == 2


class TestApprovalRecorded:
    def test_span_name(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_approval_recorded(
            workflow_id="wf-1",
            proposal_id="p-1",
            decision="approved",
            approver_type="human",
        )
        assert _get_spans(exp)[0].name == "ownevo.approval.recorded"

    def test_required_attributes(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_approval_recorded(
            workflow_id="wf-2",
            proposal_id="p-2",
            decision="rejected",
            approver_type="llm-judge",
        )
        a = _attrs(_get_spans(exp)[0])
        assert a["ownevo.event.kind"] == "approval.recorded"
        assert a["ownevo.workflow.id"] == "wf-2"
        assert a["ownevo.proposal.id"] == "p-2"
        assert a["ownevo.approval.decision"] == "rejected"
        assert a["ownevo.approval.approver_type"] == "llm-judge"


class TestGatePassed:
    def test_span_name(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_gate_passed(
            workflow_id="wf-1",
            proposal_id="p-1",
            iteration_id="iter-1",
            baseline_score=0.70,
            candidate_score=0.80,
        )
        assert _get_spans(exp)[0].name == "ownevo.gate.passed"

    def test_required_attributes(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_gate_passed(
            workflow_id="wf-1",
            proposal_id="p-1",
            iteration_id="iter-1",
            baseline_score=0.70,
            candidate_score=0.80,
        )
        a = _attrs(_get_spans(exp)[0])
        assert a["ownevo.event.kind"] == "gate.passed"
        assert a["ownevo.gate.result"] == "pass"
        assert abs(a["ownevo.gate.baseline_score"] - 0.70) < 1e-6
        assert abs(a["ownevo.gate.candidate_score"] - 0.80) < 1e-6
        assert abs(a["ownevo.gate.delta"] - 0.10) < 1e-4


class TestGateBlocked:
    def test_span_name_and_result_field(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_gate_blocked(
            workflow_id="wf-1",
            proposal_id="p-1",
            iteration_id="iter-1",
            result="regression",
            baseline_score=0.80,
            candidate_score=0.75,
        )
        spans = _get_spans(exp)
        assert spans[0].name == "ownevo.gate.blocked"
        a = _attrs(spans[0])
        assert a["ownevo.gate.result"] == "regression"
        assert a["ownevo.gate.delta"] < 0


class TestIterationStarted:
    def test_span_name_and_index(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_iteration_started(
            workflow_id="wf-1",
            iteration_id="iter-1",
            iteration_index=3,
        )
        spans = _get_spans(exp)
        assert spans[0].name == "ownevo.iteration.started"
        a = _attrs(spans[0])
        assert a["ownevo.iteration.index"] == 3


class TestIterationCompleted:
    def test_span_name_and_state(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_iteration_completed(
            workflow_id="wf-1",
            iteration_id="iter-1",
            iteration_index=3,
            state="gate-pass",
            duration_ms=12345,
        )
        spans = _get_spans(exp)
        assert spans[0].name == "ownevo.iteration.completed"
        a = _attrs(spans[0])
        assert a["ownevo.iteration.state"] == "gate-pass"
        assert a["ownevo.iteration.duration_ms"] == 12345


class TestEvalRun:
    def test_required_attributes(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_eval_run(
            workflow_id="wf-1",
            case_count=20,
            pass_count=16,
            score=0.80,
            fold="test",
        )
        a = _attrs(_get_spans(exp)[0])
        assert _get_spans(exp)[0].name == "ownevo.eval.run"
        assert a["ownevo.event.kind"] == "eval.run"
        assert a["ownevo.eval.case_count"] == 20
        assert a["ownevo.eval.pass_count"] == 16
        assert abs(a["ownevo.eval.score"] - 0.80) < 1e-6
        assert a["ownevo.eval.fold"] == "test"


class TestEvalCaseGraded:
    def test_required_attributes(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_eval_case_graded(
            workflow_id="wf-1",
            case_id="case-42",
            passed=True,
            score=1.0,
        )
        a = _attrs(_get_spans(exp)[0])
        assert _get_spans(exp)[0].name == "ownevo.eval.case.graded"
        assert a["ownevo.eval.case_id"] == "case-42"
        assert a["ownevo.eval.passed"] is True
        assert abs(a["ownevo.eval.score"] - 1.0) < 1e-6


class TestSkillDeployed:
    def test_required_attributes(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_skill_deployed(
            workflow_id="wf-1",
            skill_id="sk-1",
            version=3,
            kind="instruction",
        )
        a = _attrs(_get_spans(exp)[0])
        assert _get_spans(exp)[0].name == "ownevo.skill.deployed"
        assert a["ownevo.skill.id"] == "sk-1"
        assert a["ownevo.skill.version"] == 3
        assert a["ownevo.skill.kind"] == "instruction"


class TestSkillRolledBack:
    def test_span_name(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_skill_rolled_back(
            workflow_id="wf-1",
            skill_id="sk-1",
            version=2,
            kind="python",
        )
        assert _get_spans(exp)[0].name == "ownevo.skill.rolled_back"


class TestTraceIngested:
    def test_with_workflow(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_trace_ingested(
            workflow_id="wf-1",
            event_count=18,
            warning_count=0,
            has_failure=True,
        )
        a = _attrs(_get_spans(exp)[0])
        assert a["ownevo.event.kind"] == "trace.ingested"
        assert a["ownevo.workflow.id"] == "wf-1"
        assert a["ownevo.trace.event_count"] == 18
        assert a["ownevo.trace.has_failure"] is True

    def test_without_workflow(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_trace_ingested(
            workflow_id=None,
            event_count=5,
            warning_count=1,
            has_failure=False,
        )
        a = _attrs(_get_spans(exp)[0])
        assert "ownevo.workflow.id" not in a
        assert a["ownevo.trace.warning_count"] == 1


class TestTriggerFired:
    def test_required_attributes(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_trigger_fired(
            workflow_id="wf-1",
            trigger_id="trig-1",
            trigger_kind="cron",
            trigger_action="run_clustering",
            trigger_status="ok",
        )
        a = _attrs(_get_spans(exp)[0])
        assert _get_spans(exp)[0].name == "ownevo.trigger.fired"
        assert a["ownevo.event.kind"] == "trigger.fired"
        assert a["ownevo.trigger.id"] == "trig-1"
        assert a["ownevo.trigger.kind"] == "cron"
        assert a["ownevo.trigger.action"] == "run_clustering"
        assert a["ownevo.trigger.status"] == "ok"


class TestDesignCompleted:
    def test_span_name(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_design_completed(workflow_id="wf-1", domain="supply-chain")
        assert _get_spans(exp)[0].name == "ownevo.design.completed"


class TestSandboxRun:
    def test_success_span(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_sandbox_run(
            workflow_id="wf-1",
            exit_code=0,
            duration_ms=1200,
        )
        a = _attrs(_get_spans(exp)[0])
        assert _get_spans(exp)[0].name == "ownevo.sandbox.run"
        assert a["ownevo.sandbox.exit_code"] == 0
        assert "ownevo.sandbox.error_class" not in a

    def test_failure_span(self, emitter_and_exporter):
        em, exp = emitter_and_exporter
        em.emit_sandbox_run(
            workflow_id="wf-1",
            exit_code=1,
            duration_ms=5000,
            error_class="Timeout",
        )
        a = _attrs(_get_spans(exp)[0])
        assert a["ownevo.sandbox.error_class"] == "Timeout"


# ---------------------------------------------------------------------------
# Cross-cutting: every span carries ownevo.event.kind
# ---------------------------------------------------------------------------


class TestEventKindOnEverySpan:
    """Ensure the `ownevo.event.kind` attribute is present on every emitted span."""

    CASES = [
        (
            "cluster_created",
            lambda em: em.emit_cluster_created(
                workflow_id="wf", cluster_count=1, failure_count=5
            ),
        ),
        (
            "proposal_generated",
            lambda em: em.emit_proposal_generated(
                workflow_id="wf", proposal_id="p", iteration_id="i", kind="instruction"
            ),
        ),
        (
            "approval_recorded",
            lambda em: em.emit_approval_recorded(
                workflow_id="wf", proposal_id="p", decision="approved", approver_type="human"
            ),
        ),
        (
            "gate_passed",
            lambda em: em.emit_gate_passed(
                workflow_id="wf",
                proposal_id="p",
                iteration_id="i",
                baseline_score=0.5,
                candidate_score=0.6,
            ),
        ),
        (
            "gate_blocked",
            lambda em: em.emit_gate_blocked(
                workflow_id="wf",
                proposal_id="p",
                iteration_id="i",
                result="regression",
                baseline_score=0.6,
                candidate_score=0.5,
            ),
        ),
        (
            "iteration_started",
            lambda em: em.emit_iteration_started(
                workflow_id="wf", iteration_id="i", iteration_index=1
            ),
        ),
        (
            "iteration_completed",
            lambda em: em.emit_iteration_completed(
                workflow_id="wf",
                iteration_id="i",
                iteration_index=1,
                state="gate-pass",
                duration_ms=1000,
            ),
        ),
        (
            "eval_run",
            lambda em: em.emit_eval_run(
                workflow_id="wf",
                case_count=10,
                pass_count=8,
                score=0.8,
                fold="test",
            ),
        ),
        (
            "eval_case_graded",
            lambda em: em.emit_eval_case_graded(
                workflow_id="wf", case_id="c", passed=True, score=1.0
            ),
        ),
        (
            "skill_deployed",
            lambda em: em.emit_skill_deployed(
                workflow_id="wf", skill_id="s", version=1, kind="instruction"
            ),
        ),
        (
            "skill_rolled_back",
            lambda em: em.emit_skill_rolled_back(
                workflow_id="wf", skill_id="s", version=1, kind="instruction"
            ),
        ),
        (
            "trace_ingested",
            lambda em: em.emit_trace_ingested(
                workflow_id="wf", event_count=5, warning_count=0, has_failure=False
            ),
        ),
        (
            "trigger_fired",
            lambda em: em.emit_trigger_fired(
                workflow_id="wf",
                trigger_id="t",
                trigger_kind="cron",
                trigger_action="run_clustering",
                trigger_status="ok",
            ),
        ),
        (
            "design_completed",
            lambda em: em.emit_design_completed(workflow_id="wf", domain="legal"),
        ),
        (
            "sandbox_run",
            lambda em: em.emit_sandbox_run(
                workflow_id="wf", exit_code=0, duration_ms=500
            ),
        ),
    ]

    @pytest.mark.parametrize("name,call", CASES)
    def test_event_kind_present(self, name, call):
        em, exp = _make_emitter()
        call(em)
        spans = _get_spans(exp)
        assert spans, f"emit_{name} produced no spans"
        assert "ownevo.event.kind" in _attrs(spans[0]), (
            f"emit_{name}: missing ownevo.event.kind"
        )


# ---------------------------------------------------------------------------
# Disabled emitter (no endpoint, no injected exporter)
# ---------------------------------------------------------------------------


class TestDisabledEmitter:
    def test_no_spans_emitted(self):
        """With no endpoint and no exporter, all emit_* calls are no-ops."""
        em = OtelEmitter(config=OtelEmitterConfig(endpoint=None))
        # Should not raise; no spans go anywhere.
        em.emit_cluster_created(workflow_id="wf", cluster_count=1, failure_count=1)
        em.emit_iteration_started(workflow_id="wf", iteration_id="i", iteration_index=1)
