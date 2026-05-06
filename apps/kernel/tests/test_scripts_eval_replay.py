"""Tests for `scripts/eval_replay.py` (A4.3 CLI).

In-process invocation via `main(argv)` — same pattern as
`test_scripts_run_improvement_loop.py`. Captures stdout via the
`capsys` fixture.

Pins:
  * Per-workflow JSON output shape + sorted-keys + meets_target=True.
  * `--workflow all` emits one JSON object per line and exits 0 only
    when every workflow meets target.
  * Bad workflow id rejected by argparse (exit 2, not 1).
  * `--include-outcomes` toggles the outcomes array.
  * Pretty-print emits 2-space indented JSON.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from scripts.eval_replay import WORKFLOW_CHOICES, main  # noqa: E402


# ---------------------------------------------------------------------------
# Single-workflow happy paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_id", WORKFLOW_CHOICES)
def test_single_workflow_exits_zero_and_emits_one_line(workflow_id, capsys):
    rc = main(["--workflow", workflow_id])
    out = capsys.readouterr().out.strip()

    assert rc == 0
    lines = out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["meets_target"] is True
    assert payload["value"] == pytest.approx(1.0)


@pytest.mark.parametrize("workflow_id", WORKFLOW_CHOICES)
def test_single_workflow_default_omits_outcomes(workflow_id, capsys):
    main(["--workflow", workflow_id])
    payload = json.loads(capsys.readouterr().out.strip())
    assert "outcomes" not in payload


@pytest.mark.parametrize("workflow_id", WORKFLOW_CHOICES)
def test_include_outcomes_flag_adds_outcomes_array(workflow_id, capsys):
    main(["--workflow", workflow_id, "--include-outcomes"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert "outcomes" in payload
    assert isinstance(payload["outcomes"], list)
    assert len(payload["outcomes"]) >= 10
    for o in payload["outcomes"]:
        assert "case_id" in o
        assert "passed" in o


@pytest.mark.parametrize("workflow_id", WORKFLOW_CHOICES)
def test_pretty_flag_indents_json(workflow_id, capsys):
    main(["--workflow", workflow_id, "--pretty"])
    out = capsys.readouterr().out
    assert "\n  " in out  # 2-space indent → newline + 2 spaces appears


def test_default_output_is_single_line_compact_json(capsys):
    main(["--workflow", "demand-prediction"])
    out = capsys.readouterr().out
    # Compact: no 2-space indents, single line.
    assert "\n  " not in out
    assert out.count("\n") == 1


# ---------------------------------------------------------------------------
# --workflow all
# ---------------------------------------------------------------------------


def test_all_emits_one_line_per_workflow_and_exits_zero(capsys):
    rc = main(["--workflow", "all"])
    out = capsys.readouterr().out.strip()
    lines = out.splitlines()

    assert rc == 0
    assert len(lines) == len(WORKFLOW_CHOICES)
    workflow_ids_emitted = {
        json.loads(line)["workflow_spec_id"] for line in lines
    }
    # Every workflow appears exactly once.
    assert len(workflow_ids_emitted) == len(WORKFLOW_CHOICES)


def test_all_output_lines_each_meet_target(capsys):
    main(["--workflow", "all"])
    for line in capsys.readouterr().out.strip().splitlines():
        payload = json.loads(line)
        assert payload["meets_target"] is True


# ---------------------------------------------------------------------------
# JSON canonicalization for the audit chain
# ---------------------------------------------------------------------------


def test_default_output_keys_are_sorted(capsys):
    """Audit chain canonicalization assumes sorted-keys."""
    main(["--workflow", "demand-prediction"])
    out = capsys.readouterr().out.strip()
    # Re-encode with sort_keys=True and compare — must be byte-identical.
    payload = json.loads(out)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    assert out == canonical


# ---------------------------------------------------------------------------
# Argparse rejection
# ---------------------------------------------------------------------------


def test_unknown_workflow_rejected_by_argparse():
    with pytest.raises(SystemExit) as exc_info:
        main(["--workflow", "not-a-real-workflow"])
    # argparse exits with 2 on usage errors.
    assert exc_info.value.code == 2


def test_workflow_argument_required():
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# Exit-code semantics — gate-style miss
# ---------------------------------------------------------------------------


def test_miss_returns_one(monkeypatch, capsys):
    """If a workflow's report doesn't meet target, exit 1.

    Patch `_run_one` to return a synthetic missed-target report so the
    test doesn't depend on the fixtures regressing."""
    from ownevo_kernel.eval_runner import EvalRunReport
    import scripts.eval_replay as cli

    def _missed(workflow_id):
        return EvalRunReport(
            workflow_spec_id=workflow_id,
            metric_name="demo",
            metric_family="recall",
            direction="maximize",
            value=0.1,
            target_value=0.8,
            meets_target=False,
            degenerate=False,
            n_total=10,
            n_pass=1,
            tp=1,
            tn=0,
            fp=0,
            fn=9,
            outcomes=tuple(),
        )

    monkeypatch.setattr(cli, "_run_one", _missed)
    rc = main(["--workflow", "demand-prediction"])
    out = capsys.readouterr().out
    assert rc == 1
    assert json.loads(out)["meets_target"] is False
