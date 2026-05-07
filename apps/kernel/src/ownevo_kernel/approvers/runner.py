"""Judge-eval runner — drives the LLM judge across `JUDGE_EVAL_SET` (W5.2).

The runner takes an `AsyncAnthropic` client, calls `judge_proposal`
on every record in the eval set, collects per-record results, and
aggregates judge-vs-human agreement.

Agreement = fraction of records where `judgment.admits == expected_admit`.
W5.2's gate is `agreement ≥ 0.85` (higher than meta-eval's 0.7 because
false-positives drift the M5 lift the wrong direction).

Per-bucket correctness is also aggregated so a future regression on
one specific failure mode (e.g., the judge starts admitting wrong-
direction proposals) is visible without re-reading every record.

Lives alongside `llm_judge.py` in the `agent` extra path — the runner
imports `judge_proposal` directly. The eval set itself
(`eval_set.JUDGE_EVAL_SET`) is kernel-runtime, no anthropic dep.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .eval_set import JUDGE_EVAL_SET, JudgeBucketId, JudgeEvalPair
from .judgment import ApprovalJudgment
from .llm_judge import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    JudgmentValidationError,
    judge_proposal,
)

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


@dataclass(frozen=True)
class JudgeEvalRecord:
    """One judge call: which pair, what the judge said.

    `correct` is the judge-vs-human binary on the admit decision; the
    aggregate agreement is the mean of `correct` across all records.
    """

    pair_id: str
    bucket_id: JudgeBucketId
    expected_admit: bool
    judgment: ApprovalJudgment
    actual_admit: bool
    correct: bool


@dataclass(frozen=True)
class JudgeEvalReport:
    """Aggregated judge-eval results over the eval set.

    Surfaced by the runner + the CLI. JSON-serializable via `to_dict`.
    """

    records: tuple[JudgeEvalRecord, ...]
    n_total: int
    n_correct: int
    agreement: float
    """`n_correct / n_total`. The single-number summary W5.2 grades
    against (≥0.85 is the W5.2 gate)."""

    per_bucket_correct: dict[JudgeBucketId, tuple[int, int]]
    """`bucket_id → (correct, total)`. Surfaces which bucket the judge
    struggles with — particularly the `reject-wrong-direction` bucket,
    which is the highest-leverage adversarial case."""

    per_check_distribution: dict[str, Counter]
    """Per-check verdict histogram across all records. Keys are
    `references_cluster` / `names_change` / `states_direction`; values
    map verdict (`pass` / `fail`) → count. Useful for spotting "judge
    is calling everything `pass`" calibration drift."""

    n_admit_correct: int
    """Count of correctly-admitted records (true positives — bucket A
    members the judge admitted). Surfaced separately so a regression
    on the admit side (false negatives = correct proposals rejected,
    which kills the lift curve) is visible."""

    n_reject_correct: int
    """Count of correctly-rejected records (true negatives — buckets
    B/C/D/E members the judge rejected). Surfaced separately so a
    regression on the reject side (false positives = bad proposals
    admitted, which drifts the lift the wrong way) is visible."""

    model: str
    """Which model produced these judgments. Recorded so a CI run can
    distinguish opus-vs-haiku judge runs in the audit trail."""

    def to_dict(self) -> dict:
        """JSON-serializable summary. Records included as a list; if
        the caller wants summary-only output they can pop `records`."""
        return {
            "model": self.model,
            "n_total": self.n_total,
            "n_correct": self.n_correct,
            "agreement": self.agreement,
            "n_admit_correct": self.n_admit_correct,
            "n_reject_correct": self.n_reject_correct,
            "per_bucket_correct": {
                k: list(v) for k, v in self.per_bucket_correct.items()
            },
            "per_check_distribution": {
                k: dict(c) for k, c in self.per_check_distribution.items()
            },
            "records": [
                {
                    "pair_id": r.pair_id,
                    "bucket_id": r.bucket_id,
                    "expected_admit": r.expected_admit,
                    "actual_admit": r.actual_admit,
                    "correct": r.correct,
                    "judgment": r.judgment.model_dump(mode="json"),
                }
                for r in self.records
            ],
        }


