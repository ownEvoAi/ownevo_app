"""Tests for `nl_gen.meta_eval.runner.run_meta_eval` (A4.6).

The runner orchestrates many judge calls; we mock the judge directly
(rather than building a fake AsyncAnthropic for each pair) so the
tests focus on aggregation logic, ordering, and per-recipe slicing.
"""

from __future__ import annotations

import dataclasses

import pytest

from ownevo_kernel.nl_gen.meta_eval import runner as runner_module
from ownevo_kernel.nl_gen.meta_eval.eval_set import META_EVAL_SET, MetaEvalPair
from ownevo_kernel.nl_gen.meta_eval.judgment import MetaEvalJudgment
from ownevo_kernel.nl_gen.meta_eval.runner import (
    MetaEvalRecord,
    MetaEvalReport,
    run_meta_eval,
)


def _judgment(
    spec_id: str,
    *,
    overall: str = "good",
    sim: str = "pass",
    eval_cov: str = "pass",
    metric: str = "pass",
) -> MetaEvalJudgment:
    return MetaEvalJudgment.model_validate(
        {
            "schema_version": "0.1",
            "workflow_spec_id": spec_id,
            "sim_coverage": {"verdict": sim, "rationale": "x"},
            "eval_case_coverage": {"verdict": eval_cov, "rationale": "x"},
            "metric_alignment": {"verdict": metric, "rationale": "x"},
            "overall_verdict": overall,
            "overall_rationale": "test",
        }
    )


def _stub_judge_factory(verdict_for):
    """Return a coroutine matching judge_artifacts' signature.

    `verdict_for` is `(spec_id, role) -> overall_verdict` — but the
    coroutine doesn't get `role`, only the bundle. So we infer role by
    checking which side of which pair the bundle came from. Tests pass
    a closure that knows the eval set's structure.
    """

    async def fake_judge(
        client,
        description,
        spec,
        plan,
        case_set,
        metric,
        *,
        model=None,
        max_tokens=None,
    ):
        return verdict_for(spec.id, plan, metric, case_set)

    return fake_judge


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


async def test_runner_returns_records_for_every_pair_and_role(monkeypatch):
    """20 records: 10 pairs × 2 roles."""

    async def always_good(client, description, spec, plan, case_set, metric, **kw):
        return _judgment(spec.id, overall="good")

    monkeypatch.setattr(runner_module, "judge_artifacts", always_good)
    report = await run_meta_eval(client=None)
    assert isinstance(report, MetaEvalReport)
    assert report.n_total == 20
    assert len(report.records) == 20


async def test_runner_correct_count_for_oracle_judge(monkeypatch):
    """Oracle judge: returns the ground-truth verdict every time. Every
    record is correct → agreement = 1.0."""

    # spec.id (kebab-case from the WorkflowSpec) is what the judge sees;
    # pair_id is the eval-set's pair label, which equals spec.id only
    # for the minimal fixtures (pair_id == workflow_id == spec.id). For
    # production fixtures pair_id is the FIXTURES key (e.g. "demand-prediction")
    # while spec.id is the actual id (e.g. "supply-chain-demand-forecast").
    pair_by_spec_id = {p.good[0].id: p for p in META_EVAL_SET}

    async def oracle(client, description, spec, plan, case_set, metric, **kw):
        pair = pair_by_spec_id[spec.id]
        # Compare bundle components against the good bundle to infer role.
        expected_verdict = (
            "good"
            if metric == pair.good[3] and plan == pair.good[1] and case_set == pair.good[2]
            else "bad"
        )
        return _judgment(spec.id, overall=expected_verdict)

    monkeypatch.setattr(runner_module, "judge_artifacts", oracle)
    report = await run_meta_eval(client=None)
    assert report.agreement == 1.0
    assert report.n_correct == 20


async def test_runner_correct_count_for_always_good_judge(monkeypatch):
    """Always-good judge: gets every "good" right (10) and every "bad"
    wrong (10). Agreement = 0.5."""

    async def always_good(client, description, spec, plan, case_set, metric, **kw):
        return _judgment(spec.id, overall="good")

    monkeypatch.setattr(runner_module, "judge_artifacts", always_good)
    report = await run_meta_eval(client=None)
    assert report.n_correct == 10  # only the "good" half is right
    assert report.agreement == 0.5


