"""Pure-Python tests for the W6 30-day M5 replay orchestrator (no DB).

Covers the type surface (``ConditionSpec`` / ``IterationOutcome`` /
``ConditionResult`` / ``ThirtyDayReport``), the workflow-id and approver
helpers, the ``_parse_loop_summary`` JSON extractor, and the validation
in ``run_all_conditions_parallel``.

DB-backed integration (``merge_results`` against real ``iterations`` rows)
is deferred — it requires the M5 substrate + a running Postgres + the
sandbox image, which the CI policy excludes from the unit-test job.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from ownevo_kernel.replay import (
    CONDITION_A_FROZEN,
    CONDITION_B_STATIC_LLM,
    CONDITION_C_LOOP_AUTONOMOUS,
    CONDITION_D_LOOP_GATED,
    DEFAULT_WORKFLOW_PREFIX,
    SUPPORTED_CONDITIONS,
    ConditionResult,
    ConditionSpec,
    IterationOutcome,
    SubprocessResult,
    ThirtyDayReport,
    approver_mode_for_condition,
    workflow_id_for_condition,
)
from ownevo_kernel.replay.thirty_day import (
    _parse_loop_summary,
    _to_float,
)


# ---------------------------------------------------------------------------
# Constants + helpers
# ---------------------------------------------------------------------------


def test_supported_conditions_excludes_b_static_llm():
    """Condition B is documented as deferred for W6 — should not be in the
    public SUPPORTED_CONDITIONS tuple even though the constant exists for
    semantic completeness."""
    assert CONDITION_A_FROZEN in SUPPORTED_CONDITIONS
    assert CONDITION_C_LOOP_AUTONOMOUS in SUPPORTED_CONDITIONS
    assert CONDITION_D_LOOP_GATED in SUPPORTED_CONDITIONS
    assert CONDITION_B_STATIC_LLM not in SUPPORTED_CONDITIONS


def test_workflow_id_for_condition_default_prefix():
    assert workflow_id_for_condition(CONDITION_A_FROZEN) == "m5-condition-a"
    assert workflow_id_for_condition(CONDITION_C_LOOP_AUTONOMOUS) == "m5-condition-c"
    assert workflow_id_for_condition(CONDITION_D_LOOP_GATED) == "m5-condition-d"


def test_workflow_id_for_condition_custom_prefix():
    assert (
        workflow_id_for_condition(CONDITION_C_LOOP_AUTONOMOUS, prefix="m5-2026-05")
        == "m5-2026-05-c"
    )


def test_workflow_id_for_condition_supports_b_letter_for_consistency():
    """B is not in SUPPORTED_CONDITIONS for the orchestrator, but the
    workflow_id helper accepts it — the user might pre-create rows for a
    Phase-2 static-LLM run without coupling it to the W6 deliverable."""
    assert workflow_id_for_condition(CONDITION_B_STATIC_LLM) == "m5-condition-b"


def test_workflow_id_for_condition_rejects_unknown():
    with pytest.raises(ValueError, match="unknown condition"):
        workflow_id_for_condition("X")


def test_approver_mode_for_condition_defaults():
    """Defaults map to the run_improvement_loop --approver flag values."""
    assert approver_mode_for_condition(CONDITION_A_FROZEN) == "none"
    assert approver_mode_for_condition(CONDITION_C_LOOP_AUTONOMOUS) == "autonomous"
    assert approver_mode_for_condition(CONDITION_D_LOOP_GATED) == "llm-judge"


def test_approver_mode_for_condition_unknown_raises():
    with pytest.raises(ValueError, match="approver mode not defined"):
        approver_mode_for_condition("Z")


# ---------------------------------------------------------------------------
# ConditionSpec validation
# ---------------------------------------------------------------------------


def test_condition_spec_happy_path():
    spec = ConditionSpec(
        condition=CONDITION_C_LOOP_AUTONOMOUS,
        workflow_id="m5-condition-c",
        n_iterations=30,
    )
    assert spec.effective_approver_mode == "autonomous"


def test_condition_spec_explicit_approver_override():
    """An explicit approver_mode wins over the per-condition default."""
    spec = ConditionSpec(
        condition=CONDITION_C_LOOP_AUTONOMOUS,
        workflow_id="m5-condition-c-shadow",
        n_iterations=5,
        approver_mode="none",
    )
    assert spec.effective_approver_mode == "none"


def test_condition_spec_rejects_unsupported_condition():
    with pytest.raises(ValueError, match="not supported in W6 replay"):
        ConditionSpec(condition="X", workflow_id="wf", n_iterations=1)


def test_condition_spec_rejects_b_until_wired():
    """Condition B is parked behind the SUPPORTED_CONDITIONS gate so a
    typo doesn't silently produce a no-op condition."""
    with pytest.raises(ValueError, match="not supported in W6 replay"):
        ConditionSpec(
            condition=CONDITION_B_STATIC_LLM,
            workflow_id="m5-condition-b",
            n_iterations=30,
        )


