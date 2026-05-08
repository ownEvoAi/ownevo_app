"""Smoke tests for `scripts/m5_replay_7day.py` (W5.4 CLI internals).

Pure-Python coverage of the argparse layer and the gate-checker. The
DB-backed end-to-end run lives in `test_replay_seven_day.py`.
"""

from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stderr
from pathlib import Path

import pytest

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT / "scripts"))

import m5_replay_7day as cli  # noqa: E402
from ownevo_kernel.replay import CycleSummary, ReplayReport  # noqa: E402

# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def test_default_args_parse():
    args = cli._parse_args([])
    assert args.cycles == 7
    assert args.workflow_id == "m5-replay-7day"
    assert args.n_initial_priors == 10
    assert args.n_total_tasks == 20
    assert args.lift_per_cycle == 1
    assert args.cluster_cases_per_cycle == 1
    assert args.reset is False
    assert args.pretty is False
    assert args.require_climbing is False
    assert args.require_audit_entries is None
    assert args.require_eval_growth is None


def test_cycles_zero_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--cycles", "0"])


def test_cycles_negative_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--cycles", "-1"])


def test_n_total_tasks_zero_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--n-total-tasks", "0"])


def test_lift_per_cycle_negative_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--lift-per-cycle", "-1"])


def test_cluster_cases_per_cycle_zero_accepted():
    """Zero is valid (disables growth) — only negatives are rejected."""
    args = cli._parse_args(["--cluster-cases-per-cycle", "0"])
    assert args.cluster_cases_per_cycle == 0


def test_require_audit_entries_zero_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--require-audit-entries", "0"])


def test_require_eval_growth_negative_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--require-eval-growth", "-1"])


def test_positive_int_helper_rejects_garbage():
    with pytest.raises(argparse.ArgumentTypeError):
        cli._positive_int("nope")


def test_non_negative_int_helper_rejects_garbage():
    with pytest.raises(argparse.ArgumentTypeError):
        cli._non_negative_int("nope")


def test_non_negative_int_helper_rejects_negative():
    with pytest.raises(argparse.ArgumentTypeError):
        cli._non_negative_int("-5")


# ---------------------------------------------------------------------------
# Preflight: missing DB env
# ---------------------------------------------------------------------------


def test_missing_db_env_returns_exit_2(monkeypatch):
    monkeypatch.delenv(cli.ENV_DB_URL, raising=False)
    rc = cli.main([])
    assert rc == 2


def test_missing_db_env_emits_helpful_stderr(monkeypatch):
    monkeypatch.delenv(cli.ENV_DB_URL, raising=False)
    err = io.StringIO()
    with redirect_stderr(err):
        cli.main([])
    assert cli.ENV_DB_URL in err.getvalue()


# ---------------------------------------------------------------------------
# _check_gates
# ---------------------------------------------------------------------------


def _stub_cycle(idx: int, *, val_score: float | None) -> CycleSummary:
    return CycleSummary(
        cycle_index=idx,
        iteration_id=f"iter-{idx}",
        proposal_id=f"prop-{idx}",
        decision="gate-pass",
        val_score=val_score,
        best_ever_score_after=val_score,
        n_prior_cases=10,
        n_promotable=1,
        n_cluster_cases_added=1,
        judge_admitted=True,
    )


def _stub_report(
    *,
    lift_curve: list[float],
    audit_entries: int,
    eval_initial: int,
    eval_final: int,
) -> ReplayReport:
    cycles = tuple(_stub_cycle(i, val_score=v) for i, v in enumerate(lift_curve))
    return ReplayReport(
        workflow_id="w",
        n_cycles=len(cycles),
        cycles=cycles,
        eval_set_size_initial=eval_initial,
        eval_set_size_final=eval_final,
        cluster_derived_count=eval_final - eval_initial,
        audit_entry_count_after=audit_entries,
        judge_admit_count=len(cycles),
    )


def _args(**overrides) -> cli.CliArgs:
    base = dict(
        cycles=7,
        workflow_id="w",
        n_initial_priors=10,
        n_total_tasks=20,
        lift_per_cycle=1,
        cluster_cases_per_cycle=1,
        reset=False,
        pretty=False,
        require_climbing=False,
        require_audit_entries=None,
        require_eval_growth=None,
    )
    base.update(overrides)
    return cli.CliArgs(**base)


def test_check_gates_no_gates_returns_empty():
    rep = _stub_report(
        lift_curve=[0.5, 0.6, 0.7],
        audit_entries=14,
        eval_initial=10,
        eval_final=12,
    )
    assert cli._check_gates(_args(), rep) == []


def test_check_gates_climbing_pass():
    rep = _stub_report(
        lift_curve=[0.5, 0.6, 0.7],
        audit_entries=14,
        eval_initial=10,
        eval_final=12,
    )
    assert cli._check_gates(_args(require_climbing=True), rep) == []


def test_check_gates_climbing_fail_on_flat_curve():
    rep = _stub_report(
        lift_curve=[0.5, 0.5, 0.5],
        audit_entries=14,
        eval_initial=10,
        eval_final=12,
    )
    failures = cli._check_gates(_args(require_climbing=True), rep)
    assert len(failures) == 1
    assert "did not end strictly above" in failures[0]


def test_check_gates_audit_entries_pass():
    rep = _stub_report(
        lift_curve=[0.5, 0.6],
        audit_entries=20,
        eval_initial=10,
        eval_final=12,
    )
    assert cli._check_gates(_args(require_audit_entries=14), rep) == []


def test_check_gates_audit_entries_fail():
    rep = _stub_report(
        lift_curve=[0.5, 0.6],
        audit_entries=4,
        eval_initial=10,
        eval_final=12,
    )
    failures = cli._check_gates(_args(require_audit_entries=14), rep)
    assert len(failures) == 1
    assert "only 4 entries" in failures[0]


def test_check_gates_eval_growth_pass():
    rep = _stub_report(
        lift_curve=[0.5, 0.6],
        audit_entries=20,
        eval_initial=10,
        eval_final=17,
    )
    assert cli._check_gates(_args(require_eval_growth=5), rep) == []


def test_check_gates_eval_growth_fail():
    rep = _stub_report(
        lift_curve=[0.5, 0.6],
        audit_entries=20,
        eval_initial=10,
        eval_final=11,
    )
    failures = cli._check_gates(_args(require_eval_growth=5), rep)
    assert len(failures) == 1
    assert "grew by only 1" in failures[0]


def test_check_gates_can_collect_multiple_failures_in_one_run():
    rep = _stub_report(
        lift_curve=[0.5, 0.5],
        audit_entries=2,
        eval_initial=10,
        eval_final=10,
    )
    failures = cli._check_gates(
        _args(
            require_climbing=True,
            require_audit_entries=14,
            require_eval_growth=5,
        ),
        rep,
    )
    assert len(failures) == 3
