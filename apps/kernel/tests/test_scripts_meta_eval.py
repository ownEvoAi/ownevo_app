"""Tests for `scripts/meta_eval.py` (A4.6 CLI).

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

import meta_eval as cli  # noqa: E402

from ownevo_kernel.nl_gen.meta_eval import runner as runner_module  # noqa: E402
from ownevo_kernel.nl_gen.meta_eval.judgment import MetaEvalJudgment  # noqa: E402


def _stamp(spec_id: str, overall: str = "good") -> MetaEvalJudgment:
    return MetaEvalJudgment.model_validate(
        {
            "schema_version": "0.1",
            "workflow_spec_id": spec_id,
            "sim_coverage": {"verdict": "pass", "rationale": "x"},
            "eval_case_coverage": {"verdict": "pass", "rationale": "x"},
            "metric_alignment": {"verdict": "pass", "rationale": "x"},
            "overall_verdict": overall,
            "overall_rationale": "test",
        }
    )


@pytest.fixture
def mocked_judge_and_client(monkeypatch):
    """Fake AsyncAnthropic + always-good stamper judge."""

    async def stamper(client, description, spec, plan, case_set, metric, **kw):
        return _stamp(spec.id, overall="good")

    monkeypatch.setattr(runner_module, "judge_artifacts", stamper)

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
    assert payload["n_total"] == 20
    assert "agreement" in payload
    assert "per_dimension_distribution" in payload
    assert "per_recipe_correct" in payload
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
    assert len(payload["records"]) == 20


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


def test_require_agreement_passes_when_judge_is_perfect(monkeypatch):
    """Oracle judge: agreement = 1.0, threshold 0.7 → exit 0."""
    pair_by_spec_id = {
        p.good[0].id: p
        for p in __import__(
            "ownevo_kernel.nl_gen.meta_eval.eval_set", fromlist=["META_EVAL_SET"]
        ).META_EVAL_SET
    }

    async def oracle(client, description, spec, plan, case_set, metric, **kw):
        pair = pair_by_spec_id[spec.id]
        is_good = (
            metric == pair.good[3]
            and plan == pair.good[1]
            and case_set == pair.good[2]
        )
        return _stamp(spec.id, overall="good" if is_good else "bad")

    monkeypatch.setattr(runner_module, "judge_artifacts", oracle)

    class _FakeClient:
        pass

    monkeypatch.setattr(cli, "_make_client", lambda url: _FakeClient())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main(["--require-agreement", "0.7"])
    assert rc == 0


def test_require_agreement_fails_when_judge_is_always_good(
    monkeypatch, mocked_judge_and_client
):
    """Always-good judge: agreement = 0.5 < 0.7 → exit 1."""
    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main(["--require-agreement", "0.7"])
    assert rc == 1
    payload = json.loads(f.getvalue())
    assert payload["agreement"] == 0.5  # always-good gets 10/20


# ---------------------------------------------------------------------------
# Local-routing api_key default
# ---------------------------------------------------------------------------


def test_make_client_uses_local_api_key_when_base_url_set(monkeypatch):
    """When --anthropic-base-url routes to a local server, the Anthropic
    SDK still validates that *some* api_key header is present. Default it
    to ``"local"`` so callers don't need to set ANTHROPIC_API_KEY just to
    satisfy the SDK validator (regression target — the missing default
    bit during the 2026-05-08 W5.5 local-meta-eval attempt)."""
    pytest.importorskip("anthropic")  # CI's default extras don't ship it
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = cli._make_client("http://192.0.2.50:1234")
    assert client.api_key == "local"
    assert str(client.base_url).rstrip("/") == "http://192.0.2.50:1234"


def test_make_client_respects_explicit_env_key(monkeypatch):
    """When ANTHROPIC_API_KEY is set, the local-route client must use
    that value, not stomp it with the ``"local"`` placeholder."""
    pytest.importorskip("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "user-supplied")
    client = cli._make_client("http://192.0.2.50:1234")
    assert client.api_key == "user-supplied"