@pytest.mark.parametrize("bad", [0, -1, -100])
def test_condition_spec_rejects_non_positive_iterations(bad: int):
    with pytest.raises(ValueError, match="n_iterations must be"):
        ConditionSpec(
            condition=CONDITION_C_LOOP_AUTONOMOUS,
            workflow_id="wf",
            n_iterations=bad,
        )


@pytest.mark.parametrize("bad", ["", "   ", "\t"])
def test_condition_spec_rejects_blank_workflow_id(bad: str):
    with pytest.raises(ValueError, match="workflow_id must be"):
        ConditionSpec(
            condition=CONDITION_C_LOOP_AUTONOMOUS,
            workflow_id=bad,
            n_iterations=1,
        )


@pytest.mark.parametrize("bad", ["banana", "AUTO", "human", ""])
def test_condition_spec_rejects_invalid_approver_mode(bad: str):
    with pytest.raises(ValueError, match="approver_mode must be"):
        ConditionSpec(
            condition=CONDITION_C_LOOP_AUTONOMOUS,
            workflow_id="wf",
            n_iterations=1,
            approver_mode=bad,
        )


# ---------------------------------------------------------------------------
# ConditionResult derived properties
# ---------------------------------------------------------------------------


def _outcome(
    idx: int,
    decision: str = "gate-pass",
    val: float | None = 0.40,
    best_after: float | None = 0.40,
    best_before: float | None = None,
    approval: str | None = None,
    approver: str | None = None,
) -> IterationOutcome:
    return IterationOutcome(
        iteration_index=idx,
        decision=decision,
        val_score=val,
        best_ever_score_before=best_before,
        best_ever_score_after=best_after,
        approval_decision=approval,
        approver_type=approver,
    )


def test_condition_result_empty_iterations():
    res = ConditionResult(
        condition=CONDITION_A_FROZEN,
        workflow_id="m5-condition-a",
        iterations=(),
    )
    assert res.n_iterations == 0
    assert res.best_ever_curve == ()
    assert res.final_best_ever is None
    assert res.n_gate_passes == 0
    assert res.n_approvals == 0


def test_condition_result_counts_decisions():
    its = (
        _outcome(0, "gate-pass", val=0.40, best_after=0.40, approval="approve", approver="autonomous"),
        _outcome(1, "gate-blocked-regression", val=0.35, best_after=0.40, best_before=0.40),
        _outcome(2, "gate-blocked-no-improvement", val=0.40, best_after=0.40, best_before=0.40),
        _outcome(3, "sandbox-error", val=None, best_after=0.40, best_before=0.40),
        _outcome(4, "gate-pass", val=0.45, best_after=0.45, best_before=0.40, approval="reject", approver="llm-judge"),
    )
    res = ConditionResult(
        condition=CONDITION_C_LOOP_AUTONOMOUS,
        workflow_id="m5-condition-c",
        iterations=its,
    )
    assert res.n_iterations == 5
    assert res.n_gate_passes == 2
    assert res.n_gate_blocked_regression == 1
    assert res.n_gate_blocked_no_improvement == 1
    assert res.n_sandbox_errors == 1
    assert res.n_approvals == 1
    assert res.n_rejections == 1


def test_condition_result_final_best_skips_trailing_nulls():
    """If the last iterations have ``best_ever_score_after = None`` (e.g. a
    sandbox-error that never updated the score), final_best_ever should
    fall back to the most recent populated score — that's what the lift
    chart's headline number represents."""
    its = (
        _outcome(0, "gate-pass", val=0.40, best_after=0.40),
        _outcome(1, "gate-pass", val=0.42, best_after=0.42),
        _outcome(2, "running", val=None, best_after=None),
    )
    res = ConditionResult("C", "m5-condition-c", its)
    assert res.final_best_ever == 0.42


