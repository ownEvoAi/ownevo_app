"""Smoke tests for ``scripts/m5_replay_30day.py`` (W6 CLI internals).

Pure-Python coverage of the argparse layer, the ``--conditions`` parser,
and the ``--require-lift`` gate checker. The DB-backed and subprocess-
spawning path is exercised in a follow-up integration test that needs
the M5 substrate + a running Postgres + the sandbox image.
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

import m5_replay_30day as cli  # noqa: E402
from ownevo_kernel.replay import (  # noqa: E402
    CONDITION_A_FROZEN,
    CONDITION_C_LOOP_AUTONOMOUS,
    CONDITION_D_LOOP_GATED,
    ConditionResult,
    IterationOutcome,
    ThirtyDayReport,
)


# ---------------------------------------------------------------------------
# _conditions_arg — parse "a,c,d" → ("A", "C", "D")
# ---------------------------------------------------------------------------


def test_conditions_arg_default_lowercase_letters():
    assert cli._conditions_arg("a,c,d") == ("A", "C", "D")


def test_conditions_arg_accepts_uppercase():
    assert cli._conditions_arg("A,C") == ("A", "C")


def test_conditions_arg_strips_whitespace():
    assert cli._conditions_arg("a , c, d  ") == ("A", "C", "D")


def test_conditions_arg_drops_empty_segments():
    """Trailing comma or doubled separators shouldn't blow up — silently
    dropped, mirrors how shells handle ``-D a,,c``."""
    assert cli._conditions_arg("a,,c,") == ("A", "C")


def test_conditions_arg_rejects_unknown():
    with pytest.raises(argparse.ArgumentTypeError, match="unknown condition"):
        cli._conditions_arg("x")


def test_conditions_arg_rejects_b_until_wired():
    """Condition B is documented as deferred; argparse rejects it so the
    user gets a clear message instead of silent skip."""
    with pytest.raises(argparse.ArgumentTypeError, match="unknown condition"):
        cli._conditions_arg("b")


def test_conditions_arg_rejects_duplicate():
    with pytest.raises(argparse.ArgumentTypeError, match="duplicate condition"):
        cli._conditions_arg("c,c")


def test_conditions_arg_rejects_empty():
    with pytest.raises(argparse.ArgumentTypeError, match="at least one condition"):
        cli._conditions_arg("")


def test_conditions_arg_rejects_whitespace_only():
    with pytest.raises(argparse.ArgumentTypeError, match="at least one condition"):
        cli._conditions_arg("  ,  ")


# ---------------------------------------------------------------------------
# _positive_int / _positive_float
# ---------------------------------------------------------------------------


def test_positive_int_happy():
    assert cli._positive_int("30") == 30


@pytest.mark.parametrize("bad", ["0", "-1", "-100"])
def test_positive_int_rejects_non_positive(bad: str):
    with pytest.raises(argparse.ArgumentTypeError, match="must be > 0"):
        cli._positive_int(bad)


def test_positive_int_rejects_non_numeric():
    with pytest.raises(argparse.ArgumentTypeError, match="expected integer"):
        cli._positive_int("banana")


def test_positive_float_happy():
    assert cli._positive_float("1.5") == 1.5


@pytest.mark.parametrize("bad", ["0", "0.0", "-0.5"])
def test_positive_float_rejects_non_positive(bad: str):
    with pytest.raises(argparse.ArgumentTypeError, match="must be > 0"):
        cli._positive_float(bad)


# ---------------------------------------------------------------------------
# _parse_args — top-level CLI surface
# ---------------------------------------------------------------------------


def test_parse_args_defaults():
    args = cli._parse_args([])
    assert args.conditions == ("A", "C", "D")
    assert args.max_iterations == cli.DEFAULT_MAX_ITERATIONS
    assert args.workflow_prefix == "m5-condition"
    assert args.judge_model == cli.DEFAULT_JUDGE_MODEL
    assert args.iteration_timeout_s is None
    assert args.halt_on_error is False
    assert args.reset is False
    assert args.pretty is False
    assert args.require_lift is None
    assert args.extra_loop_args == ()


def test_parse_args_subset_conditions_and_iterations():
    args = cli._parse_args(["--conditions", "c,d", "--max-iterations", "5"])
    assert args.conditions == ("C", "D")
    assert args.max_iterations == 5


def test_parse_args_workflow_prefix_override():
    args = cli._parse_args(["--workflow-prefix", "m5-2026-05"])
    assert args.workflow_prefix == "m5-2026-05"


def test_parse_args_pretty_and_reset_flags():
    args = cli._parse_args(["--pretty", "--reset", "--halt-on-error"])
    assert args.pretty is True
    assert args.reset is True
    assert args.halt_on_error is True


def test_parse_args_iteration_timeout():
    args = cli._parse_args(["--iteration-timeout-s", "1800"])
    assert args.iteration_timeout_s == 1800.0


def test_parse_args_require_lift_accepts_zero_and_negative():
    """``--require-lift 0`` is meaningful (any non-A condition must match
    or beat baseline). Negative values would mean 'allow regression' which
    is unusual but not invalid for diagnostic runs."""
    args = cli._parse_args(["--require-lift", "0"])
    assert args.require_lift == 0.0
    args = cli._parse_args(["--require-lift", "-0.05"])
    assert args.require_lift == -0.05


def test_parse_args_passthrough_extra_args():
    """Anything after a leading ``--`` is forwarded verbatim to each
    run_improvement_loop subprocess. The leading ``--`` separator itself
    is stripped."""
    args = cli._parse_args([
        "--max-iterations", "3",
        "--",
        "--m5-dir", "/data/m5",
        "--llm-model", "claude-sonnet-4-6",
        "--no-stream",
    ])
    assert args.extra_loop_args == (
        "--m5-dir",
        "/data/m5",
        "--llm-model",
        "claude-sonnet-4-6",
        "--no-stream",
    )


def test_parse_args_passthrough_empty_extra():
    args = cli._parse_args(["--", ])
    assert args.extra_loop_args == ()


def test_parse_args_rejects_bad_condition_letter():
    with pytest.raises(SystemExit) as ei:
        cli._parse_args(["--conditions", "x"])
    assert ei.value.code == 2


def test_parse_args_rejects_zero_iterations():
    with pytest.raises(SystemExit) as ei:
        cli._parse_args(["--max-iterations", "0"])
    assert ei.value.code == 2


# ---------------------------------------------------------------------------
# _check_gates — --require-lift logic
# ---------------------------------------------------------------------------


def _outcome(idx: int, *, best_after: float | None) -> IterationOutcome:
    return IterationOutcome(
        iteration_index=idx,
        decision="gate-pass",
        val_score=best_after,
        best_ever_score_before=None,
        best_ever_score_after=best_after,
        approval_decision=None,
        approver_type=None,
    )


def _condition(letter: str, *, final: float | None) -> ConditionResult:
    return ConditionResult(
        condition=letter,
        workflow_id=f"m5-condition-{letter.lower()}",
        iterations=(_outcome(0, best_after=final),),
    )


def _report(*, conditions: dict[str, ConditionResult]) -> ThirtyDayReport:
    return ThirtyDayReport(
        conditions=conditions,
        started_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )


def _args_with_lift(threshold: float | None, conditions: tuple[str, ...] = ("A", "C", "D")) -> cli.CliArgs:
    return cli.CliArgs(
        conditions=conditions,
        max_iterations=30,
        workflow_prefix="m5-condition",
        judge_model="claude-opus-4-7",
        iteration_timeout_s=None,
        halt_on_error=False,
        reset=False,
        pretty=False,
        require_lift=threshold,
        extra_loop_args=(),
    )


def test_check_gates_no_threshold_returns_empty():
    args = _args_with_lift(None)
    report = _report(conditions={"A": _condition("A", final=0.30)})
    assert cli._check_gates(args, report) == []


def test_check_gates_lift_threshold_met():
    args = _args_with_lift(0.05)
    report = _report(conditions={
        "A": _condition("A", final=0.30),
        "C": _condition("C", final=0.40),  # +0.10 ≥ 0.05
        "D": _condition("D", final=0.36),  # +0.06 ≥ 0.05
    })
    assert cli._check_gates(args, report) == []


def test_check_gates_lift_threshold_missed_for_one_condition():
    args = _args_with_lift(0.10)
    report = _report(conditions={
        "A": _condition("A", final=0.30),
        "C": _condition("C", final=0.45),  # +0.15 ≥ 0.10 ✓
        "D": _condition("D", final=0.35),  # +0.05 < 0.10 ✗
    })
    failures = cli._check_gates(args, report)
    assert len(failures) == 1
    assert "condition D" in failures[0]


def test_check_gates_baseline_a_missing_from_run():
    """If A wasn't selected, --require-lift can't establish the baseline.
    Surface as a failure rather than silently passing."""
    args = _args_with_lift(0.05, conditions=("C", "D"))
    report = _report(conditions={
        "C": _condition("C", final=0.40),
        "D": _condition("D", final=0.40),
    })
    failures = cli._check_gates(args, report)
    assert len(failures) == 1
    assert "needs condition A" in failures[0]


def test_check_gates_baseline_a_has_no_score():
    """A was selected but its iteration produced no score (e.g. seed
    didn't run or DB was empty). Treat as gate failure with a clear
    message so the user knows what to fix."""
    args = _args_with_lift(0.05)
    report = _report(conditions={
        "A": _condition("A", final=None),
        "C": _condition("C", final=0.40),
    })
    failures = cli._check_gates(args, report)
    assert len(failures) == 1
    assert "produced no baseline score" in failures[0]


def test_check_gates_non_a_condition_score_none_treated_as_miss():
    """A non-A condition with final=None can't beat the threshold — treat
    as a failure (its iterations all crashed or never ran)."""
    args = _args_with_lift(0.05)
    report = _report(conditions={
        "A": _condition("A", final=0.30),
        "C": _condition("C", final=None),
    })
    failures = cli._check_gates(args, report)
    assert len(failures) == 1
    assert "condition C" in failures[0]


def test_check_gates_zero_threshold_means_match_or_beat_baseline():
    args = _args_with_lift(0.0)
    report = _report(conditions={
        "A": _condition("A", final=0.30),
        "C": _condition("C", final=0.30),  # exactly baseline → pass (≥ baseline+0)
        "D": _condition("D", final=0.29),  # below baseline → fail
    })
    failures = cli._check_gates(args, report)
    assert len(failures) == 1
    assert "condition D" in failures[0]
