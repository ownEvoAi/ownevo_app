"""Tests for `eval_runner.token_budget` (A4.5).

Two layers:

  * Primitive: `TokenBudget.record` accumulates input+output tokens,
    raises `TokenBudgetExceededError` post-call when cumulative > cap,
    rejects invalid construction.
  * Integration: the budget threads through `predict_one`,
    `solve_with_agent`, `run_with_agent`. The scripted client carries
    a fake `usage` block per response; the budget aborts the run at
    the case that tipped the cap; the typed exception carries the
    accumulator state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from ownevo_kernel.eval_runner import (
    TokenBudget,
    TokenBudgetExceededError,
    run_with_agent,
)
from ownevo_kernel.eval_runner.agent_solver import (
    TOOL_NAME,
    predict_one,
    solve_with_agent,
)
from ownevo_kernel.eval_runner.token_budget import extract_usage
from ownevo_kernel.nl_gen.eval_replay import exec_sim_module
from ownevo_kernel.nl_gen.fixtures import (
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)


# ---------------------------------------------------------------------------
# Scripted client with usage
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedResponse:
    content: list[Any]
    stop_reason: str = "tool_use"
    input_tokens: int = 0
    output_tokens: int = 0


class _ScriptedMessages:
    def __init__(self, responses: list[_ScriptedResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("client received more calls than scripted")
        response = self._responses.pop(0)
        return SimpleNamespace(
            content=response.content,
            stop_reason=response.stop_reason,
            usage=SimpleNamespace(
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            ),
        )


@dataclass
class _ScriptedClient:
    responses: list[_ScriptedResponse]
    messages: _ScriptedMessages = field(init=False)

    def __post_init__(self) -> None:
        self.messages = _ScriptedMessages(self.responses)


def _tool_use(name: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=payload, id="tu_1")


def _predict_response(
    value: bool,
    *,
    rationale: str = "scripted",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> _ScriptedResponse:
    return _ScriptedResponse(
        content=[_tool_use(TOOL_NAME, {"value": value, "rationale": rationale})],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------


def test_token_budget_construction_rejects_non_positive():
    with pytest.raises(ValueError, match=r"max_tokens must be > 0"):
        TokenBudget(max_tokens=0)
    with pytest.raises(ValueError, match=r"max_tokens must be > 0"):
        TokenBudget(max_tokens=-1)


def test_token_budget_record_accumulates():
    b = TokenBudget(max_tokens=1_000)
    b.record(input_tokens=100, output_tokens=50, label="a")
    assert b.used_input == 100
    assert b.used_output == 50
    assert b.used_total == 150
    assert b.n_calls == 1
    assert b.last_label == "a"
    assert b.remaining == 850

    b.record(input_tokens=200, output_tokens=25, label="b")
    assert b.used_input == 300
    assert b.used_output == 75
    assert b.used_total == 375
    assert b.n_calls == 2
    assert b.last_label == "b"
    assert b.remaining == 625


def test_token_budget_record_rejects_negative_usage():
    b = TokenBudget(max_tokens=1_000)
    with pytest.raises(ValueError, match=r"negative usage"):
        b.record(input_tokens=-1, output_tokens=10, label="bad")
    with pytest.raises(ValueError, match=r"negative usage"):
        b.record(input_tokens=10, output_tokens=-1, label="bad")


def test_token_budget_raises_when_exceeded():
    b = TokenBudget(max_tokens=200)
    b.record(input_tokens=100, output_tokens=50, label="under")
    # 150 used; 200 cap; this 75 push goes to 225 > 200.
    with pytest.raises(TokenBudgetExceededError) as excinfo:
        b.record(input_tokens=50, output_tokens=25, label="tipping")

    err = excinfo.value
    assert err.max_tokens == 200
    assert err.used_input == 150
    assert err.used_output == 75
    assert err.used_total == 225
    assert err.n_calls == 2
    assert err.last_label == "tipping"
    # State is preserved on the budget too (record completed before raise).
    assert b.used_total == 225
    assert b.n_calls == 2


def test_token_budget_exact_cap_does_not_raise():
    """Cap is `>` not `>=` — landing exactly at the cap is success."""
    b = TokenBudget(max_tokens=100)
    b.record(input_tokens=70, output_tokens=30, label="exact")
    assert b.used_total == 100
    assert b.remaining == 0


def test_extract_usage_returns_input_and_output():
    msg = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=42, output_tokens=17)
    )
    assert extract_usage(msg) == (42, 17)


def test_extract_usage_missing_block_returns_zero():
    msg = SimpleNamespace(content=[], stop_reason="end_turn")
    assert extract_usage(msg) == (0, 0)


def test_extract_usage_missing_fields_treated_as_zero():
    msg = SimpleNamespace(usage=SimpleNamespace())
    assert extract_usage(msg) == (0, 0)


def test_extract_usage_none_fields_treated_as_zero():
    msg = SimpleNamespace(usage=SimpleNamespace(input_tokens=None, output_tokens=None))
    assert extract_usage(msg) == (0, 0)


def test_token_budget_record_zero_tokens_accepted():
    b = TokenBudget(max_tokens=100)
    b.record(input_tokens=0, output_tokens=0, label="noop")
    assert b.used_total == 0
    assert b.n_calls == 1
    assert b.last_label == "noop"


# ---------------------------------------------------------------------------
# Integration: predict_one
# ---------------------------------------------------------------------------


async def test_predict_one_records_usage_when_budget_provided():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case = EVAL_CASE_SET_FIXTURES[workflow_id].cases[0]
    metric = METRIC_FIXTURES[workflow_id]
    ns = exec_sim_module(plan, spec, caller="test")

    budget = TokenBudget(max_tokens=10_000)
    client = _ScriptedClient(
        [_predict_response(True, input_tokens=400, output_tokens=80)]
    )

    await predict_one(
        client, case, spec=spec, metric=metric, namespace=ns, budget=budget
    )

    assert budget.used_input == 400
    assert budget.used_output == 80
    assert budget.n_calls == 1
    assert budget.last_label == case.case_id


async def test_predict_one_without_budget_does_not_track_anything():
    """Default behavior unchanged — no budget means no usage extraction
    and no abort risk. Pins back-compat with A4.4 callers."""
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case = EVAL_CASE_SET_FIXTURES[workflow_id].cases[0]
    metric = METRIC_FIXTURES[workflow_id]
    ns = exec_sim_module(plan, spec, caller="test")

    client = _ScriptedClient(
        [_predict_response(True, input_tokens=400, output_tokens=80)]
    )
    # No budget kwarg — default is None.
    result = await predict_one(
        client, case, spec=spec, metric=metric, namespace=ns
    )
    assert result.value is True


async def test_predict_one_raises_when_call_tips_cap():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case = EVAL_CASE_SET_FIXTURES[workflow_id].cases[0]
    metric = METRIC_FIXTURES[workflow_id]
    ns = exec_sim_module(plan, spec, caller="test")

    budget = TokenBudget(max_tokens=200)
    client = _ScriptedClient(
        [_predict_response(True, input_tokens=300, output_tokens=50)]
    )
    with pytest.raises(TokenBudgetExceededError) as excinfo:
        await predict_one(
            client, case, spec=spec, metric=metric, namespace=ns, budget=budget
        )
    assert excinfo.value.last_label == case.case_id
    assert excinfo.value.used_total == 350


# ---------------------------------------------------------------------------
# Integration: solve_with_agent
# ---------------------------------------------------------------------------


async def test_solve_with_agent_aborts_at_first_tipping_case():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    # Cap of 500. Per-call usage is 200. After call 1: 200; call 2:
    # 400; call 3 pushes to 600 — that's the case that aborts.
    budget = TokenBudget(max_tokens=500)
    responses = [
        _predict_response(True, input_tokens=150, output_tokens=50)
        for _ in case_set.cases
    ]
    client = _ScriptedClient(responses)

    with pytest.raises(TokenBudgetExceededError) as excinfo:
        await solve_with_agent(
            client, case_set, plan, spec, metric, budget=budget
        )

    err = excinfo.value
    assert err.n_calls == 3
    assert err.last_label == case_set.cases[2].case_id
    # Aborted before remaining cases were called — fewer messages than
    # the case-set size.
    assert len(client.messages.calls) == 3


async def test_solve_with_agent_under_budget_completes_all_cases():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    # Generous cap; every call records 200 and stays well under.
    budget = TokenBudget(max_tokens=10_000)
    responses = [
        _predict_response(c.expected_value, input_tokens=150, output_tokens=50)
        for c in case_set.cases
    ]
    client = _ScriptedClient(responses)
    results = await solve_with_agent(
        client, case_set, plan, spec, metric, budget=budget
    )
    assert len(results) == len(case_set.cases)
    assert budget.n_calls == len(case_set.cases)
    assert budget.used_total == 200 * len(case_set.cases)


# ---------------------------------------------------------------------------
# Integration: run_with_agent (full report path)
# ---------------------------------------------------------------------------


async def test_run_with_agent_propagates_budget_to_solver():
    workflow_id = "credit-risk"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    # Tip after 2 calls.
    budget = TokenBudget(max_tokens=300)
    responses = [
        _predict_response(True, input_tokens=120, output_tokens=40)
        for _ in case_set.cases
    ]
    client = _ScriptedClient(responses)

    with pytest.raises(TokenBudgetExceededError):
        await run_with_agent(
            case_set, plan, spec, metric, client=client, budget=budget
        )
    # Budget tipped on call 2 (used=320 > 300).
    assert budget.n_calls == 2