def _aggregate(
    records: list[JudgeEvalRecord],
    eval_set: list[JudgeEvalPair],
    model: str,
) -> JudgeEvalReport:
    n_total = len(records)
    n_correct = sum(1 for r in records if r.correct)
    agreement = n_correct / n_total if n_total else 0.0

    per_check: dict[str, Counter] = {
        "references_cluster": Counter(),
        "names_change": Counter(),
        "states_direction": Counter(),
    }
    for r in records:
        per_check["references_cluster"][r.judgment.references_cluster.verdict] += 1
        per_check["names_change"][r.judgment.names_change.verdict] += 1
        per_check["states_direction"][r.judgment.states_direction.verdict] += 1

    per_bucket: dict[JudgeBucketId, list[int]] = {}
    for r in records:
        bucket = per_bucket.setdefault(r.bucket_id, [0, 0])
        bucket[1] += 1  # total
        if r.correct:
            bucket[0] += 1  # correct
    per_bucket_correct: dict[JudgeBucketId, tuple[int, int]] = {
        k: (v[0], v[1]) for k, v in per_bucket.items()
    }

    n_admit_correct = sum(
        1 for r in records if r.expected_admit and r.correct
    )
    n_reject_correct = sum(
        1 for r in records if not r.expected_admit and r.correct
    )

    return JudgeEvalReport(
        records=tuple(records),
        n_total=n_total,
        n_correct=n_correct,
        agreement=agreement,
        per_bucket_correct=per_bucket_correct,
        per_check_distribution=per_check,
        n_admit_correct=n_admit_correct,
        n_reject_correct=n_reject_correct,
        model=model,
    )


async def _judge_one(
    client: AsyncAnthropic,
    pair: JudgeEvalPair,
    model: str,
    max_tokens: int,
    max_retries: int = 0,
) -> JudgeEvalRecord:
    """Drive the judge for one pair.

    `max_retries` retries on `JudgmentValidationError` only — the typed
    error fires when the model produces malformed JSON or drops a
    required field, which empirically is transient (~5-10% of opus 4.7
    calls per the A4.6 live smoke). Other errors (`NoJudgeToolUseError`,
    `JudgeProposalIdMismatchError`, anthropic API errors) are not
    retried — they signal real misconfiguration rather than model
    output drift.
    """
    last_exc: JudgmentValidationError | None = None
    for attempt in range(max_retries + 1):
        try:
            judgment = await judge_proposal(
                client,
                pair.context,
                model=model,
                max_tokens=max_tokens,
            )
            break
        except JudgmentValidationError as exc:
            last_exc = exc
            if attempt < max_retries:
                _log.warning(
                    "judge validation failed for pair=%s attempt=%d; "
                    "retrying (errors=%d)",
                    pair.pair_id,
                    attempt,
                    exc.pydantic_error.error_count(),
                )
                continue
            raise
    else:  # pragma: no cover — unreachable; loop always breaks or raises
        assert last_exc is not None
        raise last_exc

    actual_admit = judgment.admits
    return JudgeEvalRecord(
        pair_id=pair.pair_id,
        bucket_id=pair.bucket_id,
        expected_admit=pair.expected_admit,
        judgment=judgment,
        actual_admit=actual_admit,
        correct=(actual_admit == pair.expected_admit),
    )


async def run_judge_eval(
    client: AsyncAnthropic,
    *,
    eval_set: list[JudgeEvalPair] = JUDGE_EVAL_SET,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    concurrency: int = 1,
    max_retries_per_call: int = 0,
) -> JudgeEvalReport:
    """Run the judge across every pair and aggregate.

    Args:
        client: AsyncAnthropic client.
        eval_set: Defaults to `JUDGE_EVAL_SET` (the 30 pairs the W5.2
            deliverable ships). Override for unit tests or custom evals.
        model: Anthropic model id; default opus 4.7 (calibration anchor).
        max_tokens: Per-call output cap.
        concurrency: Number of judge calls to run in parallel. Default
            1 (sequential — easiest to reason about + cheapest path
            against rate limits). Bump to 4-8 for faster CI runs.
        max_retries_per_call: Retries on `JudgmentValidationError` only.
            Default 0 (current strict behavior). Bump to 1 for live
            runs against opus 4.7 — the model occasionally returns
            malformed JSON in the string-wrapped payload (~5-10% of
            calls); a single retry resolves it.

    Returns:
        `JudgeEvalReport` with per-pair records + aggregates.

    Raises:
        Re-raises any exception from `judge_proposal` (NoJudgeToolUseError,
        JudgmentValidationError, JudgeProposalIdMismatchError, anthropic
        API errors). The runner doesn't swallow per-pair failures
        because partial reports would mislead the agreement number.
    """
    if concurrency < 1:
        raise ValueError(f"concurrency must be ≥1; got {concurrency}")

    sem = asyncio.Semaphore(concurrency)

    async def _bounded(pair: JudgeEvalPair) -> JudgeEvalRecord:
        async with sem:
            return await _judge_one(
                client,
                pair,
                model,
                max_tokens,
                max_retries=max_retries_per_call,
            )

    tasks = [_bounded(p) for p in eval_set]
    records = await asyncio.gather(*tasks)
    # Stable order: same as the eval-set iteration order.
    pair_order = {p.pair_id: i for i, p in enumerate(eval_set)}
    records_sorted = sorted(records, key=lambda r: pair_order[r.pair_id])
    return _aggregate(records_sorted, eval_set, model)


__all__ = [
    "JudgeEvalRecord",
    "JudgeEvalReport",
    "run_judge_eval",
]
