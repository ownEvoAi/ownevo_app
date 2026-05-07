"""Tests for the W5.5 meta-eval gate in `nl_gen.pipeline.generate_full_pipeline`.

The gate adds an optional 5th call after the four NL-gen generators.
These tests pin:

  * Default (`meta_eval_gate=False`) preserves the four-call A4.4 shape
    and `meta_eval_judgment` is None on the result.
  * Gate-enabled happy path: 5 calls, `MetaEvalJudgment` attached to
    the result, `overall_verdict == "good"`.
  * Gate-enabled fail path: `overall_verdict == "bad"` raises
    `MetaEvalGateFailedError` with the judgment attached.
  * Gate-enabled aggregate-score floor: rejects (partial, partial,
    partial) bundles even when the judge calls them `good`.
  * Override propagation: `meta_eval_model` + `meta_eval_max_tokens`
    only hit the 5th (judge) call, not the four generators.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from ownevo_kernel.nl_gen import (
    MetaEvalGateFailedError,
    NLGenPipelineResult,
    generate_full_pipeline,
)
from ownevo_kernel.nl_gen.eval_generator import TOOL_NAME as EVAL_TOOL_NAME
from ownevo_kernel.nl_gen.fixtures import (
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)
from ownevo_kernel.nl_gen.meta_eval.judge import TOOL_NAME as JUDGE_TOOL_NAME
from ownevo_kernel.nl_gen.meta_eval.judgment import MetaEvalJudgment
from ownevo_kernel.nl_gen.metric_generator import TOOL_NAME as METRIC_TOOL_NAME
from ownevo_kernel.nl_gen.sim_generator import TOOL_NAME as SIM_TOOL_NAME
from ownevo_kernel.nl_gen.workflow_spec_generator import TOOL_NAME as SPEC_TOOL_NAME

_GENERATOR_TOOLS_IN_ORDER = [
    SPEC_TOOL_NAME,
    SIM_TOOL_NAME,
    EVAL_TOOL_NAME,
    METRIC_TOOL_NAME,
]
_GENERATOR_WRAPPER_KEYS_IN_ORDER = [
    "spec",
    "plan",
    "eval_case_set",
    "metric_definition",
]


@dataclass
class _ScriptedMessages:
    """Scripts up to 5 calls — 4 generators + optional 5th meta-eval judge.

    Each scripted entry is a `(tool_name, wrapper_key, payload)` triple.
    The judge call's wrapper key is `judgment`; the four generators
    each have their own per-step wrapper.
    """

    scripted: list[tuple[str, str, dict]]
    calls: list[dict] = field(default_factory=list)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        idx = len(self.calls) - 1
        if idx >= len(self.scripted):
            raise AssertionError(
                f"pipeline made more calls ({len(self.calls)}) than scripted "
                f"({len(self.scripted)})"
            )
        tool_name, wrapper_key, payload = self.scripted[idx]
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name=tool_name,
                    input={wrapper_key: payload},
                    id=f"tu_{idx}",
                )
            ],
            stop_reason="tool_use",
        )


@dataclass
class _ScriptedClient:
    scripted: list[tuple[str, str, dict]]
    messages: _ScriptedMessages = field(init=False)

    def __post_init__(self) -> None:
        self.messages = _ScriptedMessages(self.scripted)


def _generator_payloads(workflow_id: str) -> list[dict]:
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]
    return [
        json.loads(spec.model_dump_json()),
        json.loads(plan.model_dump_json()),
        json.loads(case_set.model_dump_json()),
        json.loads(metric.model_dump_json()),
    ]


def _generator_script(workflow_id: str) -> list[tuple[str, str, dict]]:
    return [
        (tool, wrapper, payload)
        for tool, wrapper, payload in zip(
            _GENERATOR_TOOLS_IN_ORDER,
            _GENERATOR_WRAPPER_KEYS_IN_ORDER,
            _generator_payloads(workflow_id),
            strict=True,
        )
    ]


def _judgment_payload(
    spec_id: str,
    *,
    sim: str = "pass",
    eval_: str = "pass",
    metric: str = "pass",
    overall: str = "good",
) -> dict:
    return {
        "schema_version": "0.1",
        "workflow_spec_id": spec_id,
        "sim_coverage": {
            "verdict": sim,
            "rationale": f"sim verdict={sim}.",
        },
        "eval_case_coverage": {
            "verdict": eval_,
            "rationale": f"eval verdict={eval_}.",
        },
        "metric_alignment": {
            "verdict": metric,
            "rationale": f"metric verdict={metric}.",
        },
        "overall_verdict": overall,
        "overall_rationale": (
            f"Overall {overall}; sim={sim} eval={eval_} metric={metric}."
        ),
    }


# ---------------------------------------------------------------------------
# Default (gate disabled) — back-compat
# ---------------------------------------------------------------------------


async def test_gate_disabled_by_default_makes_four_calls():
    workflow_id = "demand-prediction"
    client = _ScriptedClient(_generator_script(workflow_id))

    result = await generate_full_pipeline(client, "any description")

    assert isinstance(result, NLGenPipelineResult)
    assert len(client.messages.calls) == 4
    assert result.meta_eval_judgment is None


async def test_gate_explicitly_disabled_makes_four_calls():
    workflow_id = "demand-prediction"
    client = _ScriptedClient(_generator_script(workflow_id))

    result = await generate_full_pipeline(
        client, "any description", meta_eval_gate=False
    )

    assert len(client.messages.calls) == 4
    assert result.meta_eval_judgment is None


# ---------------------------------------------------------------------------
# Gate enabled — happy path
# ---------------------------------------------------------------------------


async def test_gate_enabled_runs_judge_and_attaches_judgment():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    script = _generator_script(workflow_id) + [
        (JUDGE_TOOL_NAME, "judgment", _judgment_payload(spec.id)),
    ]
    client = _ScriptedClient(script)

    result = await generate_full_pipeline(
        client, "any description", meta_eval_gate=True
    )

    assert len(client.messages.calls) == 5
    assert result.meta_eval_judgment is not None
    assert isinstance(result.meta_eval_judgment, MetaEvalJudgment)
    assert result.meta_eval_judgment.overall_verdict == "good"
    assert result.meta_eval_judgment.workflow_spec_id == spec.id


async def test_gate_enabled_fifth_call_uses_judge_tool():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    script = _generator_script(workflow_id) + [
        (JUDGE_TOOL_NAME, "judgment", _judgment_payload(spec.id)),
    ]
    client = _ScriptedClient(script)

    await generate_full_pipeline(
        client, "any description", meta_eval_gate=True
    )

    judge_call = client.messages.calls[4]
    assert judge_call["tools"][0]["name"] == JUDGE_TOOL_NAME


# ---------------------------------------------------------------------------
# Gate enabled — fail paths
# ---------------------------------------------------------------------------


async def test_gate_raises_on_overall_bad():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    script = _generator_script(workflow_id) + [
        (
            JUDGE_TOOL_NAME,
            "judgment",
            _judgment_payload(
                spec.id, sim="fail", eval_="pass", metric="pass", overall="bad"
            ),
        ),
    ]
    client = _ScriptedClient(script)

    with pytest.raises(MetaEvalGateFailedError) as exc_info:
        await generate_full_pipeline(
            client, "any description", meta_eval_gate=True
        )

    err = exc_info.value
    assert err.judgment.overall_verdict == "bad"
    assert err.judgment.sim_coverage.verdict == "fail"
    # Five calls — the gate runs the judge before raising.
    assert len(client.messages.calls) == 5


async def test_gate_min_aggregate_score_rejects_below_floor():
    """Judge calls overall=good but every dimension is partial → 0.5 < 0.7."""
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    script = _generator_script(workflow_id) + [
        (
            JUDGE_TOOL_NAME,
            "judgment",
            _judgment_payload(
                spec.id,
                sim="partial",
                eval_="partial",
                metric="partial",
                overall="good",
            ),
        ),
    ]
    client = _ScriptedClient(script)

    with pytest.raises(MetaEvalGateFailedError) as exc_info:
        await generate_full_pipeline(
            client,
            "any description",
            meta_eval_gate=True,
            meta_eval_min_aggregate_score=0.7,
        )

    err = exc_info.value
    assert err.judgment.overall_verdict == "good"
    assert err.judgment.aggregate_score() == pytest.approx(0.5)
    assert err.min_aggregate_score == 0.7


async def test_gate_min_aggregate_score_accepts_at_or_above_floor():
    """All-pass = 1.0; passes a 0.7 floor."""
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    script = _generator_script(workflow_id) + [
        (JUDGE_TOOL_NAME, "judgment", _judgment_payload(spec.id)),
    ]
    client = _ScriptedClient(script)

    result = await generate_full_pipeline(
        client,
        "any description",
        meta_eval_gate=True,
        meta_eval_min_aggregate_score=0.7,
    )
    assert result.meta_eval_judgment is not None
    assert result.meta_eval_judgment.aggregate_score() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Override propagation
# ---------------------------------------------------------------------------


async def test_meta_eval_model_only_overrides_judge_call():
    """`model=` covers the four generators; `meta_eval_model=` covers only
    the 5th call. Verifies the cheap-NL-gen-+-frontier-judge config works."""
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    script = _generator_script(workflow_id) + [
        (JUDGE_TOOL_NAME, "judgment", _judgment_payload(spec.id)),
    ]
    client = _ScriptedClient(script)

    await generate_full_pipeline(
        client,
        "any description",
        model="claude-haiku-4-5-20251001",
        meta_eval_gate=True,
        meta_eval_model="claude-opus-4-7",
    )

    for call in client.messages.calls[:4]:
        assert call["model"] == "claude-haiku-4-5-20251001"
    assert client.messages.calls[4]["model"] == "claude-opus-4-7"


async def test_meta_eval_max_tokens_only_overrides_judge_call():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    script = _generator_script(workflow_id) + [
        (JUDGE_TOOL_NAME, "judgment", _judgment_payload(spec.id)),
    ]
    client = _ScriptedClient(script)

    await generate_full_pipeline(
        client,
        "any description",
        max_tokens=2048,
        meta_eval_gate=True,
        meta_eval_max_tokens=4096,
    )

    for call in client.messages.calls[:4]:
        assert call["max_tokens"] == 2048
    assert client.messages.calls[4]["max_tokens"] == 4096


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


async def test_result_is_frozen_dataclass_when_gate_enabled():
    """Pipeline result is frozen — caller can't mutate the judgment."""
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    script = _generator_script(workflow_id) + [
        (JUDGE_TOOL_NAME, "judgment", _judgment_payload(spec.id)),
    ]
    client = _ScriptedClient(script)

    result = await generate_full_pipeline(
        client, "any description", meta_eval_gate=True
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.meta_eval_judgment = None  # type: ignore[misc]
