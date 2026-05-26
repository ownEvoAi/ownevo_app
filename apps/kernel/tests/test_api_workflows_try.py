"""Integration tests for `POST /api/workflows/{id}/try`.

Mocks `anthropic.AsyncAnthropic.messages.create` with the same
scripted-tool-use pattern used by `test_eval_runner_agent_solver.py`.
Seeds spec / sim_plan / metric / one eval case from the existing
`credit-risk` fixture so the underlying `predict_one` call has real
artifacts to chew on.

Skipped without `OWNEVO_DATABASE_URL` (same convention as the rest of
the API integration tests).
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import pytest
from ownevo_kernel.api._internal_auth import (
    DEV_AUTH_ENV,
    INTERNAL_AUTH_KEY_ENV,
    mint_workspace_assertion,
)
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.eval_cases.registry import add_eval_case
from ownevo_kernel.nl_gen.fixtures import (
    CREDIT_RISK_EVAL_CASE_SET,
    CREDIT_RISK_METRIC,
    CREDIT_RISK_SIM_PLAN,
    CREDIT_RISK_SPEC,
)

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


# ---------------------------------------------------------------------
# Scripted-tool-use fake AsyncAnthropic (lifted from
# test_eval_runner_agent_solver.py — same `messages.create` shape).
# ---------------------------------------------------------------------


@dataclass
class _ScriptedResponse:
    content: list[Any]
    stop_reason: str = "tool_use"
    usage: SimpleNamespace = field(
        default_factory=lambda: SimpleNamespace(
            input_tokens=120, output_tokens=42
        )
    )


class _ScriptedMessages:
    def __init__(self, responses: list[_ScriptedResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("scripted client out of responses")
        r = self._responses.pop(0)
        return SimpleNamespace(
            content=r.content, stop_reason=r.stop_reason, usage=r.usage
        )


class _ScriptedClient:
    def __init__(self, responses: list[_ScriptedResponse]) -> None:
        self.messages = _ScriptedMessages(responses)


def _tool_use(value: bool, rationale: str = "scripted") -> SimpleNamespace:
    return SimpleNamespace(
        type="tool_use",
        name="predict_label",
        id="tu_1",
        input={"value": value, "rationale": rationale},
    )


# ---------------------------------------------------------------------
# DB seeders — populate one credit-risk workflow + one eval case.
# ---------------------------------------------------------------------


async def _seed_full_workflow(
    db: asyncpg.Connection, workflow_id: str = "wf-try"
) -> UUID:
    """Insert one fully-populated workflow + one eval case from the
    credit-risk fixture. Returns the eval case UUID for the request body.

    The fixture's spec.id is overridden to the row id so `WorkflowSpec`
    validation downstream stays consistent (the iteration runner builds
    its EvalCaseSet with `workflow_spec_id=spec.id` from the row).
    """
    spec_dict = CREDIT_RISK_SPEC.model_dump(mode="json")
    spec_dict["id"] = workflow_id
    sim_dict = CREDIT_RISK_SIM_PLAN.model_dump(mode="json")
    sim_dict["workflow_spec_id"] = workflow_id
    metric_dict = CREDIT_RISK_METRIC.model_dump(mode="json")
    metric_dict["workflow_spec_id"] = workflow_id

    await db.execute(
        """
        INSERT INTO workflows (id, description, mode, spec,
                               simulation_plan, metric_definition)
        VALUES ($1, $2, 'gated'::workflow_mode,
                $3::jsonb, $4::jsonb, $5::jsonb)
        """,
        workflow_id,
        "Credit risk recalibration",
        json.dumps(spec_dict),
        json.dumps(sim_dict),
        json.dumps(metric_dict),
    )

    # Persist one case in the same split-payload shape that
    # eval_persistence writes (input + expected_behavior jsonb columns).
    case_seed = CREDIT_RISK_EVAL_CASE_SET.cases[0]
    inserted = await add_eval_case(
        db,
        provenance="nl-gen",
        input={
            "sim_seed": case_seed.sim_seed,
            "n_steps": case_seed.n_steps,
            "target_step_index": case_seed.target_step_index,
        },
        expected_behavior={
            "case_id": case_seed.case_id,
            "target_label_field": case_seed.target_label_field,
            "expected_value": case_seed.expected_value,
            "rationale": case_seed.rationale,
            "provenance": {
                "kind": case_seed.provenance.kind,
                "source": case_seed.provenance.source,
            },
        },
        workflow_id=workflow_id,
        is_test_fold=case_seed.is_test_fold,
    )
    return inserted.id


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


async def test_try_one_case_happy_path(
    api_client: httpx.AsyncClient,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
):
    """Reviewer picks an eval case, agent returns the expected value via
    forced tool-use, response carries trace + output + cost_usd."""
    case_id = await _seed_full_workflow(db, workflow_id="wf-try-happy")

    # The seeded case's expected_value mirrors the fixture (True or
    # False — we don't care which; we just script the agent to match it
    # so passed=True is asserted below).
    seeded_expected = CREDIT_RISK_EVAL_CASE_SET.cases[0].expected_value
    scripted = _ScriptedClient(
        [
            _ScriptedResponse(
                content=[_tool_use(value=seeded_expected, rationale="ok")]
            )
        ]
    )

    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **_kw: scripted)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    res = await api_client.post(
        "/api/workflows/wf-try-happy/try",
        json={"eval_case_id": str(case_id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["passed"] is True
    assert body["actual_value"] == seeded_expected
    assert body["rationale"] == "ok"
    # Trace is a [tool_call_start, tool_call_result] pair.
    assert len(body["trace"]) == 2
    assert body["trace"][0]["type"] == "tool_call_start"
    assert body["trace"][1]["type"] == "tool_call_result"
    assert body["trace"][1]["status"] == "ok"
    # Cost compute used the scripted usage (120 in, 42 out) at the
    # default workflow model rate (claude-sonnet-4-6 @ 3.00 / 15.00 per MTok).
    # 120 × 3.00 + 42 × 15.00 = 360 + 630 = 990; / 1_000_000 = 0.00099.
    assert body["input_tokens"] == 120
    assert body["output_tokens"] == 42
    assert body["cost_usd"] == pytest.approx(0.00099, rel=1e-6)


async def test_try_one_case_failed_prediction(
    api_client: httpx.AsyncClient,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
):
    """Agent returns the wrong label → passed=False, trace still 200."""
    case_id = await _seed_full_workflow(db, workflow_id="wf-try-fail")
    flipped = not CREDIT_RISK_EVAL_CASE_SET.cases[0].expected_value
    scripted = _ScriptedClient(
        [_ScriptedResponse(content=[_tool_use(value=flipped, rationale="wrong")])]
    )

    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **_kw: scripted)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    res = await api_client.post(
        "/api/workflows/wf-try-fail/try",
        json={"eval_case_id": str(case_id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["passed"] is False
    assert body["actual_value"] == flipped


async def test_try_404_on_unknown_workflow(
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    res = await api_client.post(
        "/api/workflows/no-such-workflow/try",
        json={"eval_case_id": "00000000-0000-0000-0000-000000000000"},
    )
    # WorkflowNotReadyError with "not found" maps to 404 per the route.
    assert res.status_code == 404


async def test_try_404_on_unknown_eval_case(
    api_client: httpx.AsyncClient,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
):
    await _seed_full_workflow(db, workflow_id="wf-try-no-case")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    res = await api_client.post(
        "/api/workflows/wf-try-no-case/try",
        json={"eval_case_id": "11111111-1111-1111-1111-111111111111"},
    )
    assert res.status_code == 404
    assert "eval case" in res.json()["detail"]


async def test_try_400_on_missing_inputs(
    api_client: httpx.AsyncClient,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
):
    """Neither eval_case_id nor free_form_input → 400."""
    await _seed_full_workflow(db, workflow_id="wf-try-empty")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    res = await api_client.post(
        "/api/workflows/wf-try-empty/try",
        json={},
    )
    assert res.status_code == 400


async def test_try_400_on_free_form_input(
    api_client: httpx.AsyncClient,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
):
    """free_form_input is not yet wired — surface a 400 with the reason."""
    await _seed_full_workflow(db, workflow_id="wf-try-freeform")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    res = await api_client.post(
        "/api/workflows/wf-try-freeform/try",
        json={"free_form_input": "what about a brand-new account?"},
    )
    assert res.status_code == 400
    assert "not yet supported" in res.json()["detail"]


async def test_try_503_without_anthropic_key(
    api_client: httpx.AsyncClient,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
):
    case_id = await _seed_full_workflow(db, workflow_id="wf-try-no-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = await api_client.post(
        "/api/workflows/wf-try-no-key/try",
        json={"eval_case_id": str(case_id)},
    )
    assert res.status_code == 503


async def test_try_409_on_concurrent_in_flight(
    api_client: httpx.AsyncClient,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
):
    """Two simultaneous tries on the same workflow → the second 409s.

    Holds the in-process lock manually via the route module so the
    second request hits the locked() branch deterministically; no
    flakey timing involved.
    """
    case_id = await _seed_full_workflow(db, workflow_id="wf-try-lock")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    from ownevo_kernel.api.routes import workflows as workflows_route

    lock = workflows_route._try_lock_for("wf-try-lock")
    await lock.acquire()
    try:
        res = await api_client.post(
            "/api/workflows/wf-try-lock/try",
            json={"eval_case_id": str(case_id)},
        )
    finally:
        lock.release()
    assert res.status_code == 409
    assert "in-flight" in res.json()["detail"]


async def test_try_renders_agent_failure_inline(
    api_client: httpx.AsyncClient,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
):
    """Agent emits text instead of tool_use → predict_one raises
    NoPredictToolUseError → runner catches it and returns a degraded
    TryItResult with passed=False + the failure recorded in trace.

    Design intent: Try-it surfaces failures inline (trace.status="error"
    + error_class) rather than HTTP-erroring out. Better UX for a "dry
    run" surface — reviewer sees what went wrong without a separate
    error branch in the UI.
    """
    case_id = await _seed_full_workflow(db, workflow_id="wf-try-soft-fail")
    scripted = _ScriptedClient(
        [
            _ScriptedResponse(
                content=[
                    SimpleNamespace(type="text", text="forgot to call the tool")
                ],
                stop_reason="end_turn",
            )
        ]
    )

    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **_kw: scripted)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    res = await api_client.post(
        "/api/workflows/wf-try-soft-fail/try",
        json={"eval_case_id": str(case_id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["passed"] is False
    assert body["actual_value"] is None
    # The failure shows up in the result-event of the trace.
    result_evt = body["trace"][-1]
    assert result_evt["status"] == "error"
    assert result_evt["error_class"]  # name of the exception class
    assert result_evt["error"]  # the message from the solver


async def test_try_403_for_non_member(
    api_client: httpx.AsyncClient,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
):
    """A signed assertion for a user with no workspace membership → 403.

    Exercises the WorkspaceMembershipError path in try_workflow_one_case
    that was added when the route switched from WorkspaceIdDep (no gate)
    to PrincipalDep + acquire_workspace_conn(user_id=...).
    """
    _KEY = "test-signing-key-try-403"
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    monkeypatch.delenv(DEV_AUTH_ENV, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    case_id = await _seed_full_workflow(db, workflow_id="wf-try-403")

    # Mint an assertion for a user who has no workspace_members row.
    token = mint_workspace_assertion(
        user_id="usr_nonmember",
        workspace_id="default",
        ttl_seconds=300,
        signing_key=_KEY,
    )
    res = await api_client.post(
        "/api/workflows/wf-try-403/try",
        headers={"Authorization": f"Bearer {token}"},
        json={"eval_case_id": str(case_id)},
    )
    assert res.status_code == 403, res.text
