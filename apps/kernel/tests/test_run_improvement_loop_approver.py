"""Pure-Python tests for the W6 ``--approver`` dispatch in
``scripts/run_improvement_loop.py`` (no DB / no Anthropic).

Covers:
  * ``_proposal_to_approval_case`` — Proposal → LabeledApprovalCase adapter
  * ``_run_post_gate_approval`` — three modes (``none`` / ``autonomous`` /
    ``llm-judge``), the gate-not-pass short-circuit, the judge-error
    safety reject, and the missing-judge-client guard
  * ``parse_args`` — new ``--approver`` flag and the
    llm-judge-requires-anthropic constraint
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from ownevo_kernel.approvers.llm_judge import (  # noqa: E402
    LLMJudgeApprovalJudgment,
    LabeledApprovalCase,
    NoLLMJudgeApprovalToolUseError,
)
from ownevo_kernel.approvers.llm_judge.judgment import (  # noqa: E402
    StructuralElement,
)
from ownevo_kernel.gate.result import GateDecision, GateResult  # noqa: E402
from ownevo_kernel.types import (  # noqa: E402
    ApproverType,
    Approval,
    Iteration,
    IterationState,
    Proposal,
    ProposalState,
)

from scripts import run_improvement_loop  # noqa: E402
from scripts.run_improvement_loop import (  # noqa: E402
    APPROVER_AUTONOMOUS,
    APPROVER_LLM_JUDGE,
    APPROVER_NONE,
    _proposal_to_approval_case,
    _run_post_gate_approval,
    parse_args,
)


_NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _build_persisted(
    *,
    decision: GateDecision = GateDecision.PASS,
    proposal_id: UUID | None = None,
    plain_language_summary: str = "swap lag_28 → rolling_7 for FOODS_3",
):
    """Construct a minimal PersistedGateRun for the dispatcher tests."""
    from ownevo_kernel.gate.persistence import PersistedGateRun

    pid = proposal_id or uuid4()
    iter_id = uuid4()
    iteration = Iteration(
        id=iter_id,
        workflow_id="m5-condition-c",
        iteration_index=3,
        state=IterationState.GATE_PASS
        if decision == GateDecision.PASS
        else IterationState.GATE_BLOCKED_REGRESSION,
        val_score=0.42,
        best_ever_score_after=0.42 if decision == GateDecision.PASS else None,
        started_at=_NOW,
    )
    proposal = Proposal(
        id=pid,
        iteration_id=iter_id,
        skill_id="m5.baseline.v1.feature_engineer",
        proposed_content="# python\n",
        plain_language_summary=plain_language_summary,
        state=ProposalState.GATE_PASSED
        if decision == GateDecision.PASS
        else ProposalState.GATE_FAILED,
        created_at=_NOW,
        state_updated_at=_NOW,
    )
    gate_result = GateResult(
        decision=decision,
        rationale="ok" if decision == GateDecision.PASS else "regressed",
        val_score=0.42,
        best_ever_score_before=0.40,
        best_ever_score_after=0.42 if decision == GateDecision.PASS else None,
        full_run=None,
        failed_prior_task_ids=(),
        promotable_task_ids=(),
    )
    return PersistedGateRun(
        gate_result=gate_result,
        iteration=iteration,
        proposal=proposal,
        audit_started_id=uuid4(),
        audit_completed_id=uuid4(),
    )


def _build_approval(*, decision: str, approver_type: ApproverType) -> Approval:
    return Approval(
        id=uuid4(),
        proposal_id=uuid4(),
        decided_by={
            ApproverType.AUTONOMOUS: "autonomous",
            ApproverType.LLM_JUDGE: "llm-judge:claude-opus-4-7",
            ApproverType.HUMAN: "human:test",
        }[approver_type],
        approver_type=approver_type,
        decision=decision,  # type: ignore[arg-type]
        comment=None,
        decided_at=_NOW,
    )


def _build_judgment(verdict: str, *, proposal_id: str) -> LLMJudgeApprovalJudgment:
    """Construct a real LLMJudgeApprovalJudgment so the dispatcher's
    .verdict access path is exercised end-to-end."""
    return LLMJudgeApprovalJudgment(
        proposal_id=proposal_id,
        cluster_referenced=StructuralElement(present=True, quote="addresses lag_28 cluster"),
        change_named=StructuralElement(present=True, quote="swap lag_28 for rolling_7"),
        metric_direction_stated=StructuralElement(present=True, quote="should improve val_score"),
        verdict=verdict,  # type: ignore[arg-type]
        rationale="all three structural elements present",
    )


# ---------------------------------------------------------------------------
# _proposal_to_approval_case — pure adapter
# ---------------------------------------------------------------------------


def test_proposal_to_approval_case_carries_proposal_id_and_summary():
    persisted = _build_persisted(plain_language_summary="rolling_7 swap")
    case = _proposal_to_approval_case(persisted, "agent narrative explaining the swap")
    assert isinstance(case, LabeledApprovalCase)
    assert case.case_id == str(persisted.proposal.id)
    assert case.proposal_summary == "rolling_7 swap"
    assert case.explanation == "agent narrative explaining the swap"
    # Sentinel cluster name + always-up direction documented in the helper
    # docstring (no failure-cluster context plumbed yet).
    assert case.cluster_name == run_improvement_loop._M5_CLUSTER_PLACEHOLDER
    assert case.metric_direction_expected == "up"


def test_proposal_to_approval_case_falls_back_to_summary_when_agent_text_blank():
    persisted = _build_persisted(plain_language_summary="non-empty summary")
    case = _proposal_to_approval_case(persisted, "")
    # explanation prefers agent_final_text but falls back to summary so the
    # judge has *something* to evaluate.
    assert case.explanation == "non-empty summary"


def test_proposal_to_approval_case_handles_blank_summary():
    """Defense-in-depth: if the proposal summary is also blank (shouldn't
    happen in production — gate persistence requires non-empty), fall back
    to a sentinel rather than crashing."""
    persisted = _build_persisted(plain_language_summary="")
    case = _proposal_to_approval_case(persisted, "")
    assert case.proposal_summary == "agent-proposed change"


# ---------------------------------------------------------------------------
# _run_post_gate_approval — APPROVER_NONE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_gate_approval_none_returns_none_no_dispatch():
    """The legacy bootstrap-loop default — no approval step. Returns None
    so the JSON summary's ``"approval"`` field is null; never touches DB."""
    persisted = _build_persisted()
    summary = await _run_post_gate_approval(
        conn=object(),  # never used
        persisted=persisted,
        agent_final_text="any",
        approver_mode=APPROVER_NONE,
        judge_client=None,
        judge_model="claude-opus-4-7",
    )
    assert summary is None