async def test_runner_per_recipe_correctness(monkeypatch):
    """Judge that calls "bad" only on swap_sim_plan corruptions: should
    score 1/1 on swap_sim_plan, 0/total on every other recipe."""

    pair_by_spec_id = {p.good[0].id: p for p in META_EVAL_SET}

    async def selective(client, description, spec, plan, case_set, metric, **kw):
        pair = pair_by_spec_id[spec.id]
        if plan != pair.good[1]:
            # bad side, sim swapped — the only case we call bad
            return _judgment(spec.id, overall="bad")
        if metric != pair.good[3] or case_set != pair.good[2]:
            # bad side but a different recipe — call good (wrong on purpose)
            return _judgment(spec.id, overall="good")
        # good side: call good (correct)
        return _judgment(spec.id, overall="good")

    monkeypatch.setattr(runner_module, "judge_artifacts", selective)
    report = await run_meta_eval(client=None)
    swap_sim_count = sum(
        1 for p in META_EVAL_SET if p.bad_recipe_id == "swap_sim_plan"
    )
    correct, total = report.per_recipe_correct["swap_sim_plan"]
    assert total == swap_sim_count
    assert correct == swap_sim_count


async def test_runner_per_dimension_distribution_counts(monkeypatch):
    """Stamp every judgment with sim=fail, eval=partial, metric=pass.
    Aggregates should reflect 20 of each."""

    async def stamper(client, description, spec, plan, case_set, metric, **kw):
        return _judgment(
            spec.id,
            overall="good",
            sim="fail",
            eval_cov="partial",
            metric="pass",
        )

    monkeypatch.setattr(runner_module, "judge_artifacts", stamper)
    report = await run_meta_eval(client=None)
    assert dict(report.per_dimension_distribution["sim_coverage"]) == {"fail": 20}
    assert dict(report.per_dimension_distribution["eval_case_coverage"]) == {"partial": 20}
    assert dict(report.per_dimension_distribution["metric_alignment"]) == {"pass": 20}


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


async def test_to_dict_is_json_serializable(monkeypatch):
    import json

    async def stamper(client, description, spec, plan, case_set, metric, **kw):
        return _judgment(spec.id, overall="good")

    monkeypatch.setattr(runner_module, "judge_artifacts", stamper)
    report = await run_meta_eval(client=None)
    payload = report.to_dict()
    json.dumps(payload, sort_keys=True)  # must not raise
    assert payload["n_total"] == 20
    assert payload["agreement"] == 0.5
    assert "records" in payload
    assert len(payload["records"]) == 20


async def test_records_carry_role_and_expected_verdict(monkeypatch):
    async def stamper(client, description, spec, plan, case_set, metric, **kw):
        return _judgment(spec.id, overall="good")

    monkeypatch.setattr(runner_module, "judge_artifacts", stamper)
    report = await run_meta_eval(client=None)
    roles = [r.role for r in report.records]
    assert roles.count("good") == 10
    assert roles.count("bad") == 10
    for r in report.records:
        if r.role == "good":
            assert r.expected_verdict == "good"
        else:
            assert r.expected_verdict == "bad"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


async def test_records_are_in_eval_set_order_good_before_bad(monkeypatch):
    async def stamper(client, description, spec, plan, case_set, metric, **kw):
        return _judgment(spec.id, overall="good")

    monkeypatch.setattr(runner_module, "judge_artifacts", stamper)
    report = await run_meta_eval(client=None)
    # First 2 records should be the first pair, good then bad.
    first_pair_id = META_EVAL_SET[0].pair_id
    assert report.records[0].pair_id == first_pair_id
    assert report.records[0].role == "good"
    assert report.records[1].pair_id == first_pair_id
    assert report.records[1].role == "bad"


# ---------------------------------------------------------------------------
# Concurrency knob
# ---------------------------------------------------------------------------


async def test_concurrency_zero_rejected(monkeypatch):
    async def stamper(client, description, spec, plan, case_set, metric, **kw):
        return _judgment(spec.id, overall="good")

    monkeypatch.setattr(runner_module, "judge_artifacts", stamper)
    with pytest.raises(ValueError):
        await run_meta_eval(client=None, concurrency=0)


async def test_concurrency_higher_than_one_runs_to_completion(monkeypatch):
    """Smoke: concurrency>1 doesn't change correctness on the mock judge."""

    async def stamper(client, description, spec, plan, case_set, metric, **kw):
        return _judgment(spec.id, overall="good")

    monkeypatch.setattr(runner_module, "judge_artifacts", stamper)
    report = await run_meta_eval(client=None, concurrency=4)
    assert report.n_total == 20
    assert report.n_correct == 10  # always-good judge


# ---------------------------------------------------------------------------
# Custom eval_set
# ---------------------------------------------------------------------------


