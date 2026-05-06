"""Tests for `scripts/nl_gen_smoketest.py` (A4.4 CLI).

In-process invocation via `main(argv)`. The CLI's live API path is
exercised end-to-end only when the operator runs it manually with
ANTHROPIC_API_KEY set; here we monkeypatch `_make_client` and
`run_with_agent` so every test is hermetic.

Pins:
  * --workflow argparse + alias to all-mode.
  * --from-fixtures path doesn't require ANTHROPIC_API_KEY.
  * Live mode aborts cleanly (exit 2) when ANTHROPIC_API_KEY is missing
    and --from-fixtures was not passed.
  * Exit code is 0 iff every workflow meets target; 1 otherwise.
  * --include-outcomes toggles the outcomes array.
  * --max-cases truncates the case set and re-validates (errors loud
    when the cap drops below the balanced-classes minimum).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

import scripts.nl_gen_smoketest as smoketest  # noqa: E402
from ownevo_kernel.eval_runner import EvalRunReport  # noqa: E402


@pytest.fixture(autouse=True)
def _api_key_present(monkeypatch):
    """Most tests assume the operator has auth set up; the explicit
    `test_live_mode_without_api_key_aborts_with_two` test deletes the
    env var inside its body."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fixture")


def _stub_report(workflow_id: str, *, meets_target: bool) -> EvalRunReport:
    return EvalRunReport(
        workflow_spec_id=workflow_id,
        metric_name="stub-metric",
        metric_family="recall",
        direction="maximize",
        value=1.0 if meets_target else 0.1,
        target_value=0.5,
        meets_target=meets_target,
        degenerate=False,
        n_total=12,
        n_pass=12 if meets_target else 1,
        tp=6 if meets_target else 1,
        tn=6 if meets_target else 0,
        fp=0 if meets_target else 6,
        fn=0 if meets_target else 5,
        outcomes=tuple(),
    )


def _patch_run_to(monkeypatch, *, meets_target: bool):
    async def _stub_run_with_agent(case_set, plan, spec, metric, **kwargs):
        return _stub_report(spec.id, meets_target=meets_target)

    monkeypatch.setattr(smoketest, "run_with_agent", _stub_run_with_agent)


class _StubClient:
    pass


def _patch_client(monkeypatch):
    monkeypatch.setattr(smoketest, "_make_client", lambda base_url: _StubClient())


# ---------------------------------------------------------------------------
# --from-fixtures path
# ---------------------------------------------------------------------------


def test_from_fixtures_meets_target_exits_zero(monkeypatch, capsys):
    _patch_client(monkeypatch)
    _patch_run_to(monkeypatch, meets_target=True)

    rc = smoketest.main(["--workflow", "demand-prediction", "--from-fixtures"])
    out = capsys.readouterr().out.strip()

    assert rc == 0
    payload = json.loads(out)
    assert payload["meets_target"] is True
    assert payload["workflow_id"] == "demand-prediction"


def test_from_fixtures_miss_exits_one(monkeypatch, capsys):
    _patch_client(monkeypatch)
    _patch_run_to(monkeypatch, meets_target=False)

    rc = smoketest.main(["--workflow", "credit-risk", "--from-fixtures"])
    out = capsys.readouterr().out.strip()

    assert rc == 1
    payload = json.loads(out)
    assert payload["meets_target"] is False


def test_all_mode_exits_zero_only_when_every_workflow_meets_target(
    monkeypatch, capsys
):
    _patch_client(monkeypatch)
    _patch_run_to(monkeypatch, meets_target=True)

    rc = smoketest.main(["--workflow", "all", "--from-fixtures"])
    out = capsys.readouterr().out.strip()

    assert rc == 0
    lines = out.splitlines()
    assert len(lines) == len(smoketest.WORKFLOW_CHOICES)
    for line in lines:
        assert json.loads(line)["meets_target"] is True


def test_all_mode_one_miss_propagates_exit_one(monkeypatch, capsys):
    _patch_client(monkeypatch)

    # First call succeeds, second fails, third succeeds → exit 1.
    state = {"i": 0}
    pattern = [True, False, True]

    async def _stub_run_with_agent(case_set, plan, spec, metric, **kwargs):
        report = _stub_report(spec.id, meets_target=pattern[state["i"]])
        state["i"] += 1
        return report

    monkeypatch.setattr(smoketest, "run_with_agent", _stub_run_with_agent)
    rc = smoketest.main(["--workflow", "all", "--from-fixtures"])
    capsys.readouterr()

    assert rc == 1


