"""LLM-judge approver eval runner — drives the judge across the
fixture set (W5.2).

For each `LabeledApprovalCase`:

  1. Call the judge (default: opus 4.7) on the case to produce an
     `LLMJudgeApprovalJudgment`.
  2. Compare the judgment's binary `verdict` against the fixture's
     `ground_truth_verdict` to produce `correct`.
  3. Record both for the audit trail.

Aggregate: agreement = mean(correct) across the fixture set. The
W5.2 exit criterion is `agreement ≥ 0.85`. Higher than B3.5's 0.7
because false-positives (admit when the explanation is vague) drift
M5 lift the wrong direction in unattended benchmark runs.

Per-bucket slicing (`bucket → (correct, total)`) catches:

  * "Judge gets all the structural cases right but flips on every
    structural-but-wrong-direction case" — the dominant adversarial
    mode the spec calls out.
  * "Judge admits every vague-but-positive explanation" — symmetric
    failure that aggregate-only would mask if the structural set
    were small.

Lives alongside `judge.py` in the `agent` extra path. The eval set
(`fixtures.LABELED_APPROVAL_CASES`) is kernel-runtime, no anthropic
dep.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .fixtures import LABELED_APPROVAL_CASES, LabeledApprovalCase
from .judge import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    LLMJudgeApprovalJudgmentValidationError,
    judge_proposal_explanation,
)
from .judgment import LLMJudgeApprovalJudgment

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


@dataclass(frozen=True)
class LLMJudgeApprovalEvalRecord:
    """One eval call: which case, what the judge said, whether it agreed.

    `correct` is the binary judge-vs-human signal — 1 when
    `judgment.verdict == case.ground_truth_verdict`, 0 otherwise.
    Aggregate agreement = mean(correct) across records.
    """

    case_id: str
    bucket: str
    ground_truth_verdict: str
    judgment: LLMJudgeApprovalJudgment
    correct: bool


@dataclass(frozen=True)
class LLMJudgeApprovalEvalReport:
    """Aggregated approver-eval results.

    Surfaced by the runner + the CLI. JSON-serializable via `to_dict`.
    """

    records: tuple[LLMJudgeApprovalEvalRecord, ...]
    n_total: int
    n_correct: int
    agreement: float
    """`n_correct / n_total`. The single-number summary the W5.2 ≥0.85
    gate measures."""

    per_bucket_correct: dict[str, tuple[int, int]]
    """`bucket → (correct, total)`. Catches "judge is great on
    structural cases but flips on every wrong-direction case" — a
    regression the aggregate would average out."""

    verdict_distribution: Counter
    """Histogram of judge verdicts across all records (`admit` /
    `reject`). Useful for spotting calibration drift ("judge is calling
    everything reject, agreement looks high but is meaningless")."""

    judge_model: str

    def to_dict(self) -> dict:
        """JSON-serializable summary. Records are included as a list;
        the CLI may pop them when only the aggregate is needed."""
        return {
            "judge_model": self.judge_model,
            "n_total": self.n_total,
            "n_correct": self.n_correct,
            "agreement": self.agreement,
            "per_bucket_correct": {
                k: list(v) for k, v in self.per_bucket_correct.items()
            },
            "verdict_distribution": dict(self.verdict_distribution),
            "records": [
                {
                    "case_id": r.case_id,
                    "bucket": r.bucket,
                    "ground_truth_verdict": r.ground_truth_verdict,
                    "correct": r.correct,
                    "judgment": r.judgment.model_dump(mode="json"),
                }
                for r in self.records
            ],
        }


def _aggregate(
    records: list[LLMJudgeApprovalEvalRecord],
    judge_model: str,
) -> LLMJudgeApprovalEvalReport:
    n_total = len(records)
    n_correct = sum(1 for r in records if r.correct)
    agreement = n_correct / n_total if n_total else 0.0

    per_bucket: dict[str, list[int]] = {}
    verdicts: Counter = Counter()
    for r in records:
        bucket = per_bucket.setdefault(r.bucket, [0, 0])
        bucket[1] += 1
        if r.correct:
            bucket[0] += 1
        verdicts[r.judgment.verdict] += 1

    return LLMJudgeApprovalEvalReport(
        records=tuple(records),
        n_total=n_total,
        n_correct=n_correct,
        agreement=agreement,
        per_bucket_correct={k: (v[0], v[1]) for k, v in per_bucket.items()},
        verdict_distribution=verdicts,
        judge_model=judge_model,
    )


async def _eval_one_case(
    client: AsyncAnthropic,
    case: LabeledApprovalCase,
    judge_model: str,
    judge_max_tokens: int,
    max_retries: int,
) -> LLMJudgeApprovalEvalRecord:
    """Run the judge on one fixture.

    `max_retries` retries on `LLMJudgeApprovalJudgmentValidationError`
    only — same rationale as A4.6 / B3.5 (transient malformed-JSON
    returns from the model). Other errors (no-tool-use, id-mismatch,
    anthropic API errors) are not retried — they signal real
    misconfiguration.
    """
    last_exc: LLMJudgeApprovalJudgmentValidationError | None = None
    judgment: LLMJudgeApprovalJudgment | None = None
    for attempt in range(max_retries + 1):
        try:
            judgment = await judge_proposal_explanation(
                client,
                case,
                model=judge_model,
                max_tokens=judge_max_tokens,
            )
            break
        except LLMJudgeApprovalJudgmentValidationError as exc:
            last_exc = exc
            if attempt < max_retries:
                _log.warning(
                    "judge validation failed for case_id=%s attempt=%d; "
                    "retrying (errors=%d)",
                    case.case_id,
                    attempt,
                    exc.pydantic_error.error_count(),
                )
                continue
            raise
    else:  # pragma: no cover — unreachable; loop always breaks or raises
        assert last_exc is not None
        raise last_exc

    assert judgment is not None
    return LLMJudgeApprovalEvalRecord(
        case_id=case.case_id,
        bucket=case.bucket,
        ground_truth_verdict=case.ground_truth_verdict,
        judgment=judgment,
        correct=(judgment.verdict == case.ground_truth_verdict),
    )


async def run_llm_judge_approver_eval(
    client: AsyncAnthropic,
    *,
    eval_set: tuple[LabeledApprovalCase, ...] = LABELED_APPROVAL_CASES,
    judge_model: str = DEFAULT_MODEL,
    judge_max_tokens: int = DEFAULT_MAX_TOKENS,
    concurrency: int = 1,
    max_retries_per_call: int = 0,
) -> LLMJudgeApprovalEvalReport:
    """Run the judge across every case and aggregate.

    Args:
        client: AsyncAnthropic client for the judge.
        eval_set: Defaults to `LABELED_APPROVAL_CASES` (the 30-case
            fixture set). Override for unit tests or custom evals.
        judge_model: Anthropic model id; default opus 4.7.
        judge_max_tokens: Per-call output cap.
        concurrency: Number of judge calls to run in parallel.
            Default 1 (sequential). Bump to 4-8 for faster CI runs;
            6 is a reasonable target on the default account quota.
        max_retries_per_call: Retries on
            `LLMJudgeApprovalJudgmentValidationError` only. Default 0;
            bump to 1 for live runs against weaker judges.

    Returns:
        `LLMJudgeApprovalEvalReport` with per-case records + aggregates.

    Raises:
        Re-raises any exception from `judge_proposal_explanation`.
        Partial reports are not produced: a single failure means the
        agreement number would be misleading.
    """
    if concurrency < 1:
        raise ValueError(f"concurrency must be ≥1; got {concurrency}")
    if max_retries_per_call < 0:
        raise ValueError(
            f"max_retries_per_call must be ≥0; got {max_retries_per_call}"
        )
    if not eval_set:
        raise ValueError("eval_set must not be empty")

    sem = asyncio.Semaphore(concurrency)

    async def _bounded(case: LabeledApprovalCase) -> LLMJudgeApprovalEvalRecord:
        async with sem:
            return await _eval_one_case(
                client,
                case,
                judge_model,
                judge_max_tokens,
                max_retries_per_call,
            )

    tasks = [_bounded(case) for case in eval_set]
    records = await asyncio.gather(*tasks)
    # Stable order: same as the eval-set iteration order.
    case_order = {c.case_id: i for i, c in enumerate(eval_set)}
    records_sorted = sorted(records, key=lambda r: case_order[r.case_id])
    return _aggregate(records_sorted, judge_model)


__all__ = [
    "LLMJudgeApprovalEvalRecord",
    "LLMJudgeApprovalEvalReport",
    "run_llm_judge_approver_eval",
]
