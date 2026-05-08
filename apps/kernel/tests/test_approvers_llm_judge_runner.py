"""Tests for `approvers.llm_judge.runner.run_llm_judge_approver_eval` (W5.2).

Drives the runner with a programmable fake judge so the aggregation
contract (agreement, per-bucket slicing, verdict distribution, stable
ordering) is pinned without touching the network."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from ownevo_kernel.approvers.llm_judge.fixtures import (
    LABELED_APPROVAL_CASES,
    LabeledApprovalCase,
)
from ownevo_kernel.approvers.llm_judge.judge import (
    TOOL_NAME,
    LLMJudgeApprovalJudgmentValidationError,
)
from ownevo_kernel.approvers.llm_judge.runner import (
    run_llm_judge_approver_eval,
)

# ---------------------------------------------------------------------------
# Programmable fake — emits a payload chosen by `decide(case)`.
# ---------------------------------------------------------------------------


def _make_payload(case: LabeledApprovalCase, verdict: str) -> dict:
    return {
        "schema_version": "0.1",
        "proposal_id": case.case_id,
        "cluster_referenced": {"present": verdict == "admit", "quote": ""},
        "change_named": {"present": verdict == "admit", "quote": ""},
        "metric_direction_stated": {
            "present": verdict == "admit",
            "quote": "",
        },
        "verdict": verdict,
        "rationale": f"Stub judgment: {verdict}",
    }


@dataclass
class _ProgrammableMessages:
    decide: Any
    n_calls: int = 0
    last_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        # Find the case by reading the user message we emitted.
        user_msg = kwargs["messages"][0]["content"]
        case = next(
            c for c in LABELED_APPROVAL_CASES if c.case_id in user_msg
        )
        verdict = self.decide(case)
        self.n_calls += 1
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name=TOOL_NAME,
                    input=_make_payload(case, verdict),
                    id=f"tu_{self.n_calls}",
                )
            ],
            stop_reason="tool_use",
        )


@dataclass
class _ProgrammableClient:
    decide: Any
    messages: _ProgrammableMessages = field(init=False)

    def __post_init__(self) -> None:
        self.messages = _ProgrammableMessages(self.decide)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


async def test_perfect_judge_agreement_one():
    """When the judge always matches ground truth, agreement = 1.0
    and per-bucket all-correct."""
    client = _ProgrammableClient(
        decide=lambda case: case.ground_truth_verdict
    )
    report = await run_llm_judge_approver_eval(client)
    assert report.n_total == 30
    assert report.n_correct == 30
    assert report.agreement == pytest.approx(1.0)
    for bucket, (correct, total) in report.per_bucket_correct.items():
        assert correct == total, bucket


async def test_always_reject_judge_agreement_matches_reject_share():
    """If the judge always rejects, agreement = (#reject ground-truths) / 30
    = 20/30 (10 admit ground-truths flip)."""
    client = _ProgrammableClient(decide=lambda _case: "reject")
    report = await run_llm_judge_approver_eval(client)
    assert report.n_correct == 20  # 8 vague + 6 wrong-dir + 6 hand-wavy
    assert report.agreement == pytest.approx(20 / 30)
    # Verdict distribution: all reject.
    assert report.verdict_distribution["reject"] == 30
    assert "admit" not in report.verdict_distribution


async def test_per_bucket_slicing_isolates_failure():
    """Programme judge to nail every bucket EXCEPT
    structural-but-wrong-direction (the spec'd adversarial case).
    Aggregate masks the regression; per-bucket exposes it."""

    def decide(case: LabeledApprovalCase) -> str:
        if case.bucket == "structural-but-wrong-direction":
            return "admit"  # wrong — ground truth is reject
        return case.ground_truth_verdict

    client = _ProgrammableClient(decide=decide)
    report = await run_llm_judge_approver_eval(client)
    assert report.n_correct == 24  # 30 - 6 (the wrong-direction misses)
    assert report.agreement == pytest.approx(24 / 30)
    assert report.per_bucket_correct["structural-but-wrong-direction"] == (0, 6)
    assert report.per_bucket_correct["structural"] == (10, 10)
    assert report.per_bucket_correct["vague-but-positive"] == (8, 8)
    assert report.per_bucket_correct["hand-wavy"] == (6, 6)


async def test_records_are_in_fixture_order():
    """Concurrent gather may complete out of order; runner must sort
    records back to fixture-order so the report is reproducible."""
    client = _ProgrammableClient(
        decide=lambda case: case.ground_truth_verdict
    )
    report = await run_llm_judge_approver_eval(client, concurrency=4)
    fixture_ids = [c.case_id for c in LABELED_APPROVAL_CASES]
    record_ids = [r.case_id for r in report.records]
    assert record_ids == fixture_ids


async def test_to_dict_round_trip_shape():
    client = _ProgrammableClient(
        decide=lambda case: case.ground_truth_verdict
    )
    report = await run_llm_judge_approver_eval(client)
    payload = report.to_dict()
    assert payload["n_total"] == 30
    assert payload["n_correct"] == 30
    assert payload["agreement"] == pytest.approx(1.0)
    assert "records" in payload
    assert len(payload["records"]) == 30
    assert "per_bucket_correct" in payload
    # JSON-friendly: lists not tuples.
    for bucket_data in payload["per_bucket_correct"].values():
        assert isinstance(bucket_data, list)


async def test_concurrency_must_be_positive():
    client = _ProgrammableClient(decide=lambda _: "admit")
    with pytest.raises(ValueError):
        await run_llm_judge_approver_eval(client, concurrency=0)


async def test_max_retries_must_be_non_negative():
    client = _ProgrammableClient(decide=lambda _: "admit")
    with pytest.raises(ValueError):
        await run_llm_judge_approver_eval(
            client, max_retries_per_call=-1
        )


async def test_empty_eval_set_rejected():
    client = _ProgrammableClient(decide=lambda _: "admit")
    with pytest.raises(ValueError):
        await run_llm_judge_approver_eval(client, eval_set=())


async def test_retry_on_validation_error():
    """Validation errors are retried; on success the runner records
    the retried judgment."""

    state: dict[str, int] = {"calls": 0}

    @dataclass
    class _RetryMessages:
        n_calls: int = 0

        async def create(self, **kwargs):
            self.n_calls += 1
            user_msg = kwargs["messages"][0]["content"]
            case = next(
                c for c in LABELED_APPROVAL_CASES if c.case_id in user_msg
            )
            state["calls"] = state.get("calls", 0) + 1
            if state["calls"] == 1:
                # First call: bad payload (wrong verdict literal)
                bad = _make_payload(case, "admit")
                bad["verdict"] = "maybe"
                return SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="tool_use",
                            name=TOOL_NAME,
                            input=bad,
                            id="tu_1",
                        )
                    ],
                    stop_reason="tool_use",
                )
            # Retry: good payload
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name=TOOL_NAME,
                        input=_make_payload(case, case.ground_truth_verdict),
                        id="tu_2",
                    )
                ],
                stop_reason="tool_use",
            )

    @dataclass
    class _OneCaseClient:
        messages: _RetryMessages = field(default_factory=_RetryMessages)

    client = _OneCaseClient()
    one_case = (LABELED_APPROVAL_CASES[0],)
    report = await run_llm_judge_approver_eval(
        client,
        eval_set=one_case,
        max_retries_per_call=1,
    )
    assert report.n_total == 1
    assert state["calls"] == 2  # one retry happened


async def test_no_retry_when_disabled():
    """With max_retries_per_call=0, a validation failure raises."""

    @dataclass
    class _BadMessages:
        async def create(self, **kwargs):
            user_msg = kwargs["messages"][0]["content"]
            case = next(
                c for c in LABELED_APPROVAL_CASES if c.case_id in user_msg
            )
            bad = _make_payload(case, "admit")
            bad["verdict"] = "maybe"
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name=TOOL_NAME,
                        input=bad,
                        id="tu_1",
                    )
                ],
                stop_reason="tool_use",
            )

    @dataclass
    class _BadClient:
        messages: _BadMessages = field(default_factory=_BadMessages)

    client = _BadClient()
    one_case = (LABELED_APPROVAL_CASES[0],)
    with pytest.raises(LLMJudgeApprovalJudgmentValidationError):
        await run_llm_judge_approver_eval(
            client,
            eval_set=one_case,
            max_retries_per_call=0,
        )