# ---------------------------------------------------------------------------
# _run_post_gate_approval — gate-not-pass short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_gate_approval_skips_when_gate_did_not_pass():
    """Only ``GateDecision.PASS`` proposals are eligible for approval
    (that's the proposal_state precondition in approve/reject_proposal).
    Other decisions short-circuit with a ``skipped`` marker."""
    persisted = _build_persisted(decision=GateDecision.FAIL_REGRESSION)
    summary = await _run_post_gate_approval(
        conn=object(),
        persisted=persisted,
        agent_final_text="",
        approver_mode=APPROVER_AUTONOMOUS,
        judge_client=None,
        judge_model="claude-opus-4-7",
    )
    assert summary == {
        "approver_mode": APPROVER_AUTONOMOUS,
        "skipped": "gate-not-pass",
        "gate_decision": "gate-blocked-regression",
    }


# ---------------------------------------------------------------------------
# _run_post_gate_approval — autonomous
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_gate_approval_autonomous_calls_approve_proposal(monkeypatch):
    """Condition C path: every gate-pass auto-approves via the approvals
    service with ``approver_type=AUTONOMOUS, decided_by='autonomous'``."""
    persisted = _build_persisted()

    fake_approve = AsyncMock(
        return_value=_build_approval(decision="approve", approver_type=ApproverType.AUTONOMOUS),
    )
    fake_reject = AsyncMock()
    monkeypatch.setattr(run_improvement_loop, "approve_proposal", fake_approve)
    monkeypatch.setattr(run_improvement_loop, "reject_proposal", fake_reject)

    sentinel_conn = object()
    summary = await _run_post_gate_approval(
        conn=sentinel_conn,
        persisted=persisted,
        agent_final_text="agent narrative",
        approver_mode=APPROVER_AUTONOMOUS,
        judge_client=None,
        judge_model="claude-opus-4-7",
    )

    fake_approve.assert_awaited_once()
    fake_reject.assert_not_awaited()
    call_kwargs = fake_approve.await_args.kwargs
    assert call_kwargs["proposal_id"] == persisted.proposal.id
    assert call_kwargs["decided_by"] == "autonomous"
    assert call_kwargs["approver_type"] == ApproverType.AUTONOMOUS
    # The conn argument is positional in approve_proposal.
    assert fake_approve.await_args.args[0] is sentinel_conn

    assert summary is not None
    assert summary["approver_mode"] == APPROVER_AUTONOMOUS
    assert summary["decision"] == "approve"
    assert summary["decided_by"] == "autonomous"