def test_condition_result_lift_curve_preserves_nulls():
    """The lift curve passes None through so the chart can show gaps for
    sandbox-error / running iterations rather than smoothing them away."""
    its = (
        _outcome(0, val=0.40, best_after=0.40),
        _outcome(1, "sandbox-error", val=None, best_after=None),
        _outcome(2, "gate-pass", val=0.45, best_after=0.45),
    )
    res = ConditionResult("C", "wf", its)
    assert res.best_ever_curve == (0.40, None, 0.45)


def test_condition_result_to_dict_round_trip():
    its = (
        _outcome(0, val=0.40, best_after=0.40, approval="approve", approver="autonomous"),
    )
    res = ConditionResult("C", "wf", its)
    d = res.to_dict()
    assert d["condition"] == "C"
    assert d["workflow_id"] == "wf"
    assert d["n_iterations"] == 1
    assert d["n_approvals"] == 1
    assert d["final_best_ever"] == 0.40
    assert d["iterations"][0]["approval_decision"] == "approve"
    # JSON-serializable
    assert json.loads(json.dumps(d)) == d


# ---------------------------------------------------------------------------
# ThirtyDayReport
# ---------------------------------------------------------------------------


def _result(condition: str, *, final: float | None) -> ConditionResult:
    its = (
        _outcome(0, val=final, best_after=final),
    )
    return ConditionResult(condition, f"m5-condition-{condition.lower()}", its)


def test_thirty_day_report_lift_over_baseline():
    started = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)
    ended = started + timedelta(hours=2)
    report = ThirtyDayReport(
        conditions={
            CONDITION_A_FROZEN: _result("A", final=0.30),
            CONDITION_C_LOOP_AUTONOMOUS: _result("C", final=0.45),
            CONDITION_D_LOOP_GATED: _result("D", final=0.40),
        },
        started_at=started,
        ended_at=ended,
    )
    lift = report.lift_over_baseline()
    assert lift == {
        CONDITION_C_LOOP_AUTONOMOUS: pytest.approx(0.15),
        CONDITION_D_LOOP_GATED: pytest.approx(0.10),
    }
    assert report.wall_seconds == 7200.0


def test_thirty_day_report_lift_returns_empty_when_baseline_missing():
    """Without condition A or with A's final score == None, the lift gate
    can't be computed — the function returns {} rather than guessing."""
    report = ThirtyDayReport(
        conditions={
            CONDITION_C_LOOP_AUTONOMOUS: _result("C", final=0.45),
            CONDITION_D_LOOP_GATED: _result("D", final=0.40),
        },
        started_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )
    assert report.lift_over_baseline() == {}


def test_thirty_day_report_lift_handles_null_non_baseline():
    """A non-baseline condition with no final score (e.g. all iterations
    crashed) shows up as None in the lift dict — the caller distinguishes
    'missing' from '0.0 lift'."""
    report = ThirtyDayReport(
        conditions={
            CONDITION_A_FROZEN: _result("A", final=0.30),
            CONDITION_C_LOOP_AUTONOMOUS: _result("C", final=None),
        },
        started_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )
    assert report.lift_over_baseline() == {CONDITION_C_LOOP_AUTONOMOUS: None}


def test_thirty_day_report_to_dict_serializable():
    report = ThirtyDayReport(
        conditions={
            CONDITION_A_FROZEN: _result("A", final=0.30),
            CONDITION_C_LOOP_AUTONOMOUS: _result("C", final=0.45),
        },
        started_at=datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
    )
    d = report.to_dict()
    # Round-trip via JSON
    assert json.loads(json.dumps(d))["wall_seconds"] == 7200.0
    assert d["conditions"]["A"]["final_best_ever"] == 0.30
    assert d["lift_over_baseline"]["C"] == pytest.approx(0.15)


# ---------------------------------------------------------------------------
# _parse_loop_summary — extract trailing JSON from subprocess stdout
# ---------------------------------------------------------------------------


_LOOP_STDOUT_HAPPY = """seed: workflow=m5 registered=6/6 skipped=0/6
agent: model=claude-sonnet-4-6 base_url=https://...
agent: stop_reason=end_turn iterations=2 tool_calls=2 tool_errors=0
{
  "iteration_id": "abcd-1234",
  "iteration_index": 5,
  "decision": "gate-pass",
  "rationale": "passed three-step gate",
  "val_score": 0.42,
  "best_ever_score_before": 0.40,
  "best_ever_score_after": 0.42,
  "proposal_id": "ef01-2345",
  "proposal_state": "gate-passed",
  "audit_started_id": "aaaa",
  "audit_completed_id": "bbbb",
  "approval": null
}
"""


