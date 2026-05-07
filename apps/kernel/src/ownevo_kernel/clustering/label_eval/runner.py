"""Cluster-label eval runner — drives the labeler + judge across the
fixture set (B3.5).

For each `LabeledClusterCase`:

  1. Call the labeler (default: `AnthropicLabeler` on haiku 4.5,
     wrapped to be async) on the case's `member_signatures` to produce
     a candidate label — same code path the production cluster card
     hits.
  2. Call the judge (default: sonnet 4.6) on (case, candidate_label)
     to produce a `ClusterLabelJudgment`.
  3. Record both the candidate and the judgment.

Aggregate: agreement = mean(judgment.verdict == "agree") across the
fixture set. The W3 Track B exit criterion is `agreement ≥ 0.7`.

Per-hint slicing (`dominant_hint` → (correct, total)) catches "labeler
is fine on under-forecast clusters but mislabels every flat-prediction
cluster" — a regression that aggregate-only metrics would mask.

Lives alongside `judge.py` in the `agent` extra path. The eval set
(`fixtures.LABELED_CLUSTER_CASES`) is kernel-runtime, no anthropic dep.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .fixtures import LABELED_CLUSTER_CASES, LabeledClusterCase
from .judge import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    ClusterLabelJudgmentValidationError,
    judge_label_match,
)
from .judgment import ClusterLabelJudgment

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

    from ..types import Labeler


LabelFn = Callable[[list[str], int], Awaitable[str]]
"""Async-callable labeler signature.

