"""Tests for `scripts/llm_judge_approver_eval.py` (W5.2 CLI).

Pins:
  * Argparse rejection (zero/negative concurrency, out-of-range
    agreement threshold).
  * Preflight rejects when ANTHROPIC_API_KEY is unset and no
    --anthropic-base-url is provided (exit 2).
  * Happy path with mocked judge: exit 0, JSON on stdout with the
    expected aggregate keys.
  * --include-records adds the records list (30 entries).
  * --pretty re-emits the same content as 2-space JSON.
  * --require-agreement enforces the threshold (exit 1 on miss; exit
    0 on pass).
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT / "scripts"))

import llm_judge_approver_eval as cli  # noqa: E402
from ownevo_kernel.approvers.llm_judge import runner as runner_module  # noqa: E402
from ownevo_kernel.approvers.llm_judge.judgment import (  # noqa: E402
    LLMJudgeApprovalJudgment,
)


def _stamp(case_id: str, verdict: str = "admit") -> LLMJudgeApprovalJudgment:
    _present = verdict == "admit"
    return LLMJudgeApprovalJudgment.model_validate(
        {
            "schema_version": "0.1",
            "proposal_id": case_id,
            "cluster_referenced": {
                "present": _present,
                "quote": "stub-cluster-ref" if _present else "",
            },
            "change_named": {
                "present": _present,
                "quote": "stub-change-name" if _present else "",
            },
            "metric_direction_stated": {
                "present": _present,
                "quote": "stub-direction" if _present else "",
            },
            "verdict": verdict,
            "rationale": "stamped for test",
        }
    )


@pytest.fixture
def mocked_perfect_judge(monkeypatch):
    """Always-correct judge: returns ground-truth verdict for every case."""

    async def stamper(client, case, **kw):
        return _stamp(case.case_id, verdict=case.ground_truth_verdict)

    monkeypatch.setattr(runner_module, "judge_proposal_explanation", stamper)

    class _FakeAsync:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(cli, "_make_async_client", lambda url: _FakeAsync())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


@pytest.fixture
def mocked_always_admit(monkeypatch):
    """Always-admit judge: agreement = (#admit ground-truths) / 30 = 10/30."""

    async def stamper(client, case, **kw):
        return _stamp(case.case_id, verdict="admit")

    monkeypatch.setattr(runner_module, "judge_proposal_explanation", stamper)

    class _FakeAsync:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(cli, "_make_async_client", lambda url: _FakeAsync())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def test_concurrency_zero_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--concurrency", "0"])


def test_concurrency_negative_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--concurrency", "-1"])


def test_max_tokens_zero_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--max-tokens", "0"])


def test_max_retries_negative_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--max-retries-per-call", "-1"])


def test_require_agreement_out_of_range_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--require-agreement", "1.5"])


def test_require_agreement_negative_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--require-agreement", "-0.1"])


def test_default_args_parse():
    ns = cli._parse_args([])
    assert ns.judge_model
    assert ns.concurrency == 1
    assert ns.require_agreement is None
    assert ns.include_records is False


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def test_preflight_aborts_when_api_key_unset(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = cli.main([])
    assert rc == 2


def test_preflight_passes_with_base_url(monkeypatch, mocked_perfect_judge):
    """--anthropic-base-url substitutes for ANTHROPIC_API_KEY."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main(["--anthropic-base-url", "http://localhost:4001"])
    assert rc == 0


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_emits_json_summary(mocked_perfect_judge):
    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main([])
    assert rc == 0
    payload = json.loads(f.getvalue())
    assert payload["n_total"] == 30
    assert payload["agreement"] == 1.0
    assert "per_bucket_correct" in payload
    assert "verdict_distribution" in payload
    assert "wall_seconds" in payload
    assert payload["judge_model"]


def test_default_omits_records(mocked_perfect_judge):
    f = io.StringIO()
    with redirect_stdout(f):
        cli.main([])
    payload = json.loads(f.getvalue())
    assert "records" not in payload


def test_include_records_adds_records(mocked_perfect_judge):
    f = io.StringIO()
    with redirect_stdout(f):
        cli.main(["--include-records"])
    payload = json.loads(f.getvalue())
    assert "records" in payload
    assert len(payload["records"]) == 30


def test_pretty_emits_indented_json(mocked_perfect_judge):
    f = io.StringIO()
    with redirect_stdout(f):
        cli.main(["--pretty"])
    output = f.getvalue()
    assert output.startswith("{\n")
    assert "  \"n_total\":" in output


def test_judge_model_override_threads_through(mocked_perfect_judge):
    f = io.StringIO()
    with redirect_stdout(f):
        cli.main(["--judge-model", "claude-haiku-4-5-20251001"])
    payload = json.loads(f.getvalue())
    assert payload["judge_model"] == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Agreement gate
# ---------------------------------------------------------------------------


def test_require_agreement_passes_when_judge_is_perfect(mocked_perfect_judge):
    """Perfect judge → agreement 1.0; threshold 0.85 → exit 0."""
    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main(["--require-agreement", "0.85"])
    assert rc == 0


def test_require_agreement_fails_when_judge_is_below_threshold(
    mocked_always_admit,
):
    """Always-admit judge → agreement = 10/30 ≈ 0.333; 0.85 → exit 1."""
    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main(["--require-agreement", "0.85"])
    assert rc == 1
    payload = json.loads(f.getvalue())
    assert payload["agreement"] == pytest.approx(10 / 30)


def test_require_agreement_unset_ignores_low_score(mocked_always_admit):
    """Default (no --require-agreement) → exit 0 even on disagreement."""
    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main([])
    assert rc == 0