# ---------------------------------------------------------------------------
# Live-mode preflight
# ---------------------------------------------------------------------------


def test_live_mode_without_api_key_aborts_with_two(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _patch_run_to(monkeypatch, meets_target=True)
    # _make_client should never be called.
    monkeypatch.setattr(
        smoketest, "_make_client",
        lambda base_url: pytest.fail("client built before key check"),
    )

    rc = smoketest.main(["--workflow", "demand-prediction"])
    err = capsys.readouterr().err

    assert rc == 2
    assert "ANTHROPIC_API_KEY" in err


def test_live_mode_with_api_key_proceeds(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    _patch_client(monkeypatch)
    _patch_run_to(monkeypatch, meets_target=True)

    # Stub generate_full_pipeline so no real Anthropic call occurs.
    from ownevo_kernel.nl_gen import NLGenPipelineResult
    from ownevo_kernel.nl_gen.fixtures import (
        EVAL_CASE_SET_FIXTURES,
        FIXTURES,
        METRIC_FIXTURES,
        SIM_PLAN_FIXTURES,
    )

    async def _stub_pipeline(client, description, *, model=None, max_tokens=None):
        # Use demand-prediction as the canned response for any description.
        wid = "demand-prediction"
        return NLGenPipelineResult(
            workflow_spec=FIXTURES[wid],
            simulation_plan=SIM_PLAN_FIXTURES[wid],
            eval_case_set=EVAL_CASE_SET_FIXTURES[wid],
            metric_definition=METRIC_FIXTURES[wid],
        )

    monkeypatch.setattr(smoketest, "generate_full_pipeline", _stub_pipeline)
    rc = smoketest.main(["--workflow", "demand-prediction"])
    capsys.readouterr()
    assert rc == 0


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_default_omits_outcomes(monkeypatch, capsys):
    _patch_client(monkeypatch)
    _patch_run_to(monkeypatch, meets_target=True)

    smoketest.main(["--workflow", "demand-prediction", "--from-fixtures"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert "outcomes" not in payload


def test_include_outcomes_adds_array(monkeypatch, capsys):
    _patch_client(monkeypatch)
    _patch_run_to(monkeypatch, meets_target=True)

    smoketest.main(
        ["--workflow", "demand-prediction", "--from-fixtures", "--include-outcomes"]
    )
    payload = json.loads(capsys.readouterr().out.strip())
    assert "outcomes" in payload
    # Stub report carries empty outcomes; the key should be there regardless.
    assert isinstance(payload["outcomes"], list)


def test_pretty_emits_indented_json(monkeypatch, capsys):
    _patch_client(monkeypatch)
    _patch_run_to(monkeypatch, meets_target=True)

    smoketest.main(
        ["--workflow", "demand-prediction", "--from-fixtures", "--pretty"]
    )
    out = capsys.readouterr().out
    assert "\n  " in out


def test_default_output_keys_are_sorted(monkeypatch, capsys):
    _patch_client(monkeypatch)
    _patch_run_to(monkeypatch, meets_target=True)

    smoketest.main(["--workflow", "demand-prediction", "--from-fixtures"])
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    assert out == canonical


def test_workflow_id_and_wall_seconds_in_output(monkeypatch, capsys):
    _patch_client(monkeypatch)
    _patch_run_to(monkeypatch, meets_target=True)

    smoketest.main(["--workflow", "demand-prediction", "--from-fixtures"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["workflow_id"] == "demand-prediction"
    assert isinstance(payload["wall_seconds"], (int, float))
    assert payload["wall_seconds"] >= 0


# ---------------------------------------------------------------------------
# Argparse rejection
# ---------------------------------------------------------------------------


def test_unknown_workflow_rejected_by_argparse():
    with pytest.raises(SystemExit) as exc_info:
        smoketest.main(["--workflow", "nonexistent", "--from-fixtures"])
    assert exc_info.value.code == 2


def test_workflow_argument_required():
    with pytest.raises(SystemExit) as exc_info:
        smoketest.main(["--from-fixtures"])
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# --max-cases truncation
# ---------------------------------------------------------------------------


def test_max_cases_below_minimum_class_count_raises_via_validator(
    monkeypatch, capsys
):
    """Cap dropped below 3-of-each-class → EvalCaseSet validator fires.

    The CLI surfaces the underlying ValidationError; the operator sees
    that the cap was too aggressive."""
    _patch_client(monkeypatch)
    _patch_run_to(monkeypatch, meets_target=True)

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        smoketest.main(
            ["--workflow", "demand-prediction", "--from-fixtures", "--max-cases", "2"]
        )


def test_max_cases_above_total_is_no_op(monkeypatch, capsys):
    """Cap >= len(cases) leaves the case set untouched."""
    _patch_client(monkeypatch)
    _patch_run_to(monkeypatch, meets_target=True)

    rc = smoketest.main(
        ["--workflow", "demand-prediction", "--from-fixtures", "--max-cases", "100"]
    )
    capsys.readouterr()
    assert rc == 0


# ---------------------------------------------------------------------------
# A4.5 — --max-tokens-per-workflow guardrail
# ---------------------------------------------------------------------------


def test_max_tokens_default_unset_no_budget_block_in_output(monkeypatch, capsys):
    _patch_client(monkeypatch)
    _patch_run_to(monkeypatch, meets_target=True)

    rc = smoketest.main(["--workflow", "demand-prediction", "--from-fixtures"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 0
    assert "token_budget" not in payload


def test_max_tokens_under_cap_emits_token_budget_block(monkeypatch, capsys):
    """When the run completes under the cap, the JSON output gains a
    `token_budget` block summarizing spend. The stub run_with_agent
    doesn't actually consume tokens, so used_total stays 0."""
    _patch_client(monkeypatch)
    _patch_run_to(monkeypatch, meets_target=True)

    rc = smoketest.main(
        [
            "--workflow",
            "demand-prediction",
            "--from-fixtures",
            "--max-tokens-per-workflow",
            "100000",
        ]
    )
    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 0
    assert payload["token_budget"]["max_tokens"] == 100_000
    assert payload["token_budget"]["used_total"] == 0
    assert payload["token_budget"]["n_calls"] == 0


def test_max_tokens_exceeded_exits_three_with_structured_error(
    monkeypatch, capsys
):
    """When run_with_agent raises TokenBudgetExceededError, the CLI
    exits 3 and prints a structured error block to stdout."""
    _patch_client(monkeypatch)

    from ownevo_kernel.eval_runner import TokenBudgetExceededError

    async def _explode(case_set, plan, spec, metric, **kwargs):
        raise TokenBudgetExceededError(
            "test-injected: cap tipped",
            max_tokens=500,
            used_input=400,
            used_output=200,
            n_calls=3,
            last_label="tipping-case",
        )

    monkeypatch.setattr(smoketest, "run_with_agent", _explode)

    rc = smoketest.main(
        [
            "--workflow",
            "demand-prediction",
            "--from-fixtures",
            "--max-tokens-per-workflow",
            "500",
        ]
    )
    out = capsys.readouterr().out.strip()
    assert rc == 3
    payload = json.loads(out)
    assert payload["error"] == "token_budget_exceeded"
    assert payload["max_tokens"] == 500
    assert payload["used_total"] == 600
    assert payload["n_calls"] == 3
    assert payload["last_label"] == "tipping-case"
    assert payload["workflow_id"] == "demand-prediction"


def test_max_tokens_propagated_to_run_with_agent(monkeypatch, capsys):
    """The CLI constructs a TokenBudget and passes it down."""
    _patch_client(monkeypatch)

    captured: dict = {}

    async def _capture(case_set, plan, spec, metric, **kwargs):
        captured["budget"] = kwargs.get("budget")
        return _stub_report(spec.id, meets_target=True)

    monkeypatch.setattr(smoketest, "run_with_agent", _capture)
    smoketest.main(
        [
            "--workflow",
            "credit-risk",
            "--from-fixtures",
            "--max-tokens-per-workflow",
            "12345",
        ]
    )
    capsys.readouterr()
    assert captured["budget"] is not None
    assert captured["budget"].max_tokens == 12345
