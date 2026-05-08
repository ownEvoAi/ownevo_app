"""Pure-Python tests for the W5.4 replay helpers (no DB).

ReplayConfig validation, lift-curve math, sort-key helpers — these
live in their own file so the DB-skip marker on
`test_replay_seven_day.py` doesn't suppress them when the integration
DB isn't around.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.replay import (
    CycleSummary,
    ReplayConfig,
    ReplayReport,
)
from ownevo_kernel.replay.seven_day import (
    DEFAULT_WORKFLOW_ID,
    _task_id,
    _task_id_sort_key,
)


def test_default_config_passes_validation():
    cfg = ReplayConfig()
    assert cfg.n_cycles == 7
    assert cfg.workflow_id == DEFAULT_WORKFLOW_ID


def test_config_rejects_zero_cycles():
    with pytest.raises(ValueError, match="n_cycles"):
        ReplayConfig(n_cycles=0)


def test_config_rejects_negative_lift():
    with pytest.raises(ValueError, match="lift_per_cycle"):
        ReplayConfig(lift_per_cycle=-1)


def test_config_rejects_priors_exceeding_total():
    with pytest.raises(ValueError, match="exceed n_total_tasks"):
        ReplayConfig(n_initial_priors=20, n_total_tasks=10)


def test_config_rejects_negative_cluster_cases():
    with pytest.raises(ValueError, match="cluster_cases_per_cycle"):
        ReplayConfig(cluster_cases_per_cycle=-1)


def test_config_rejects_zero_total_tasks():
    with pytest.raises(ValueError, match="n_total_tasks"):
        ReplayConfig(n_total_tasks=0)


def test_task_id_sort_key_is_numeric_not_lexical():
    """Lex sorting puts task-10 before task-2; we want numeric order."""
    ids = ["task-10", "task-2", "task-1", "task-100"]
    ids.sort(key=_task_id_sort_key)
    assert ids == ["task-1", "task-2", "task-10", "task-100"]


def test_task_id_sort_key_falls_back_for_non_matching_ids():
    """Foreign ids (e.g., a future cluster-derived helper writing
    differently-shaped ids) fall to a stable lexical bucket."""
    ids = ["task-1", "extern-z", "task-2", "extern-a"]
    ids.sort(key=_task_id_sort_key)
    assert ids == ["task-1", "task-2", "extern-a", "extern-z"]


def test_task_id_helper():
    assert _task_id(7) == "task-7"


# ---------------------------------------------------------------------------
# Lift curve / climbing / to_dict
# ---------------------------------------------------------------------------


def _stub_cycle(idx: int, *, val_score: float | None) -> CycleSummary:
    return CycleSummary(
        cycle_index=idx,
        iteration_id=f"iter-{idx}",
        proposal_id=f"prop-{idx}",
        decision="gate-pass",
        val_score=val_score,
        best_ever_score_after=val_score,
        n_prior_cases=10 + idx,
        n_promotable=1,
        n_cluster_cases_added=1,
        judge_admitted=True,
    )


def _stub_report(
    cycles: tuple[CycleSummary, ...],
    *,
    eval_initial: int = 10,
    eval_final: int = 12,
) -> ReplayReport:
    return ReplayReport(
        workflow_id="w",
        n_cycles=len(cycles),
        cycles=cycles,
        eval_set_size_initial=eval_initial,
        eval_set_size_final=eval_final,
        cluster_derived_count=eval_final - eval_initial,
        audit_entry_count_after=2 * len(cycles),
        judge_admit_count=sum(1 for c in cycles if c.judge_admitted),
    )


def test_lift_curve_extracts_val_scores_in_order():
    cycles = (
        _stub_cycle(0, val_score=0.5),
        _stub_cycle(1, val_score=0.6),
        _stub_cycle(2, val_score=0.7),
    )
    report = _stub_report(cycles)
    assert report.lift_curve == (0.5, 0.6, 0.7)
    assert report.is_climbing()


def test_lift_curve_drops_none_entries():
    cycles = (
        _stub_cycle(0, val_score=0.5),
        _stub_cycle(1, val_score=None),
        _stub_cycle(2, val_score=0.6),
    )
    report = _stub_report(cycles)
    assert report.lift_curve == (0.5, 0.6)


def test_is_climbing_rejects_flat_curve():
    cycles = (
        _stub_cycle(0, val_score=0.5),
        _stub_cycle(1, val_score=0.5),
        _stub_cycle(2, val_score=0.5),
    )
    report = _stub_report(cycles)
    assert not report.is_climbing()


def test_is_climbing_rejects_dip_in_middle():
    cycles = (
        _stub_cycle(0, val_score=0.5),
        _stub_cycle(1, val_score=0.4),
        _stub_cycle(2, val_score=0.6),
    )
    report = _stub_report(cycles)
    assert not report.is_climbing()


def test_is_climbing_accepts_plateau_at_top():
    """The synthetic skill caps at 1.0 once every task passes; a
    plateau at the top should still count as climbing as long as the
    end is above the start."""
    cycles = (
        _stub_cycle(0, val_score=0.5),
        _stub_cycle(1, val_score=0.7),
        _stub_cycle(2, val_score=1.0),
        _stub_cycle(3, val_score=1.0),
    )
    report = _stub_report(cycles)
    assert report.is_climbing()


def test_is_climbing_requires_at_least_two_points():
    """Single cycle has no slope to measure — `is_climbing` returns
    False so the CLI's `--require-climbing` gate refuses a 1-cycle run
    rather than passing a meaningless trivial-true."""
    cycles = (_stub_cycle(0, val_score=0.5),)
    report = _stub_report(cycles)
    assert not report.is_climbing()


def test_to_dict_includes_lift_curve_and_cycles():
    cycles = (
        _stub_cycle(0, val_score=0.5),
        _stub_cycle(1, val_score=0.6),
    )
    report = _stub_report(cycles, eval_initial=10, eval_final=12)
    payload = report.to_dict()
    assert payload["lift_curve"] == [0.5, 0.6]
    assert payload["is_climbing"] is True
    assert payload["eval_set_size_initial"] == 10
    assert payload["eval_set_size_final"] == 12
    assert len(payload["cycles"]) == 2
    # Per-cycle structure pinned.
    cycle0 = payload["cycles"][0]
    assert cycle0["cycle_index"] == 0
    assert cycle0["val_score"] == 0.5
    assert cycle0["judge_admitted"] is True
