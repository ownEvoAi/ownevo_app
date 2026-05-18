"""Meta-eval runner — drives the judge across `META_EVAL_SET` (A4.6).

The runner takes an `AsyncAnthropic` client, calls `judge_artifacts`
on every (good, bad) pair in the eval set, collects per-evaluation
records, and aggregates judge-vs-human agreement.

For each pair, the runner produces two records (one for the good
bundle, one for the bad). Agreement = fraction of records where
`judgment.overall_verdict == expected_verdict`. A5.5's gate is
`agreement ≥ 0.7`.

Per-dimension verdict distributions and per-recipe correctness are
also aggregated so a future regression on one dimension or one
corruption mode is visible without re-reading the per-pair records.

Lives alongside `judge.py` in the `agent` extra path — the runner
imports `judge_artifacts` directly. The eval set itself
(`eval_set.META_EVAL_SET`) is kernel-runtime, no anthropic dep.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .corruptions import Bundle
from .eval_set import META_EVAL_SET, MetaEvalPair
from .judge import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    MetaEvalJudgmentValidationError,
    judge_artifacts,
)
from .judgment import MetaEvalJudgment, OverallVerdict

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


PairRole = Literal["good", "bad"]


@dataclass(frozen=True)
class MetaEvalRecord:
    """One judge call: which pair, which side, what the judge said.

    `correct` is the judge-vs-human binary on the overall_verdict; the
    aggregate agreement is the mean of `correct` across all records.
    """

    pair_id: str
    role: PairRole
    expected_verdict: OverallVerdict
    judgment: MetaEvalJudgment
    correct: bool


@dataclass(frozen=True)
class MetaEvalReport:
    """Aggregated meta-eval results over the eval set.

    Surfaced by the runner + the CLI. JSON-serializable via `to_dict`.
    """

    records: tuple[MetaEvalRecord, ...]
    n_total: int
    n_correct: int
    agreement: float
    """`n_correct / n_total`. The single-number summary A5.5 grades
    against (≥0.7 is the W5 gate)."""

    per_dimension_distribution: dict[str, Counter]
    """Per-dimension verdict histogram across all records. Keys are
    `sim_coverage` / `eval_case_coverage` / `metric_alignment`; values
    map verdict (`pass` / `partial` / `fail`) → count. Useful for
    spotting "judge is calling everything `pass`" calibration drift."""

    per_recipe_correct: dict[str, tuple[int, int]]
    """`recipe_id → (correct, total)` for the `bad` half. Surfaces
    which corruption modes the judge struggles with."""

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
            "per_dimension_distribution": {
                dim: dict(c) for dim, c in self.per_dimension_distribution.items()
            },
            "per_recipe_correct": {
                k: list(v) for k, v in self.per_recipe_correct.items()
            },
            "records": [
                {
                    "pair_id": r.pair_id,
                    "role": r.role,
                    "expected_verdict": r.expected_verdict,
                    "correct": r.correct,
                    "judgment": r.judgment.model_dump(mode="json"),
                }
                for r in self.records
            ],
        }


def _aggregate(
    records: list[MetaEvalRecord],
    eval_set: list[MetaEvalPair],
    model: str,
) -> MetaEvalReport:
    n_total = len(records)
    n_correct = sum(1 for r in records if r.correct)
    agreement = n_correct / n_total if n_total else 0.0

    per_dim: dict[str, Counter] = {
        "sim_coverage": Counter(),
        "eval_case_coverage": Counter(),
        "metric_alignment": Counter(),
    }
    for r in records:
        per_dim["sim_coverage"][r.judgment.sim_coverage.verdict] += 1
        per_dim["eval_case_coverage"][r.judgment.eval_case_coverage.verdict] += 1
        per_dim["metric_alignment"][r.judgment.metric_alignment.verdict] += 1

    pair_by_id = {p.pair_id: p for p in eval_set}
    per_recipe: dict[str, list[int]] = {}
    for r in records:
        if r.role != "bad":
            continue  # recipe is only meaningful for the corrupted side
        pair = pair_by_id[r.pair_id]
        bucket = per_recipe.setdefault(pair.bad_recipe_id, [0, 0])
        bucket[1] += 1  # total
        if r.correct:
            bucket[0] += 1
    per_recipe_correct = {k: (v[0], v[1]) for k, v in per_recipe.items()}

    return MetaEvalReport(
        records=tuple(records),
        n_total=n_total,
        n_correct=n_correct,
        agreement=agreement,
        per_dimension_distribution=per_dim,
        per_recipe_correct=per_recipe_correct,
        model=model,
    )


async def _judge_one_side(
    client: AsyncAnthropic,
    pair: MetaEvalPair,
    role: PairRole,
    bundle: Bundle,
    model: str,
    max_tokens: int,
    max_retries: int = 0,
) -> MetaEvalRecord:
    """Drive the judge for one (pair, role).

    `max_retries` retries on `MetaEvalJudgmentValidationError` only —
    the typed error fires when the model produces malformed JSON or
    drops a required field, which empirically is transient (~5-10%
    of opus 4.7 calls when the wrapped payload comes back as a
    JSON-encoded string with a parse error). Other errors
    (NoMetaEvalToolUseError, MetaEvalSpecIdMismatchError, anthropic
    API errors) are not retried — they signal real misconfiguration
    rather than model output drift.
    """
    spec, plan, case_set, metric = bundle
    last_exc: MetaEvalJudgmentValidationError | None = None
    for attempt in range(max_retries + 1):
        try:
            judgment = await judge_artifacts(
                client,
                pair.description,
                spec,
                plan,
                case_set,
                metric,
                model=model,
                max_tokens=max_tokens,
            )
            break
        except MetaEvalJudgmentValidationError as exc:
            last_exc = exc
            if attempt < max_retries:
                _log.warning(
                    "judge validation failed for pair=%s role=%s attempt=%d; "
                    "retrying (errors=%d)",
                    pair.pair_id,
                    role,
                    attempt,
                    exc.pydantic_error.error_count(),
                )
                continue
            raise
    else:  # pragma: no cover — unreachable; loop always breaks or raises
        assert last_exc is not None
        raise last_exc

    expected = pair.expected_good_verdict if role == "good" else pair.expected_bad_verdict
    return MetaEvalRecord(
        pair_id=pair.pair_id,
        role=role,
        expected_verdict=expected,
        judgment=judgment,
        correct=(judgment.overall_verdict == expected),
    )


async def run_meta_eval(
    client: AsyncAnthropic,
    *,
    eval_set: list[MetaEvalPair] = META_EVAL_SET,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    concurrency: int = 1,
    max_retries_per_call: int = 0,
) -> MetaEvalReport:
    """Run the judge across every (good, bad) pair and aggregate.

    Args:
        client: AsyncAnthropic client.
        eval_set: Defaults to `META_EVAL_SET` (the 10 pairs the A4.6
            deliverable ships). Override for unit tests or custom evals.
        model: Anthropic model id; default opus 4.7 (calibration anchor).
        max_tokens: Per-call output cap.
        concurrency: Number of judge calls to run in parallel. Default
            1 (sequential — easiest to reason about + cheapest path
            against rate limits). Bump to 4-8 for faster CI runs.
        max_retries_per_call: Retries on `MetaEvalJudgmentValidationError`
            only. Default 0 (current strict behavior). Bump to 1 for
            live runs against opus 4.7 — the model occasionally
            returns malformed JSON in the string-wrapped payload
            (~5-10% of calls); a single retry resolves it.

    Returns:
        `MetaEvalReport` with per-pair records + aggregates.

    Raises:
        Re-raises any exception from `judge_artifacts` (NoMetaEvalToolUseError,
        MetaEvalJudgmentValidationError, MetaEvalSpecIdMismatchError,
        anthropic API errors). The runner doesn't swallow per-pair failures
        because partial reports would mislead the agreement number.
    """
    if concurrency < 1:
        raise ValueError(f"concurrency must be ≥1; got {concurrency}")

    sem = asyncio.Semaphore(concurrency)

    async def _bounded(pair: MetaEvalPair, role: PairRole, bundle: Bundle):
        async with sem:
            return await _judge_one_side(
                client,
                pair,
                role,
                bundle,
                model,
                max_tokens,
                max_retries=max_retries_per_call,
            )

    tasks = []
    for pair in eval_set:
        tasks.append(_bounded(pair, "good", pair.good))
        tasks.append(_bounded(pair, "bad", pair.bad))

    records = await asyncio.gather(*tasks)
    # Stable order: same as the eval-set iteration order, good then bad.
    records_sorted = sorted(records, key=lambda r: (
        [p.pair_id for p in eval_set].index(r.pair_id),
        0 if r.role == "good" else 1,
    ))
    return _aggregate(records_sorted, eval_set, model)


__all__ = [
    "MetaEvalRecord",
    "MetaEvalReport",
    "PairRole",
    "run_meta_eval",
]
