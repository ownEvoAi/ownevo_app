"""Tests for `scripts/cluster_label_eval.py` (B3.5 CLI).

Pins:
  * Argparse rejection (zero/negative concurrency, out-of-range
    agreement threshold).
  * Preflight rejects when ANTHROPIC_API_KEY is unset and no
    --anthropic-base-url is provided (exit 2).
  * Preflight rejects when --judge-model == --labeler-model (D4
    "different model from labeler" contract; exit 2).
  * Happy path with mocked judge + labeler: exit 0, JSON on stdout
    with expected keys.
  * --include-records adds the records list.
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

import cluster_label_eval as cli  # noqa: E402
from ownevo_kernel.clustering.label_eval import runner as runner_module  # noqa: E402
from ownevo_kernel.clustering.label_eval.judgment import (  # noqa: E402
    ClusterLabelJudgment,
)


def _stamp(cluster_id: str, verdict: str = "agree") -> ClusterLabelJudgment:
    return ClusterLabelJudgment.model_validate(
        {
            "schema_version": "0.1",
            "cluster_id": cluster_id,
            "verdict": verdict,
            "rationale": "stamped for test",
        }
    )


@pytest.fixture
def mocked_judge_and_clients(monkeypatch):
    """Always-agree judge + dummy clients + canned label_fn."""

    async def stamper(client, case, candidate_label, **kw):
        return _stamp(case.cluster_id, verdict="agree")

    monkeypatch.setattr(runner_module, "judge_label_match", stamper)

    class _FakeAsync:
        pass

    async def _canned_label(sample_texts, cluster_index):
        return f"candidate-{cluster_index}"

    monkeypatch.setattr(cli, "_make_async_client", lambda url: _FakeAsync())
    monkeypatch.setattr(cli, "_make_label_fn", lambda model, url: _canned_label)
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
    assert ns.judge_model
    assert ns.labeler_model
    assert ns.judge_model != ns.labeler_model
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


def test_preflight_aborts_when_judge_equals_labeler(monkeypatch, mocked_judge_and_clients):
    """D4 contract: must reject before any API call."""
    rc = cli.main([
        "--judge-model", "claude-haiku-4-5-20251001",
        "--labeler-model", "claude-haiku-4-5-20251001",
    ])
    assert rc == 2


def test_preflight_passes_with_base_url(monkeypatch, mocked_judge_and_clients):
    """--anthropic-base-url substitutes for ANTHROPIC_API_KEY."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main(["--anthropic-base-url", "http://localhost:4001"])
    assert rc == 0


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_emits_json_summary(mocked_judge_and_clients):
    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main([])
    assert rc == 0
    payload = json.loads(f.getvalue())
    assert payload["n_total"] == 20
    assert payload["agreement"] == 1.0
    assert "per_hint_correct" in payload
    assert "verdict_distribution" in payload
    assert "wall_seconds" in payload
    assert payload["judge_model"]


def test_default_omits_records(mocked_judge_and_clients):
    f = io.StringIO()
    with redirect_stdout(f):
        cli.main([])
    payload = json.loads(f.getvalue())
    assert "records" not in payload


def test_include_records_adds_records(mocked_judge_and_clients):
    f = io.StringIO()
    with redirect_stdout(f):
        cli.main(["--include-records"])
    payload = json.loads(f.getvalue())
    assert "records" in payload
    assert len(payload["records"]) == 20


def test_pretty_emits_indented_json(mocked_judge_and_clients):
    f = io.StringIO()
    with redirect_stdout(f):
        cli.main(["--pretty"])
    output = f.getvalue()
    assert output.startswith("{\n")
    assert "  \"n_total\":" in output


def test_judge_model_override_threads_through(mocked_judge_and_clients):
    f = io.StringIO()
    with redirect_stdout(f):
        cli.main(["--judge-model", "claude-opus-4-7"])
    payload = json.loads(f.getvalue())
    assert payload["judge_model"] == "claude-opus-4-7"


# ---------------------------------------------------------------------------
# Agreement gate
# ---------------------------------------------------------------------------


def test_require_agreement_passes_when_judge_is_perfect(mocked_judge_and_clients):
    """Stamper says agree on every case → agreement 1.0; threshold 0.7 → exit 0."""
    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main(["--require-agreement", "0.7"])
    assert rc == 0


def test_require_agreement_fails_when_judge_is_below_threshold(monkeypatch):
    """Always-disagree judge → agreement 0.0; threshold 0.7 → exit 1."""
    async def disagreer(client, case, candidate_label, **kw):
        return _stamp(case.cluster_id, verdict="disagree")

    monkeypatch.setattr(runner_module, "judge_label_match", disagreer)

    class _FakeAsync:
        pass

    async def _canned_label(sample_texts, cluster_index):
        return "wrong"

    monkeypatch.setattr(cli, "_make_async_client", lambda url: _FakeAsync())
    monkeypatch.setattr(cli, "_make_label_fn", lambda model, url: _canned_label)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main(["--require-agreement", "0.7"])
    assert rc == 1
    payload = json.loads(f.getvalue())
    assert payload["agreement"] == 0.0


def test_require_agreement_unset_ignores_low_score(monkeypatch):
    """Default (no --require-agreement) → exit 0 even on disagreement."""
    async def disagreer(client, case, candidate_label, **kw):
        return _stamp(case.cluster_id, verdict="disagree")

    monkeypatch.setattr(runner_module, "judge_label_match", disagreer)

    class _FakeAsync:
        pass

    async def _canned_label(sample_texts, cluster_index):
        return "wrong"

    monkeypatch.setattr(cli, "_make_async_client", lambda url: _FakeAsync())
    monkeypatch.setattr(cli, "_make_label_fn", lambda model, url: _canned_label)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    f = io.StringIO()
    with redirect_stdout(f):
        rc = cli.main([])
    assert rc == 0
