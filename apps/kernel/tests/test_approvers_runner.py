"""Tests for `approvers.runner.run_judge_eval` (W5.2).

The runner orchestrates many judge calls; we mock `judge_proposal`
directly so the tests focus on aggregation logic, ordering, and
per-bucket / per-check slicing.
"""

from __future__ import annotations

import json

import pytest
from ownevo_kernel.approvers import runner as runner_module
from ownevo_kernel.approvers.eval_set import JUDGE_EVAL_SET
from ownevo_kernel.approvers.judgment import ApprovalJudgment
from ownevo_kernel.approvers.llm_judge import (
    JudgmentValidationError,
)
from ownevo_kernel.approvers.runner import (
    JudgeEvalReport,
    run_judge_eval,
)


def _judgment(
    proposal_id: str,
    *,
    refs: str = "pass",
    names: str = "pass",
    direction: str = "pass",
) -> ApprovalJudgment:
    return ApprovalJudgment.model_validate(
        {
            "schema_version": "0.1",
            "proposal_id": proposal_id,
            "references_cluster": {"verdict": refs, "rationale": "x"},
            "names_change": {"verdict": names, "rationale": "x"},
            "states_direction": {"verdict": direction, "rationale": "x"},
            "overall_rationale": "test",
        }
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


async def test_runner_returns_record_per_pair(monkeypatch):
    """30 records, one per pair."""

    async def always_admit(client, ctx, **kw):
        return _judgment(ctx.proposal_id)

    monkeypatch.setattr(runner_module, "judge_proposal", always_admit)
    report = await run_judge_eval(client=None)
    assert isinstance(report, JudgeEvalReport)
    assert report.n_total == 30
    assert len(report.records) == 30


async def test_oracle_judge_hits_perfect_agreement(monkeypatch):
    """Oracle judge returns the bucket-derived ground truth every time —
    every record correct, agreement 1.0."""

    async def oracle(client, ctx, **kw):
        # Find the pair to know what bucket we're in.
        pair = next(
            p for p in JUDGE_EVAL_SET if p.context.proposal_id == ctx.proposal_id
        )
        if pair.expected_admit:
            # admit-structural-correct → all three pass
            return _judgment(ctx.proposal_id, refs="pass", names="pass", direction="pass")
        # Reject buckets — fail the dimension that defines the bucket.
        if pair.bucket_id == "reject-vague-positive":
            return _judgment(ctx.proposal_id, refs="fail", names="fail", direction="fail")
        if pair.bucket_id == "reject-wrong-direction":
            return _judgment(ctx.proposal_id, refs="pass", names="pass", direction="fail")
        if pair.bucket_id == "reject-handwavy-change":
            return _judgment(ctx.proposal_id, refs="pass", names="fail", direction="pass")
        if pair.bucket_id == "reject-missing-cluster":
            return _judgment(ctx.proposal_id, refs="fail", names="pass", direction="pass")
        raise AssertionError(f"unreachable bucket {pair.bucket_id}")

    monkeypatch.setattr(runner_module, "judge_proposal", oracle)
    report = await run_judge_eval(client=None)
    assert report.agreement == 1.0
    assert report.n_correct == 30
    assert report.n_admit_correct == 6
    assert report.n_reject_correct == 24


async def test_always_admit_judge_hits_admit_bucket_only(monkeypatch):
    """Always-admit: gets the 6 admits right, the 24 rejects wrong.
    Agreement 6/30 = 0.2."""

    async def always_admit(client, ctx, **kw):
        return _judgment(ctx.proposal_id)

    monkeypatch.setattr(runner_module, "judge_proposal", always_admit)
    report = await run_judge_eval(client=None)
    assert report.n_correct == 6
    assert report.agreement == pytest.approx(6 / 30)
    assert report.n_admit_correct == 6
    assert report.n_reject_correct == 0


async def test_always_reject_judge_hits_reject_buckets_only(monkeypatch):
    """Always-reject: gets the 24 rejects right, the 6 admits wrong.
    Agreement 24/30 = 0.8."""

    async def always_reject(client, ctx, **kw):
        return _judgment(ctx.proposal_id, refs="fail", names="fail", direction="fail")

    monkeypatch.setattr(runner_module, "judge_proposal", always_reject)
    report = await run_judge_eval(client=None)
    assert report.n_correct == 24
    assert report.agreement == pytest.approx(24 / 30)
    assert report.n_admit_correct == 0
    assert report.n_reject_correct == 24


async def test_per_bucket_correctness(monkeypatch):
    """Judge that admits everything: per-bucket correctness should be
    6/6 on the admit bucket and 0/6 on each reject bucket."""

    async def always_admit(client, ctx, **kw):
        return _judgment(ctx.proposal_id)

    monkeypatch.setattr(runner_module, "judge_proposal", always_admit)
    report = await run_judge_eval(client=None)
    assert report.per_bucket_correct["admit-structural-correct"] == (6, 6)
    for b in (
        "reject-vague-positive",
        "reject-wrong-direction",
        "reject-handwavy-change",
        "reject-missing-cluster",
    ):
        assert report.per_bucket_correct[b] == (0, 6)


async def test_per_check_distribution_counts(monkeypatch):
    """Stamp every judgment with refs=fail, names=pass, direction=fail.
    Aggregates should reflect 30 of each."""

    async def stamper(client, ctx, **kw):
        return _judgment(ctx.proposal_id, refs="fail", names="pass", direction="fail")

    monkeypatch.setattr(runner_module, "judge_proposal", stamper)
    report = await run_judge_eval(client=None)
    assert dict(report.per_check_distribution["references_cluster"]) == {"fail": 30}
    assert dict(report.per_check_distribution["names_change"]) == {"pass": 30}
    assert dict(report.per_check_distribution["states_direction"]) == {"fail": 30}


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


async def test_to_dict_is_json_serializable(monkeypatch):
    async def stamper(client, ctx, **kw):
        return _judgment(ctx.proposal_id)

    monkeypatch.setattr(runner_module, "judge_proposal", stamper)
    report = await run_judge_eval(client=None)
    payload = report.to_dict()
    json.dumps(payload, sort_keys=True)  # must not raise
    assert payload["n_total"] == 30
    assert "records" in payload
    assert len(payload["records"]) == 30
    # Per-bucket correct serialized as list (JSON-friendly)
    for v in payload["per_bucket_correct"].values():
        assert isinstance(v, list)
        assert len(v) == 2


async def test_records_carry_bucket_and_expected_admit(monkeypatch):
    async def stamper(client, ctx, **kw):
        return _judgment(ctx.proposal_id)

    monkeypatch.setattr(runner_module, "judge_proposal", stamper)
    report = await run_judge_eval(client=None)
    for r in report.records:
        assert r.bucket_id in {
            "admit-structural-correct",
            "reject-vague-positive",
            "reject-wrong-direction",
            "reject-handwavy-change",
            "reject-missing-cluster",
        }
        # expected_admit ↔ bucket
        assert r.expected_admit == (r.bucket_id == "admit-structural-correct")


async def test_records_in_eval_set_order(monkeypatch):
    """Records come back in the same order as the eval set, regardless
    of concurrency. Surface a stable order so a CI report's record list
    is diff-friendly."""

    async def stamper(client, ctx, **kw):
        return _judgment(ctx.proposal_id)

    monkeypatch.setattr(runner_module, "judge_proposal", stamper)
    # Concurrency > 1 to scramble completion order
    report = await run_judge_eval(client=None, concurrency=8)
    assert [r.pair_id for r in report.records] == [
        p.pair_id for p in JUDGE_EVAL_SET
    ]


async def test_record_correctness_matches_admits_property(monkeypatch):
    """`record.correct` should be `(judgment.admits == pair.expected_admit)`."""

    async def admit_admits_only(client, ctx, **kw):
        # All-pass judgment → admits=True; correct iff pair.expected_admit
        return _judgment(ctx.proposal_id)

    monkeypatch.setattr(runner_module, "judge_proposal", admit_admits_only)
    report = await run_judge_eval(client=None)
    for r in report.records:
        # actual_admit = True (always-pass), correct iff expected_admit
        assert r.actual_admit is True
        assert r.correct == r.expected_admit


# ---------------------------------------------------------------------------
# Custom eval set
# ---------------------------------------------------------------------------


async def test_runner_accepts_custom_eval_set(monkeypatch):
    """Pass a 1-record eval set; runner returns 1 record."""
    custom = JUDGE_EVAL_SET[:1]

    async def stamper(client, ctx, **kw):
        return _judgment(ctx.proposal_id)

    monkeypatch.setattr(runner_module, "judge_proposal", stamper)
    report = await run_judge_eval(client=None, eval_set=custom)
    assert report.n_total == 1


# ---------------------------------------------------------------------------
# Concurrency validation
# ---------------------------------------------------------------------------


async def test_concurrency_zero_rejected():
    with pytest.raises(ValueError, match="concurrency"):
        await run_judge_eval(client=None, concurrency=0)


# ---------------------------------------------------------------------------
# Retry on validation error
# ---------------------------------------------------------------------------


async def test_retry_on_judgment_validation_error(monkeypatch):
    """`max_retries_per_call=1` retries once on JudgmentValidationError;
    second-call success returns a normal record."""
    pid = JUDGE_EVAL_SET[0].context.proposal_id
    attempts = {"n": 0}

    async def flaky(client, ctx, **kw):
        attempts["n"] += 1
        if attempts["n"] == 1 and ctx.proposal_id == pid:
            raise JudgmentValidationError(
                "transient",
                raw_input={"x": 1},
                pydantic_error=_pydantic_error(),
            )
        return _judgment(ctx.proposal_id)

    monkeypatch.setattr(runner_module, "judge_proposal", flaky)
    report = await run_judge_eval(
        client=None,
        eval_set=JUDGE_EVAL_SET[:2],
        max_retries_per_call=1,
    )
    assert report.n_total == 2
    # The flaky call retried — total attempts = 1 retry + 1 normal call
    # for pid + 1 call for the second pair = 3
    assert attempts["n"] == 3


async def test_retry_zero_propagates_validation_error(monkeypatch):
    """Default max_retries=0 — validation errors propagate immediately."""

    async def always_fail(client, ctx, **kw):
        raise JudgmentValidationError(
            "transient",
            raw_input={"x": 1},
            pydantic_error=_pydantic_error(),
        )

    monkeypatch.setattr(runner_module, "judge_proposal", always_fail)
    with pytest.raises(JudgmentValidationError):
        await run_judge_eval(client=None, eval_set=JUDGE_EVAL_SET[:1])


def _pydantic_error():
    """Stand-in pydantic ValidationError for retry tests.

    `JudgmentValidationError.pydantic_error.error_count()` is the only
    attribute the runner reads; build the smallest ValidationError that
    satisfies it.
    """
    from pydantic import BaseModel, ValidationError

    class _Tiny(BaseModel):
        x: int

    try:
        _Tiny.model_validate({"x": "not-int"})
    except ValidationError as e:
        return e
    raise AssertionError("ValidationError did not fire")  # pragma: no cover