Mirrors the sync `Labeler.label(sample_texts, cluster_index) -> str`
shape in `clustering/types.py` but returns an awaitable so the runner
can drive labelers + judges concurrently. Use `wrap_sync_labeler` to
adapt a sync `Labeler` (e.g. `AnthropicLabeler`)."""


def wrap_sync_labeler(labeler: Labeler) -> LabelFn:
    """Adapt a sync `Labeler` (e.g. `AnthropicLabeler`) to `LabelFn`.

    Calls the sync `.label(...)` method via `asyncio.to_thread` so it
    cooperates with the runner's `asyncio.gather` + Semaphore. The
    sync labeler's blocking I/O lands on the default thread-pool
    executor; concurrency is bounded by the runner, not the pool.
    """

    async def _wrapped(sample_texts: list[str], cluster_index: int) -> str:
        return await asyncio.to_thread(labeler.label, sample_texts, cluster_index)

    return _wrapped


@dataclass(frozen=True)
class ClusterLabelEvalRecord:
    """One eval call: which case, what the labeler said, what the judge said.

    `correct` is the binary judge-vs-human signal — 1 when the judge
    says the candidate is `agree` with the ground-truth label, 0
    otherwise. Aggregate agreement = mean(correct) across records.
    """

    cluster_id: str
    dominant_hint: str
    candidate_label: str
    judgment: ClusterLabelJudgment
    correct: bool


@dataclass(frozen=True)
class ClusterLabelEvalReport:
    """Aggregated cluster-label eval results.

    Surfaced by the runner + the CLI. JSON-serializable via `to_dict`.
    """

    records: tuple[ClusterLabelEvalRecord, ...]
    n_total: int
    n_correct: int
    agreement: float
    """`n_correct / n_total`. The single-number summary B3.5's ≥0.7
    gate measures."""

    per_hint_correct: dict[str, tuple[int, int]]
    """`dominant_hint → (correct, total)`. Catches "labeler is great
    on under-forecast clusters but blows zero-inflated ones" — a
    regression the aggregate would average out."""

    verdict_distribution: Counter
    """Histogram of judge verdicts across all records (`agree` / `disagree`).
    Useful for spotting calibration drift ("judge is calling everything
    agree, agreement is meaningless")."""

    judge_model: str
    labeler_label_for_log: str
    """Display string for the labeler that produced these candidates.
    The labeler itself isn't always introspectable (it could be a fake
    in tests, a wrapped sync object in prod) — record a freeform label
    so a CI run's audit trail can distinguish runs."""

    def to_dict(self) -> dict:
        """JSON-serializable summary. Records are included as a list; the
        CLI may pop them when only the aggregate is needed."""
        return {
            "judge_model": self.judge_model,
            "labeler_label_for_log": self.labeler_label_for_log,
            "n_total": self.n_total,
            "n_correct": self.n_correct,
            "agreement": self.agreement,
            "per_hint_correct": {k: list(v) for k, v in self.per_hint_correct.items()},
            "verdict_distribution": dict(self.verdict_distribution),
            "records": [
                {
                    "cluster_id": r.cluster_id,
                    "dominant_hint": r.dominant_hint,
                    "candidate_label": r.candidate_label,
                    "correct": r.correct,
                    "judgment": r.judgment.model_dump(mode="json"),
                }
                for r in self.records
            ],
        }


def _aggregate(
    records: list[ClusterLabelEvalRecord],
    judge_model: str,
    labeler_label_for_log: str,
) -> ClusterLabelEvalReport:
    n_total = len(records)
    n_correct = sum(1 for r in records if r.correct)
    agreement = n_correct / n_total if n_total else 0.0

    per_hint: dict[str, list[int]] = {}
    verdicts: Counter = Counter()
    for r in records:
        bucket = per_hint.setdefault(r.dominant_hint, [0, 0])
        bucket[1] += 1
        if r.correct:
            bucket[0] += 1
        verdicts[r.judgment.verdict] += 1

    return ClusterLabelEvalReport(
        records=tuple(records),
        n_total=n_total,
        n_correct=n_correct,
        agreement=agreement,
        per_hint_correct={k: (v[0], v[1]) for k, v in per_hint.items()},
        verdict_distribution=verdicts,
        judge_model=judge_model,
        labeler_label_for_log=labeler_label_for_log,
    )


async def _eval_one_case(
    client: AsyncAnthropic,
    case: LabeledClusterCase,
    case_index: int,
    label_fn: LabelFn,
    judge_model: str,
    judge_max_tokens: int,
    max_retries: int,
) -> ClusterLabelEvalRecord:
    """Drive labeler + judge for one fixture.

    `max_retries` retries on `ClusterLabelJudgmentValidationError` only —
    same rationale as A4.6 (transient malformed-JSON returns from the
    model). Other errors (NoClusterLabelToolUseError,
    ClusterLabelIdMismatchError, anthropic API errors, labeler errors)
    are not retried — they signal real misconfiguration.
    """
    candidate = await label_fn(list(case.member_signatures), case_index)

    last_exc: ClusterLabelJudgmentValidationError | None = None
    judgment: ClusterLabelJudgment | None = None
    for attempt in range(max_retries + 1):
        try:
            judgment = await judge_label_match(
                client,
                case,
                candidate,
                model=judge_model,
                max_tokens=judge_max_tokens,
            )
            break
        except ClusterLabelJudgmentValidationError as exc:
            last_exc = exc
            if attempt < max_retries:
                _log.warning(
                    "judge validation failed for cluster_id=%s attempt=%d; "
                    "retrying (errors=%d)",
                    case.cluster_id,
                    attempt,
                    exc.pydantic_error.error_count(),
                )
                continue
            raise
    else:  # pragma: no cover — unreachable; loop always breaks or raises
        assert last_exc is not None
        raise last_exc

    assert judgment is not None  # reachable iff loop break path ran
    return ClusterLabelEvalRecord(
        cluster_id=case.cluster_id,
        dominant_hint=case.dominant_hint,
        candidate_label=candidate,
        judgment=judgment,
        correct=(judgment.verdict == "agree"),
    )


async def run_cluster_label_eval(
    client: AsyncAnthropic,
    label_fn: LabelFn,
    *,
    eval_set: tuple[LabeledClusterCase, ...] = LABELED_CLUSTER_CASES,
    judge_model: str = DEFAULT_MODEL,
    judge_max_tokens: int = DEFAULT_MAX_TOKENS,
    concurrency: int = 1,
    max_retries_per_call: int = 0,
    labeler_label_for_log: str = "anthropic-haiku-4-5",
) -> ClusterLabelEvalReport:
    """Run the labeler + judge across every case and aggregate.

    Args:
        client: AsyncAnthropic client for the judge.
        label_fn: Async callable producing a candidate label for a
            cluster's member signatures. Use `wrap_sync_labeler` to
            adapt a sync `Labeler` (e.g. `AnthropicLabeler`).
        eval_set: Defaults to `LABELED_CLUSTER_CASES` (the 20-case fixture
            set). Override for unit tests or custom evals.
        judge_model: Anthropic model id; default sonnet 4.6.
        judge_max_tokens: Per-call output cap.
        concurrency: Number of (label + judge) pairs to run in parallel.
            Default 1 (sequential). Bump to 4-8 for faster CI runs.
        max_retries_per_call: Retries on `ClusterLabelJudgmentValidationError`
            only. Default 0; bump to 1 for live runs against weaker
            judges (sonnet's failure rate on this schema is empirically
            very low; the retry is cheap insurance).
        labeler_label_for_log: Freeform string identifying the labeler.
            Recorded in the report for the CI audit trail.

    Returns:
        `ClusterLabelEvalReport` with per-case records + aggregates.

    Raises:
        Re-raises any exception from `judge_label_match` or `label_fn`.
        Partial reports are not produced: a single failure means the
        agreement number would be misleading.
    """
    if concurrency < 1:
        raise ValueError(f"concurrency must be ≥1; got {concurrency}")

    sem = asyncio.Semaphore(concurrency)

    async def _bounded(case: LabeledClusterCase, idx: int) -> ClusterLabelEvalRecord:
        async with sem:
            return await _eval_one_case(
                client,
                case,
                idx,
                label_fn,
                judge_model,
                judge_max_tokens,
                max_retries_per_call,
            )

    tasks = [_bounded(case, idx) for idx, case in enumerate(eval_set)]
    records = await asyncio.gather(*tasks)
    # Stable order: same as the eval-set iteration order.
    case_order = {c.cluster_id: i for i, c in enumerate(eval_set)}
    records_sorted = sorted(records, key=lambda r: case_order[r.cluster_id])
    return _aggregate(records_sorted, judge_model, labeler_label_for_log)


__all__ = [
    "LabelFn",
    "wrap_sync_labeler",
    "ClusterLabelEvalRecord",
    "ClusterLabelEvalReport",
    "run_cluster_label_eval",
]