# ---------------------------------------------------------------------------
# _run_post_gate_approval — llm-judge happy paths + safety reject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_gate_approval_llm_judge_admit_calls_approve(monkeypatch):
    persisted = _build_persisted()
    judgment = _build_judgment("admit", proposal_id=str(persisted.proposal.id))
    fake_judge = AsyncMock(return_value=judgment)
    fake_approve = AsyncMock(
        return_value=_build_approval(decision="approve", approver_type=ApproverType.LLM_JUDGE),
    )
    fake_reject = AsyncMock()

    monkeypatch.setattr(run_improvement_loop, "judge_proposal_explanation", fake_judge)
    monkeypatch.setattr(run_improvement_loop, "approve_proposal", fake_approve)
    monkeypatch.setattr(run_improvement_loop, "reject_proposal", fake_reject)

    judge_client = object()  # AsyncAnthropic stand-in; only identity matters
    summary = await _run_post_gate_approval(
        conn=object(),
        persisted=persisted,
        agent_final_text="agent narrative referencing the cluster + change + direction",
        approver_mode=APPROVER_LLM_JUDGE,
        judge_client=judge_client,
        judge_model="claude-opus-4-7",
    )

    fake_judge.assert_awaited_once()
    # First positional arg is the client; case is the second.
    judge_args, judge_kwargs = fake_judge.await_args.args, fake_judge.await_args.kwargs
    assert judge_args[0] is judge_client
    assert isinstance(judge_args[1], LabeledApprovalCase)
    assert judge_args[1].case_id == str(persisted.proposal.id)
    assert judge_kwargs["model"] == "claude-opus-4-7"

    fake_approve.assert_awaited_once()
    approve_kwargs = fake_approve.await_args.kwargs
    assert approve_kwargs["approver_type"] == ApproverType.LLM_JUDGE
    assert approve_kwargs["decided_by"] == "llm-judge:claude-opus-4-7"
    assert approve_kwargs["comment"] == judgment.rationale

    fake_reject.assert_not_awaited()

    assert summary is not None
    assert summary["decision"] == "approve"
    assert summary["verdict"] == "admit"
    assert summary["rationale"] == judgment.rationale


@pytest.mark.asyncio
async def test_post_gate_approval_llm_judge_reject_calls_reject(monkeypatch):
    persisted = _build_persisted()
    judgment = _build_judgment("reject", proposal_id=str(persisted.proposal.id))
    fake_judge = AsyncMock(return_value=judgment)
    fake_approve = AsyncMock()
    fake_reject = AsyncMock(
        return_value=_build_approval(decision="reject", approver_type=ApproverType.LLM_JUDGE),
    )

    monkeypatch.setattr(run_improvement_loop, "judge_proposal_explanation", fake_judge)
    monkeypatch.setattr(run_improvement_loop, "approve_proposal", fake_approve)
    monkeypatch.setattr(run_improvement_loop, "reject_proposal", fake_reject)

    summary = await _run_post_gate_approval(
        conn=object(),
        persisted=persisted,
        agent_final_text="agent narrative",
        approver_mode=APPROVER_LLM_JUDGE,
        judge_client=object(),
        judge_model="claude-opus-4-7",
    )

    fake_reject.assert_awaited_once()
    fake_approve.assert_not_awaited()
    reject_kwargs = fake_reject.await_args.kwargs
    assert reject_kwargs["approver_type"] == ApproverType.LLM_JUDGE
    assert reject_kwargs["decided_by"] == "llm-judge:claude-opus-4-7"
    # The judge's rationale is preserved on the rejection so the audit
    # chain captures *why* it was rejected.
    assert reject_kwargs["comment"] == judgment.rationale

    assert summary is not None
    assert summary["decision"] == "reject"
    assert summary["verdict"] == "reject"


