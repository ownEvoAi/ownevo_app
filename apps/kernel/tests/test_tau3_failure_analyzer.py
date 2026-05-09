"""Unit tests for the τ³ failure analyzer (P1.5 / M7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ownevo_kernel.benchmark.tau3 import (
    Tau3FailureAnalyzerError,
    analyze_tau3_failures,
)


def _write_results(tmp_path: Path, sims: list[dict]) -> Path:
    p = tmp_path / "results.json"
    p.write_text(json.dumps({
        "info": {"environment_info": {"domain_name": "retail"}},
        "simulations": sims,
        "tasks": [{"id": s["task_id"]} for s in sims],
    }))
    return p


def test_passing_sims_excluded(tmp_path: Path):
    p = _write_results(tmp_path, [
        {"task_id": "0", "reward_info": {"reward": 1.0},
         "termination_reason": "agent_stop", "messages": []},
        {"task_id": "1", "reward_info": {"reward": 0.0},
         "termination_reason": "user_stop", "messages": []},
    ])
    failures = analyze_tau3_failures(p)
    assert [f.task_id for f in failures] == ["1"]


def test_infra_errors_sort_first(tmp_path: Path):
    p = _write_results(tmp_path, [
        {"task_id": "low", "reward_info": {"reward": 0.0},
         "termination_reason": "user_stop", "messages": [], "duration": 30.0},
        {"task_id": "infra", "reward_info": None,
         "termination_reason": "infrastructure_error", "messages": [],
         "duration": 0.0},
        {"task_id": "low2", "reward_info": {"reward": 0.0},
         "termination_reason": "user_stop", "messages": [], "duration": 50.0},
    ])
    failures = analyze_tau3_failures(p)
    # infra first, then descending duration among rewards=0.0
    assert [f.task_id for f in failures] == ["infra", "low2", "low"]


def test_hints_for_user_stop_with_writes(tmp_path: Path):
    msgs = [
        {"role": "user", "content": "Cancel my order"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"name": "get_order_details", "arguments": "{}"}]},
        {"role": "tool", "content": "..."},
        {"role": "assistant", "content": None,
         "tool_calls": [{"name": "cancel_pending_order", "arguments": "{}"}]},
        {"role": "tool", "content": "..."},
    ]
    p = _write_results(tmp_path, [
        {"task_id": "5", "reward_info": {"reward": 0.0},
         "termination_reason": "user_stop", "messages": msgs,
         "duration": 39.0, "agent_cost": 0.10, "user_cost": 0.05},
    ])
    failures = analyze_tau3_failures(p)
    assert len(failures) == 1
    f = failures[0]
    assert f.reward == 0.0
    assert f.termination_reason == "user_stop"
    assert "user-gave-up" in f.failure_hints
    assert "write-attempted" in f.failure_hints
    assert f.last_user_request == "Cancel my order"
    assert "cancel_pending_order" in f.text_signature


def test_hints_for_max_steps(tmp_path: Path):
    msgs = [{"role": "user", "content": "do stuff"}] + [
        {"role": "assistant", "content": "thinking..."} for _ in range(35)
    ]
    p = _write_results(tmp_path, [
        {"task_id": "7", "reward_info": {"reward": 0.0},
         "termination_reason": "max_steps", "messages": msgs,
         "duration": 200.0},
    ])
    f = analyze_tau3_failures(p)[0]
    assert "max-steps" in f.failure_hints
    assert "long-conversation" in f.failure_hints
    assert "no-tool-calls" in f.failure_hints


def test_top_k_truncation(tmp_path: Path):
    sims = [
        {"task_id": str(i), "reward_info": {"reward": 0.0},
         "termination_reason": "user_stop", "messages": [],
         "duration": float(i)}
        for i in range(5)
    ]
    p = _write_results(tmp_path, sims)
    failures = analyze_tau3_failures(p, top_k=2)
    assert len(failures) == 2


def test_missing_file_raises(tmp_path: Path):
    p = tmp_path / "nope.json"
    with pytest.raises(Tau3FailureAnalyzerError, match="not found"):
        analyze_tau3_failures(p)


def test_malformed_results_raises(tmp_path: Path):
    p = tmp_path / "broken.json"
    p.write_text("{not valid json")
    with pytest.raises(Tau3FailureAnalyzerError, match="parse"):
        analyze_tau3_failures(p)


def test_text_signature_truncated(tmp_path: Path):
    msgs = [{"role": "user", "content": "x" * 1000}]
    p = _write_results(tmp_path, [
        {"task_id": "x", "reward_info": {"reward": 0.0},
         "termination_reason": "user_stop", "messages": msgs},
    ])
    f = analyze_tau3_failures(p)[0]
    assert len(f.text_signature) <= 220