def test_parse_loop_summary_happy_path():
    parsed = _parse_loop_summary(_LOOP_STDOUT_HAPPY)
    assert parsed is not None
    assert parsed["iteration_index"] == 5
    assert parsed["decision"] == "gate-pass"
    assert parsed["val_score"] == 0.42


def test_parse_loop_summary_with_nested_braces():
    """Nested objects in the summary (e.g. ``approval`` with sub-fields)
    should round-trip via the bracket-counter walk-back."""
    stdout = """agent: stop_reason=end_turn
{
  "iteration_id": "x",
  "approval": {
    "approver_mode": "autonomous",
    "decision": "approve",
    "approval_id": "yz"
  }
}
"""
    parsed = _parse_loop_summary(stdout)
    assert parsed is not None
    assert parsed["approval"]["approver_mode"] == "autonomous"


def test_parse_loop_summary_empty_stdout():
    assert _parse_loop_summary("") is None


def test_parse_loop_summary_no_json_object():
    assert _parse_loop_summary("just some prose, no braces") is None


def test_parse_loop_summary_malformed_json_returns_none():
    """A truncated or otherwise malformed JSON object yields None rather
    than raising — the orchestrator treats it as 'subprocess produced no
    parseable summary' and continues."""
    stdout = """agent: stop_reason=end_turn
{
  "iteration_id": "x",
  "decision":
"""
    assert _parse_loop_summary(stdout) is None


def test_parse_loop_summary_picks_last_json_when_multiple():
    """If for some reason stdout contains multiple JSON objects, the
    function returns the trailing one (the loop's intended summary)."""
    stdout = """{"earlier": "object"}
some prose
{"iteration_index": 7, "decision": "gate-pass"}
"""
    parsed = _parse_loop_summary(stdout)
    assert parsed == {"iteration_index": 7, "decision": "gate-pass"}


# ---------------------------------------------------------------------------
# _to_float — Decimal coercion
# ---------------------------------------------------------------------------


def test_to_float_passes_through_none():
    assert _to_float(None) is None


def test_to_float_coerces_decimal_int_float():
    from decimal import Decimal

    assert _to_float(Decimal("0.42")) == pytest.approx(0.42)
    assert _to_float(0) == 0.0
    assert _to_float(0.42) == 0.42


# ---------------------------------------------------------------------------
# SubprocessResult — typed sanity
# ---------------------------------------------------------------------------


def test_subprocess_result_holds_summary_on_success():
    r = SubprocessResult(exit_code=0, summary={"x": 1}, stderr_tail="")
    assert r.summary == {"x": 1}
    assert r.exit_code == 0


def test_subprocess_result_no_summary_on_failure():
    r = SubprocessResult(exit_code=2, summary=None, stderr_tail="boom")
    assert r.summary is None
    assert r.stderr_tail == "boom"


# ---------------------------------------------------------------------------
# run_all_conditions_parallel — input validation (no DB / no subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_all_conditions_rejects_empty_specs():
    from ownevo_kernel.replay import run_all_conditions_parallel

    with pytest.raises(ValueError, match="at least one ConditionSpec"):
        await run_all_conditions_parallel([])


@pytest.mark.asyncio
async def test_run_all_conditions_rejects_duplicate_condition():
    from ownevo_kernel.replay import run_all_conditions_parallel

    specs = [
        ConditionSpec("C", "m5-condition-c", 1),
        ConditionSpec("C", "m5-condition-c-shadow", 1),
    ]
    with pytest.raises(ValueError, match="duplicate condition"):
        await run_all_conditions_parallel(specs)


@pytest.mark.asyncio
async def test_run_all_conditions_rejects_duplicate_workflow_id():
    from ownevo_kernel.replay import run_all_conditions_parallel

    specs = [
        ConditionSpec("C", "shared-workflow", 1),
        ConditionSpec("D", "shared-workflow", 1),
    ]
    with pytest.raises(ValueError, match="duplicate workflow_id"):
        await run_all_conditions_parallel(specs)


# ---------------------------------------------------------------------------
# run_condition_loop — condition A frozen-baseline early-return
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_condition_loop_condition_a_returns_empty_immediately():
    """Condition A is the frozen baseline — no agent loop. The orchestrator
    skips all subprocess work and returns an empty results list so the merge
    step reads whatever the seeded baseline already wrote to the DB."""
    from ownevo_kernel.replay import run_condition_loop

    spec = ConditionSpec(
        condition=CONDITION_A_FROZEN,
        workflow_id="m5-condition-a",
        n_iterations=30,
    )
    results = await run_condition_loop(spec)
    assert results == []
