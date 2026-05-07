"""Tests for `clustering.label_eval.runner.run_cluster_label_eval` (B3.5).

The runner orchestrates labeler + judge across the fixture set. Tests
mock both stages so the runner's coordination logic is exercised in
isolation:

  - Aggregate agreement = mean(verdict == "agree") over records.
  - Per-hint slicing buckets correctly.
  - Concurrency bound respected.
  - Retry-on-validation-error works; non-validation errors propagate.
  - Ordering is deterministic (matches eval-set iteration).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from ownevo_kernel.clustering.label_eval.fixtures import (
    LABELED_CLUSTER_CASES,
)
from ownevo_kernel.clustering.label_eval.judge import (
    TOOL_NAME,
    ClusterLabelJudgmentValidationError,
)
from ownevo_kernel.clustering.label_eval.judgment import ClusterLabelJudgment
from ownevo_kernel.clustering.label_eval.runner import (
    run_cluster_label_eval,
    wrap_sync_labeler,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedJudgeResponse:
    """One scripted judge response — keyed in the fake client by cluster_id."""

    cluster_id: str
    verdict: str  # "agree" | "disagree"
    rationale: str = "scripted"


def _judgment_payload(scripted: _ScriptedJudgeResponse) -> dict:
    return {
        "schema_version": "0.1",
        "cluster_id": scripted.cluster_id,
        "verdict": scripted.verdict,
        "rationale": scripted.rationale,
    }


def _tool_use_block(payload: Any) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=TOOL_NAME, input=payload, id="tu_x")


class _ScriptedJudgeClient:
    """AsyncAnthropic shim that hands back a verdict per cluster_id.

    Reads the cluster_id out of the user message (the runner sends it
    via `_format_user_message`) and returns the scripted verdict.
    """

    def __init__(
        self,
        scripted: dict[str, _ScriptedJudgeResponse],
        *,
        validation_error_first_n_calls: int = 0,
    ) -> None:
        self._scripted = scripted
        self.calls: list[str] = []
        self._calls_total = 0
        self._fail_first_n = validation_error_first_n_calls
        self.messages = self  # so client.messages.create works

    async def create(self, **kwargs):  # pragma: no cover via behavior tests
        self._calls_total += 1
        # Pull cluster_id out of the user message
        user_msg = kwargs["messages"][0]["content"]
        cluster_id = None
        for line in user_msg.splitlines():
            if line.startswith("cluster_id:"):
                cluster_id = line.split(":", 1)[1].strip()
                break
        assert cluster_id is not None, "cluster_id missing from user message"
        self.calls.append(cluster_id)
        scripted = self._scripted[cluster_id]
        # Optionally return a malformed payload for the first N calls to
        # exercise the validation-retry path. Use the wrapped payload
        # form so the judge is exercised through its real unwrap path.
        if self._calls_total <= self._fail_first_n:
            bad = _judgment_payload(scripted)
            bad.pop("verdict")  # force ValidationError
            return SimpleNamespace(
                content=[_tool_use_block(bad)],
                stop_reason="tool_use",
            )
        return SimpleNamespace(
            content=[_tool_use_block(_judgment_payload(scripted))],
            stop_reason="tool_use",
        )


def _make_label_fn(label_for_cluster: dict[str, str]):
    """Return an async label_fn that picks the candidate by cluster_id.

    The runner passes `member_signatures` + `cluster_index` to label_fn;
    we recover the cluster_id from the index using the eval-set order.
    """

    async def _fn(sample_texts: list[str], cluster_index: int) -> str:
        # eval_set defaults to LABELED_CLUSTER_CASES; tests that override
        # eval_set pass a custom dict where keys match those cases'
        # cluster_ids in the order the runner enumerates them.
        keys = list(label_for_cluster.keys())
        cluster_id = keys[cluster_index]
        return label_for_cluster[cluster_id]

    return _fn


# Build a tiny eval-set we can hold in our heads — first 3 fixtures.
_TEST_SET = LABELED_CLUSTER_CASES[:3]


# ---------------------------------------------------------------------------
# Aggregate math
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregate_perfect_agreement():
    scripted = {
        c.cluster_id: _ScriptedJudgeResponse(c.cluster_id, "agree")
        for c in _TEST_SET
    }
    candidate_for = {c.cluster_id: c.ground_truth_label for c in _TEST_SET}

    report = await run_cluster_label_eval(
        _ScriptedJudgeClient(scripted),
        _make_label_fn(candidate_for),
        eval_set=_TEST_SET,
    )
    assert report.n_total == 3
    assert report.n_correct == 3
    assert report.agreement == 1.0
    assert report.verdict_distribution == {"agree": 3}


@pytest.mark.asyncio
async def test_aggregate_zero_agreement():
    scripted = {
        c.cluster_id: _ScriptedJudgeResponse(c.cluster_id, "disagree")
        for c in _TEST_SET
    }
    candidate_for = {c.cluster_id: "a wrong label" for c in _TEST_SET}

    report = await run_cluster_label_eval(
        _ScriptedJudgeClient(scripted),
        _make_label_fn(candidate_for),
        eval_set=_TEST_SET,
    )
    assert report.agreement == 0.0
    assert report.n_correct == 0


@pytest.mark.asyncio
async def test_aggregate_mixed_agreement():
    scripted = {
        _TEST_SET[0].cluster_id: _ScriptedJudgeResponse(_TEST_SET[0].cluster_id, "agree"),
        _TEST_SET[1].cluster_id: _ScriptedJudgeResponse(_TEST_SET[1].cluster_id, "disagree"),
        _TEST_SET[2].cluster_id: _ScriptedJudgeResponse(_TEST_SET[2].cluster_id, "agree"),
    }
    candidate_for = {c.cluster_id: c.ground_truth_label for c in _TEST_SET}

    report = await run_cluster_label_eval(
        _ScriptedJudgeClient(scripted),
        _make_label_fn(candidate_for),
        eval_set=_TEST_SET,
    )
    assert report.n_correct == 2
    assert report.agreement == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# Per-hint slicing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_hint_slicing_aggregates_per_dominant_hint():
    """Use 4 cases spanning 2 hints. Per-hint slice should count correctly."""
    sub = (
        LABELED_CLUSTER_CASES[0],   # under-forecast — agree
        LABELED_CLUSTER_CASES[4],   # under-forecast — disagree
        LABELED_CLUSTER_CASES[1],   # over-forecast — agree
        LABELED_CLUSTER_CASES[5],   # over-forecast — agree
    )
    verdicts = ["agree", "disagree", "agree", "agree"]
    scripted = {
        c.cluster_id: _ScriptedJudgeResponse(c.cluster_id, v)
        for c, v in zip(sub, verdicts, strict=True)
    }
    candidate_for = {c.cluster_id: c.ground_truth_label for c in sub}

    report = await run_cluster_label_eval(
        _ScriptedJudgeClient(scripted),
        _make_label_fn(candidate_for),
        eval_set=sub,
    )
    assert report.per_hint_correct["under-forecast"] == (1, 2)
    assert report.per_hint_correct["over-forecast"] == (2, 2)


# ---------------------------------------------------------------------------
# Order + record shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_records_in_eval_set_order():
    sub = LABELED_CLUSTER_CASES[:5]
    scripted = {
        c.cluster_id: _ScriptedJudgeResponse(c.cluster_id, "agree") for c in sub
    }
    candidate_for = {c.cluster_id: c.ground_truth_label for c in sub}

    report = await run_cluster_label_eval(
        _ScriptedJudgeClient(scripted),
        _make_label_fn(candidate_for),
        eval_set=sub,
        concurrency=4,  # would otherwise scramble order
    )
    assert [r.cluster_id for r in report.records] == [c.cluster_id for c in sub]


@pytest.mark.asyncio
async def test_record_carries_candidate_and_judgment():
    case = LABELED_CLUSTER_CASES[0]
    scripted = {case.cluster_id: _ScriptedJudgeResponse(case.cluster_id, "agree")}
    candidate_for = {case.cluster_id: "a candidate label"}

    report = await run_cluster_label_eval(
        _ScriptedJudgeClient(scripted),
        _make_label_fn(candidate_for),
        eval_set=(case,),
    )
    rec = report.records[0]
    assert rec.candidate_label == "a candidate label"
    assert isinstance(rec.judgment, ClusterLabelJudgment)
    assert rec.dominant_hint == case.dominant_hint
    assert rec.correct is True


# ---------------------------------------------------------------------------
# Retry behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_recovers_validation_error():
    """First call returns malformed payload; runner retries and succeeds."""
    case = LABELED_CLUSTER_CASES[0]
    scripted = {case.cluster_id: _ScriptedJudgeResponse(case.cluster_id, "agree")}
    candidate_for = {case.cluster_id: "x"}
    client = _ScriptedJudgeClient(scripted, validation_error_first_n_calls=1)

    report = await run_cluster_label_eval(
        client,
        _make_label_fn(candidate_for),
        eval_set=(case,),
        max_retries_per_call=1,
    )
    assert report.agreement == 1.0
    assert client._calls_total == 2  # one fail + one retry


@pytest.mark.asyncio
async def test_retry_zero_propagates_validation_error():
    case = LABELED_CLUSTER_CASES[0]
    scripted = {case.cluster_id: _ScriptedJudgeResponse(case.cluster_id, "agree")}
    candidate_for = {case.cluster_id: "x"}
    client = _ScriptedJudgeClient(scripted, validation_error_first_n_calls=1)

    with pytest.raises(ClusterLabelJudgmentValidationError):
        await run_cluster_label_eval(
            client,
            _make_label_fn(candidate_for),
            eval_set=(case,),
            max_retries_per_call=0,
        )


# ---------------------------------------------------------------------------
# Concurrency guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrency_lt_one_raises():
    case = LABELED_CLUSTER_CASES[0]
    scripted = {case.cluster_id: _ScriptedJudgeResponse(case.cluster_id, "agree")}
    candidate_for = {case.cluster_id: "x"}

    with pytest.raises(ValueError, match="concurrency"):
        await run_cluster_label_eval(
            _ScriptedJudgeClient(scripted),
            _make_label_fn(candidate_for),
            eval_set=(case,),
            concurrency=0,
        )


@pytest.mark.asyncio
async def test_to_dict_is_serializable():
    case = LABELED_CLUSTER_CASES[0]
    scripted = {case.cluster_id: _ScriptedJudgeResponse(case.cluster_id, "agree")}
    candidate_for = {case.cluster_id: "x"}

    report = await run_cluster_label_eval(
        _ScriptedJudgeClient(scripted),
        _make_label_fn(candidate_for),
        eval_set=(case,),
    )
    import json
    payload = report.to_dict()
    json.dumps(payload)  # must not raise
    assert payload["agreement"] == 1.0
    assert payload["judge_model"]
    assert payload["records"][0]["cluster_id"] == case.cluster_id


# ---------------------------------------------------------------------------
# wrap_sync_labeler
# ---------------------------------------------------------------------------


def test_wrap_sync_labeler_returns_async_callable():
    class _SyncLabeler:
        def label(self, sample_texts: list[str], cluster_index: int) -> str:
            return f"sync-{cluster_index}-{len(sample_texts)}"

    fn = wrap_sync_labeler(_SyncLabeler())
    result = asyncio.run(fn(["a", "b"], 7))
    assert result == "sync-7-2"
