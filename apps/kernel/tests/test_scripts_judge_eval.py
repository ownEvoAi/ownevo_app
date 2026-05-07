"""Tests for `scripts/judge_eval.py` (W5.2 CLI).

Pins:
  * Argparse rejection (zero/negative concurrency, out-of-range
    agreement threshold).
  * Preflight rejects when ANTHROPIC_API_KEY is unset and no
    --anthropic-base-url is provided (exit 2).
  * Happy path with mocked AsyncAnthropic + judge: exit 0, JSON on
    stdout with expected keys.
  * --include-records adds the records list.
  * --pretty re-emits the same content as 2-space JSON.
  * --require-agreement enforces the threshold (exit 1 on miss).
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

import judge_eval as cli  # noqa: E402
from ownevo_kernel.approvers import runner as runner_module  # noqa: E402
from ownevo_kernel.approvers.eval_set import JUDGE_EVAL_SET  # noqa: E402
from ownevo_kernel.approvers.judgment import ApprovalJudgment  # noqa: E402


def _stamp(
    proposal_id: str,
    *,
    refs: str = "pass",
    names: str = "pass",
    direction: str = "pass",
) -> ApprovalJudgment:
    return ApprovalJudgment.model_validate(
        {
            "schema_version": "0.1",
            "proposal_id": proposal_id,
            "references_cluster": {"verdict": refs, "rationale": "x"},
            "names_change": {"verdict": names, "rationale": "x"},
            "states_direction": {"verdict": direction, "rationale": "x"},
            "overall_rationale": "test",
        }
    )


@pytest.fixture
def mocked_judge_and_client(monkeypatch):
    """Fake AsyncAnthropic + always-admit stamper judge."""

    async def stamper(client, ctx, **kw):
        return _stamp(ctx.proposal_id)

    monkeypatch.setattr(runner_module, "judge_proposal", stamper)

    class _FakeClient:
        pass

    def _fake_make(base_url):
        return _FakeClient()

    monkeypatch.setattr(cli, "_make_client", _fake_make)
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


def test_require_agreement_out_of_range_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--require-agreement", "1.5"])


def test_require_agreement_negative_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--require-agreement", "-0.1"])


def test_default_args_parse():
    ns = cli._parse_args([])
    assert ns.model
    assert ns.concurrency == 1
    assert ns.require_agreement is None
    assert ns.include_records is False
    assert ns.smoke is False


def test_smoke_flag_runs_5_pair_subset(mocked_judge_and_client):
    """With `--smoke`, the runner sees the 5-pair subset, not the 30."""
    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main(["--smoke"])
    assert rc == 0
    payload = json.loads(f.getvalue())
    assert payload["n_total"] == 5


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def test_preflight_aborts_when_api_key_unset(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = cli.main([])
    assert rc == 2


def test_preflight_passes_with_base_url(monkeypatch, mocked_judge_and_client):
    """--anthropic-base-url substitutes for ANTHROPIC_API_KEY."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main(["--anthropic-base-url", "http://localhost:4001"])
    assert rc == 0


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_emits_json_summary(mocked_judge_and_client):
    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main([])
    assert rc == 0
    payload = json.loads(f.getvalue())
    assert payload["n_total"] == 30
    assert "agreement" in payload
    assert "per_bucket_correct" in payload
    assert "per_check_distribution" in payload
    assert "n_admit_correct" in payload
    assert "n_reject_correct" in payload
    assert "wall_seconds" in payload


def test_default_omits_records(mocked_judge_and_client):
    f = io.StringIO()
    with redirect_stdout(f):
        cli.main([])
    payload = json.loads(f.getvalue())
    assert "records" not in payload


def test_include_records_adds_records(mocked_judge_and_client):
    f = io.StringIO()
    with redirect_stdout(f):
        cli.main(["--include-records"])
    payload = json.loads(f.getvalue())
    assert "records" in payload
    assert len(payload["records"]) == 30


def test_pretty_emits_indented_json(mocked_judge_and_client):
    f = io.StringIO()
    with redirect_stdout(f):
        cli.main(["--pretty"])
    output = f.getvalue()
    assert output.startswith("{\n")
    assert "  \"n_total\":" in output


def test_model_override_threads_through(mocked_judge_and_client):
    f = io.StringIO()
    with redirect_stdout(f):
        cli.main(["--model", "claude-haiku-4-5"])
    payload = json.loads(f.getvalue())
    assert payload["model"] == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Agreement gate
# ---------------------------------------------------------------------------


def test_require_agreement_passes_for_oracle_judge(monkeypatch):
    """Oracle judge: agreement = 1.0, threshold 0.85 → exit 0."""

    async def oracle(client, ctx, **kw):
        pair = next(p for p in JUDGE_EVAL_SET if p.context.proposal_id == ctx.proposal_id)
        if pair.expected_admit:
            return _stamp(ctx.proposal_id)
        if pair.bucket_id == "reject-vague-positive":
            return _stamp(ctx.proposal_id, refs="fail", names="fail", direction="fail")
        if pair.bucket_id == "reject-wrong-direction":
            return _stamp(ctx.proposal_id, refs="pass", names="pass", direction="fail")
        if pair.bucket_id == "reject-handwavy-change":
            return _stamp(ctx.proposal_id, refs="pass", names="fail", direction="pass")
        if pair.bucket_id == "reject-missing-cluster":
            return _stamp(ctx.proposal_id, refs="fail", names="pass", direction="pass")
        raise AssertionError(f"unreachable bucket {pair.bucket_id}")

    monkeypatch.setattr(runner_module, "judge_proposal", oracle)

    class _FakeClient:
        pass

    monkeypatch.setattr(cli, "_make_client", lambda url: _FakeClient())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main(["--require-agreement", "0.85"])
    assert rc == 0


def test_require_agreement_fails_for_always_admit_judge(
    monkeypatch, mocked_judge_and_client
):
    """Always-admit judge: agreement = 6/30 = 0.2 < 0.85 → exit 1."""
    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main(["--require-agreement", "0.85"])
    assert rc == 1
    payload = json.loads(f.getvalue())
    assert payload["agreement"] == pytest.approx(6 / 30)
