"""Smoke tests for `scripts/nl_gen_demo_loop.py` (W6 CLI internals).

Pure-Python coverage of the argparse layer, the `--require-*` gate
checker, and the instruction-redaction layer. The DB-free /
network-free integration story is exercised in `test_nl_gen_loop.py`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT / "scripts"))

import nl_gen_demo_loop as cli  # noqa: E402
from ownevo_kernel.nl_gen.loop import (  # noqa: E402
    CycleOutcome,
    DemoLoopReport,
)


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def test_parse_args_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-default")
    args = cli._parse_args([])
    assert args.workflow == "demand-prediction"
    assert args.cycles == 3
    assert args.agent_model == "claude-sonnet-4-6"
    assert args.proposer_model == "claude-sonnet-4-6"
    assert args.proposer_max_tokens == 1_500
    assert args.api_key == "sk-test-default"
    assert args.pretty is False
    assert args.include_instructions is False
    assert args.progress is False
    assert args.require_climbing is False
    assert args.require_lift is None
    assert args.require_meets_target is False


def test_parse_args_progress_flag(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    args = cli._parse_args(["--progress"])
    assert args.progress is True


def test_parse_args_explicit_workflow_and_cycles(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    args = cli._parse_args(
        ["--workflow", "credit-risk", "--cycles", "5", "--pretty"]
    )
    assert args.workflow == "credit-risk"
    assert args.cycles == 5
    assert args.pretty is True


def test_parse_args_explicit_api_key_overrides_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    args = cli._parse_args(["--anthropic-api-key", "sk-cli"])
    assert args.api_key == "sk-cli"


def test_parse_args_rejects_missing_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit) as ei:
        cli._parse_args([])
    assert ei.value.code == 2


def test_parse_args_rejects_unknown_workflow(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with pytest.raises(SystemExit) as ei:
        cli._parse_args(["--workflow", "banana"])
    assert ei.value.code == 2


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_parse_args_rejects_non_positive_cycles(monkeypatch, bad: str):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with pytest.raises(SystemExit) as ei:
        cli._parse_args(["--cycles", bad])
    assert ei.value.code == 2


@pytest.mark.parametrize("bad", ["0", "-0.5", "0.0"])
def test_parse_args_rejects_non_positive_require_lift(monkeypatch, bad: str):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with pytest.raises(SystemExit) as ei:
        cli._parse_args(["--require-lift", bad])
    assert ei.value.code == 2


def test_parse_args_require_flags_propagate(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    args = cli._parse_args(
        [
            "--require-climbing",
            "--require-lift", "0.05",
            "--require-meets-target",
        ]
    )
    assert args.require_climbing is True
    assert args.require_lift == 0.05
    assert args.require_meets_target is True


# ---------------------------------------------------------------------------
# _check_gates — fixture-built reports (no LLM)
# ---------------------------------------------------------------------------


def _outcome(idx: int, *, value: float, meets: bool) -> CycleOutcome:
    return CycleOutcome(
        cycle_index=idx,
        metric_value=value,
        meets_target=meets,
        n_failures=0,
        n_clusters=0,
        cluster_signal="ok",
        cluster_signal_reason=None,
        top_cluster_label=None,
        top_cluster_size=0,
        instruction_before=None,
        instruction_after=None,
        instruction_edit=None,
        wall_seconds=0.0,
    )


def _report(*, curve: list[float], meets_target: bool, target: float = 0.5) -> DemoLoopReport:
    cycles = tuple(
        _outcome(i, value=v, meets=(i == len(curve) - 1 and meets_target))
        for i, v in enumerate(curve)
    )
    return DemoLoopReport(
        workflow_spec_id="supply-chain-demand-forecast",
        cycles=cycles,
        started_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
        metric_target=target,
        metric_family="recall",
        metric_direction="maximize",
    )


def _args(**overrides) -> cli.CliArgs:
    base = {
        "workflow": "demand-prediction",
        "cycles": 3,
        "agent_model": "claude-sonnet-4-6",
        "proposer_model": "claude-sonnet-4-6",
        "proposer_max_tokens": 1_500,
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-test",
        "pretty": False,
        "include_instructions": False,
        "progress": False,
        "require_climbing": False,
        "require_lift": None,
        "require_meets_target": False,
    }
    base.update(overrides)
    return cli.CliArgs(**base)


def test_check_gates_no_requirements_returns_empty():
    report = _report(curve=[0.2, 0.5, 0.8], meets_target=True)
    assert cli._check_gates(_args(), report) == []


def test_check_gates_climbing_passes_on_ascending_curve():
    report = _report(curve=[0.2, 0.5, 0.8], meets_target=False)
    assert cli._check_gates(_args(require_climbing=True), report) == []


def test_check_gates_climbing_fails_on_flat_curve():
    report = _report(curve=[0.5, 0.5, 0.5], meets_target=False)
    failures = cli._check_gates(_args(require_climbing=True), report)
    assert len(failures) == 1
    assert "did not climb" in failures[0]


def test_check_gates_lift_passes_when_threshold_met():
    report = _report(curve=[0.2, 0.5, 0.7], meets_target=False)
    assert cli._check_gates(_args(require_lift=0.4), report) == []


def test_check_gates_lift_fails_when_threshold_missed():
    report = _report(curve=[0.2, 0.3, 0.4], meets_target=False)
    failures = cli._check_gates(_args(require_lift=0.4), report)
    assert len(failures) == 1
    assert "absolute_lift" in failures[0]


def test_check_gates_lift_passes_at_exact_threshold():
    # lift = 0.6 - 0.2 = 0.4 exactly equals threshold — gate uses strict <, so should pass
    report = _report(curve=[0.2, 0.6], meets_target=False)
    assert cli._check_gates(_args(require_lift=0.4), report) == []


def test_check_gates_meets_target_passes_when_final_clears():
    report = _report(curve=[0.2, 0.5, 0.8], meets_target=True, target=0.5)
    assert cli._check_gates(_args(require_meets_target=True), report) == []


def test_check_gates_meets_target_fails_when_final_misses():
    report = _report(curve=[0.2, 0.4, 0.45], meets_target=False, target=0.5)
    failures = cli._check_gates(_args(require_meets_target=True), report)
    assert len(failures) == 1
    assert "did not clear target" in failures[0]


def test_check_gates_combines_multiple_failures():
    report = _report(curve=[0.5, 0.5, 0.5], meets_target=False, target=0.6)
    failures = cli._check_gates(
        _args(require_climbing=True, require_lift=0.1, require_meets_target=True),
        report,
    )
    assert len(failures) == 3


# ---------------------------------------------------------------------------
# Instruction redaction — keeps CLI output compact by default
# ---------------------------------------------------------------------------


def _sample_dict() -> dict:
    return {
        "lift_curve": [0.2, 0.5, 0.8],
        "cycles": [
            {
                "cycle_index": 0,
                "instruction_before": None,
                "instruction_after": "x" * 250,
                "instruction_edit": {
                    "cluster_label": "winter-spike",
                    "rationale": "failures cluster on holiday weeks",
                    "appended_text": "y" * 700,
                    "schema_version": "0.1",
                },
            },
            {
                "cycle_index": 1,
                "instruction_before": "x" * 250,
                "instruction_after": "x" * 250 + "\n\n" + "z" * 300,
                "instruction_edit": None,
            },
        ],
    }


def test_redact_keeps_structure_when_include_true():
    """Include flag passes the dict through unchanged — verbose CLI mode."""
    d = _sample_dict()
    out = cli._redact_instructions_in_dict(d, include=True)
    assert out is d


def test_redact_replaces_long_strings_with_char_count():
    d = _sample_dict()
    out = cli._redact_instructions_in_dict(d, include=False)
    cycle0 = out["cycles"][0]
    assert "instruction_after" in cycle0
    assert "<250 chars" in cycle0["instruction_after"]
    # Edit dict is reshaped: text replaced with length, label/rationale kept
    edit = cycle0["instruction_edit"]
    assert edit["cluster_label"] == "winter-spike"
    assert edit["rationale"] == "failures cluster on holiday weeks"
    assert edit["appended_text_chars"] == 700
    assert "appended_text" not in edit  # full text suppressed


def test_redact_preserves_none_instructions():
    d = _sample_dict()
    out = cli._redact_instructions_in_dict(d, include=False)
    cycle0 = out["cycles"][0]
    assert cycle0["instruction_before"] is None  # was None — stays None
    # cycle 1: instruction_edit was None — must stay None (not reshaped)
    cycle1 = out["cycles"][1]
    assert cycle1["instruction_edit"] is None
    # cycle 1: instruction_before was 250 chars — must be replaced with length hint
    assert "<250 chars" in cycle1["instruction_before"]