@pytest.mark.asyncio
async def test_post_gate_approval_llm_judge_error_defaults_to_safe_reject(monkeypatch):
    """If the judge errors mid-flight (network blip, validation error, etc.),
    the dispatcher rejects the proposal rather than silently auto-admitting.
    The audit comment captures the error type so operators can debug."""
    persisted = _build_persisted()

    async def boom(*args, **kwargs):
        raise NoLLMJudgeApprovalToolUseError(
            "model did not call predict",
            stop_reason="end_turn",
            content_preview="",
        )

    fake_approve = AsyncMock()
    fake_reject = AsyncMock(
        return_value=_build_approval(decision="reject", approver_type=ApproverType.LLM_JUDGE),
    )
    monkeypatch.setattr(run_improvement_loop, "judge_proposal_explanation", boom)
    monkeypatch.setattr(run_improvement_loop, "approve_proposal", fake_approve)
    monkeypatch.setattr(run_improvement_loop, "reject_proposal", fake_reject)

    summary = await _run_post_gate_approval(
        conn=object(),
        persisted=persisted,
        agent_final_text="agent narrative",
        approver_mode=APPROVER_LLM_JUDGE,
        judge_client=object(),
        judge_model="claude-opus-4-7",
    )

    fake_approve.assert_not_awaited()
    fake_reject.assert_awaited_once()
    reject_comment = fake_reject.await_args.kwargs["comment"]
    assert reject_comment.startswith("judge-error: NoLLMJudgeApprovalToolUseError")

    assert summary is not None
    assert summary["decision"] == "reject"
    assert summary["rationale"].startswith("judge-error: NoLLMJudgeApprovalToolUseError")


@pytest.mark.asyncio
async def test_post_gate_approval_llm_judge_requires_client():
    persisted = _build_persisted()
    with pytest.raises(ValueError, match="requires an Anthropic client"):
        await _run_post_gate_approval(
            conn=object(),
            persisted=persisted,
            agent_final_text="",
            approver_mode=APPROVER_LLM_JUDGE,
            judge_client=None,  # the constraint
            judge_model="claude-opus-4-7",
        )


@pytest.mark.asyncio
async def test_post_gate_approval_unknown_mode_raises():
    persisted = _build_persisted()
    with pytest.raises(ValueError, match="unknown approver_mode"):
        await _run_post_gate_approval(
            conn=object(),
            persisted=persisted,
            agent_final_text="",
            approver_mode="banana",
            judge_client=None,
            judge_model="claude-opus-4-7",
        )


# ---------------------------------------------------------------------------
# parse_args — new --approver flag and constraints
# ---------------------------------------------------------------------------


def test_parse_args_default_approver_is_none():
    args = parse_args(["--no-seed"])
    assert args.approver_mode == APPROVER_NONE
    assert args.judge_model == "claude-opus-4-7"


def test_parse_args_approver_autonomous():
    args = parse_args(["--no-seed", "--approver", APPROVER_AUTONOMOUS])
    assert args.approver_mode == APPROVER_AUTONOMOUS


def test_parse_args_approver_llm_judge_with_anthropic():
    args = parse_args([
        "--no-seed",
        "--approver", APPROVER_LLM_JUDGE,
        "--api-format", "anthropic",
        "--judge-model", "claude-sonnet-4-6",
    ])
    assert args.approver_mode == APPROVER_LLM_JUDGE
    assert args.judge_model == "claude-sonnet-4-6"


def test_parse_args_approver_llm_judge_rejects_openai_format():
    """Constraint: the judge calls Anthropic /v1/messages; an OpenAI-format
    backend can't serve it. Argparse exits 2 with a clear message."""
    with pytest.raises(SystemExit) as ei:
        parse_args([
            "--no-seed",
            "--approver", APPROVER_LLM_JUDGE,
            "--api-format", "openai",
        ])
    assert ei.value.code == 2


def test_parse_args_approver_rejects_unknown_value():
    with pytest.raises(SystemExit) as ei:
        parse_args(["--no-seed", "--approver", "banana"])
    assert ei.value.code == 2