async def test_runner_with_subset_eval_set(monkeypatch):
    async def stamper(client, description, spec, plan, case_set, metric, **kw):
        return _judgment(spec.id, overall="good")

    monkeypatch.setattr(runner_module, "judge_artifacts", stamper)
    subset = META_EVAL_SET[:3]
    report = await run_meta_eval(client=None, eval_set=subset)
    assert report.n_total == 6  # 3 pairs × 2


# ---------------------------------------------------------------------------
# Records carry the model name + judgment metadata
# ---------------------------------------------------------------------------


async def test_report_records_model(monkeypatch):
    async def stamper(client, description, spec, plan, case_set, metric, **kw):
        return _judgment(spec.id, overall="good")

    monkeypatch.setattr(runner_module, "judge_artifacts", stamper)
    report = await run_meta_eval(client=None, model="claude-haiku-4-5")
    assert report.model == "claude-haiku-4-5"
    assert report.to_dict()["model"] == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Exception propagation
# ---------------------------------------------------------------------------


async def test_runner_propagates_judge_exceptions(monkeypatch):
    """A partial report would mislead the agreement number; the runner
    re-raises rather than swallowing per-pair failures."""

    async def boom(client, description, spec, plan, case_set, metric, **kw):
        raise RuntimeError("the judge crashed")

    monkeypatch.setattr(runner_module, "judge_artifacts", boom)
    with pytest.raises(RuntimeError, match="the judge crashed"):
        await run_meta_eval(client=None)


# ---------------------------------------------------------------------------
# Retry on MetaEvalJudgmentValidationError
# ---------------------------------------------------------------------------


async def test_runner_retries_validation_error(monkeypatch):
    """First call fails with MetaEvalJudgmentValidationError; retry
    succeeds. With max_retries_per_call=1, the run completes."""
    from pydantic import ValidationError

    from ownevo_kernel.nl_gen.meta_eval.judge import (
        MetaEvalJudgmentValidationError,
    )

    state = {"calls": 0}

    async def flaky(client, description, spec, plan, case_set, metric, **kw):
        state["calls"] += 1
        if state["calls"] == 1:
            # Fabricate a ValidationError instance to attach.
            try:
                from ownevo_kernel.nl_gen.meta_eval.judgment import MetaEvalJudgment
                MetaEvalJudgment.model_validate({})
            except ValidationError as e:
                raise MetaEvalJudgmentValidationError(
                    "synthetic transient",
                    raw_input={},
                    pydantic_error=e,
                )
        return _judgment(spec.id, overall="good")

    monkeypatch.setattr(runner_module, "judge_artifacts", flaky)
    report = await run_meta_eval(client=None, max_retries_per_call=1)
    assert report.n_total == 20
    # First call retried + 19 good calls = 21 calls total.
    assert state["calls"] == 21


async def test_runner_does_not_retry_other_exceptions(monkeypatch):
    """`max_retries_per_call` only retries MetaEvalJudgmentValidationError —
    other failures propagate immediately so real misconfiguration
    doesn't silently waste calls."""

    state = {"calls": 0}

    async def boom_runtime(client, description, spec, plan, case_set, metric, **kw):
        state["calls"] += 1
        raise RuntimeError("anthropic API down")

    monkeypatch.setattr(runner_module, "judge_artifacts", boom_runtime)
    with pytest.raises(RuntimeError, match="anthropic API down"):
        await run_meta_eval(client=None, max_retries_per_call=3)
    # Concurrency=1 default: first call fails, gather collects pending
    # futures and the loop ends. Calls is at most concurrency (1).
    assert state["calls"] >= 1


async def test_runner_max_retries_zero_is_strict(monkeypatch):
    """Default max_retries_per_call=0 — validation error fires
    immediately (preserves the pre-A4.6-smoke contract)."""
    from pydantic import ValidationError

    from ownevo_kernel.nl_gen.meta_eval.judge import (
        MetaEvalJudgmentValidationError,
    )

    async def always_fail(client, description, spec, plan, case_set, metric, **kw):
        try:
            from ownevo_kernel.nl_gen.meta_eval.judgment import MetaEvalJudgment
            MetaEvalJudgment.model_validate({})
        except ValidationError as e:
            raise MetaEvalJudgmentValidationError(
                "synthetic", raw_input={}, pydantic_error=e
            )

    monkeypatch.setattr(runner_module, "judge_artifacts", always_fail)
    with pytest.raises(MetaEvalJudgmentValidationError):
        await run_meta_eval(client=None)  # max_retries_per_call defaults to 0
