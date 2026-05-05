"""A3.3 — generated sim runs in the W1.3 substrate sandbox.

Validation gate per `docs/PLAN.md` line 231:

> The generated `sim.py` executes in the substrate sandbox (W1.3) without
> modification. Generated sim from A3.2 runs in the sandbox; produces
> deterministic output.

The rendered sim is stdlib-only (random, math, statistics, datetime, json)
so it runs unmodified in the default `LocalDockerSandbox` image
(pinned `python:3.11-slim` digest) — no domain-specific Dockerfile required, the same
property exercised by the W2.7 non-M5 substrate proof.

Skipped automatically when Docker isn't reachable, so unit-only CI stays
green. The M5 reproducibility nightly + the dedicated DB+Docker job pick
this file up alongside `test_sandbox.py` and `test_substrate_non_m5.py`.

Three contracts asserted:

  1. **Runs end-to-end** — render → `run_pipeline` → status="ok", outputs
     parsed JSON, no error_class. ≥1 fixture clears the gate (we ship all 3).
  2. **Sandbox replay-equivalence** — two sandbox runs at the same seed
     produce bit-identical outputs. Stronger than the in-process replay
     in `test_nl_gen_sim_replay.py` because it pins the property end-to-end
     across container boundaries (subprocess RNG state, JSON serialization,
     stdout capture).
  3. **In-process / sandbox parity** — sandbox output equals the
     in-process exec output. Catches a class of bug where determinism
     holds within each path but the two paths diverge (e.g., `random`
     module differing across the runner / host Python).
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
from ownevo_kernel.agent_tools.run_pipeline import run_pipeline
from ownevo_kernel.nl_gen import SCHEMA_VERSION, render_simulation_module
from ownevo_kernel.nl_gen.fixtures import (
    CONTRACT_REVIEW_SIM_PLAN,
    CONTRACT_REVIEW_SPEC,
    CREDIT_RISK_SIM_PLAN,
    CREDIT_RISK_SPEC,
    DEMAND_PREDICTION_SIM_PLAN,
    DEMAND_PREDICTION_SPEC,
)
from ownevo_kernel.sandbox import LocalDockerSandbox, docker_available
from ownevo_kernel.skills.format import parse_skill


_SANDBOX_TIMEOUT_S = 30.0
_SANDBOX_MEMORY_MB = 256


def _docker_ok() -> bool:
    # Synchronous check — avoids asyncio.run() at collection time, which raises
    # RuntimeError if an event loop is already running (pytest-xdist workers,
    # some CI setups).
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(
    not _docker_ok(),
    reason="Docker daemon not reachable; skipping A3.3 sim-in-sandbox tests",
)


_FIXTURE_PAIRS = [
    ("demand-prediction", DEMAND_PREDICTION_SIM_PLAN, DEMAND_PREDICTION_SPEC),
    ("credit-risk", CREDIT_RISK_SIM_PLAN, CREDIT_RISK_SPEC),
    ("contract-review", CONTRACT_REVIEW_SIM_PLAN, CONTRACT_REVIEW_SPEC),
]


@pytest.fixture
def sandbox() -> LocalDockerSandbox:
    """Pinned python:3.11-slim image — the sim is stdlib-only."""
    return LocalDockerSandbox()


def _exec_in_process(plan, spec, *, seed: int, n_steps: int) -> dict[str, Any]:
    """Render + exec the skill body in-process; return the trajectory dict.

    Matches `test_nl_gen_sim_replay.py`'s helper so the parity assertion
    here is comparing the same code path that suite already pins.
    """
    content = render_simulation_module(plan, spec)
    record = parse_skill(content)
    namespace: dict[str, Any] = {"__name__": "_sim_under_test"}
    exec(compile(record.body, f"<sim:{spec.id}>", "exec"), namespace)
    return namespace["run_simulation"](seed=seed, n_steps=n_steps)


async def _run_in_sandbox(
    sandbox: LocalDockerSandbox,
    plan,
    spec,
    *,
    seed: int,
    n_steps: int,
) -> dict[str, Any]:
    """Render + run the skill in the sandbox via `run_pipeline`."""
    skill_content = render_simulation_module(plan, spec)
    result = await run_pipeline(
        sandbox,
        skill_content=skill_content,
        input_data={"seed": seed, "n_steps": n_steps},
        timeout_seconds=_SANDBOX_TIMEOUT_S,
        memory_mb=_SANDBOX_MEMORY_MB,
    )
    assert result.ok, (
        f"sandbox run failed: error={result.error!r} "
        f"error_class={result.error_class!r} "
        f"stderr={result.raw_stderr[-500:]!r}"
    )
    assert result.error_class is None
    assert result.outputs is not None, (
        f"sandbox produced no parseable JSON output. raw_stdout tail: "
        f"{result.raw_stdout[-500:]!r}"
    )
    return result.outputs


# ---------------------------------------------------------------------------
# Contract 1: runs end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("fixture_id", "plan", "spec"), _FIXTURE_PAIRS)
async def test_runs_end_to_end_in_sandbox(
    sandbox: LocalDockerSandbox, fixture_id, plan, spec
):
    n_steps = 10
    outputs = await _run_in_sandbox(
        sandbox, plan, spec, seed=plan.seed_default, n_steps=n_steps
    )
    assert outputs["workflow_spec_id"] == spec.id
    assert outputs["schema_version"] == SCHEMA_VERSION
    assert outputs["seed"] == plan.seed_default
    assert outputs["n_steps"] == n_steps
    assert isinstance(outputs["trajectory"], list)
    assert len(outputs["trajectory"]) == n_steps

    expected_keys = {f.name for f in plan.event_fields}
    for event in outputs["trajectory"]:
        assert expected_keys <= set(event.keys())


# ---------------------------------------------------------------------------
# Contract 2: replay-equivalence inside the sandbox
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("fixture_id", "plan", "spec"), _FIXTURE_PAIRS)
async def test_sandbox_replay_equivalence(
    sandbox: LocalDockerSandbox, fixture_id, plan, spec
):
    a = await _run_in_sandbox(sandbox, plan, spec, seed=42, n_steps=20)
    b = await _run_in_sandbox(sandbox, plan, spec, seed=42, n_steps=20)
    assert a == b
    # Byte-identical canonical JSON — the same property the M5 reproducibility
    # nightly enforces. If this drifts we lose the audit-trail claim.
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


@pytest.mark.parametrize(("fixture_id", "plan", "spec"), _FIXTURE_PAIRS)
async def test_sandbox_different_seeds_diverge(
    sandbox: LocalDockerSandbox, fixture_id, plan, spec
):
    """Sanity: a sim that ignores the seed would silently pass replay tests.
    Two different seeds must produce different trajectories."""
    a = await _run_in_sandbox(sandbox, plan, spec, seed=42, n_steps=15)
    b = await _run_in_sandbox(sandbox, plan, spec, seed=43, n_steps=15)
    assert a["trajectory"] != b["trajectory"]


# ---------------------------------------------------------------------------
# Contract 3: in-process / sandbox parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("fixture_id", "plan", "spec"), _FIXTURE_PAIRS)
async def test_sandbox_matches_in_process_exec(
    sandbox: LocalDockerSandbox, fixture_id, plan, spec
):
    """Sandboxed trajectory equals the in-process exec trajectory.

    `random.Random(seed)` is platform-stable across CPython versions, so
    a divergence here means the renderer or `run_pipeline` introduced
    something non-deterministic between the two paths.
    """
    seed, n_steps = plan.seed_default, 12
    sandbox_out = await _run_in_sandbox(
        sandbox, plan, spec, seed=seed, n_steps=n_steps
    )
    in_process_out = _exec_in_process(plan, spec, seed=seed, n_steps=n_steps)
    assert sandbox_out == in_process_out


# ---------------------------------------------------------------------------
# Default-input handling (no seed / n_steps in input_data)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("fixture_id", "plan", "spec"), _FIXTURE_PAIRS)
async def test_sandbox_uses_plan_defaults_when_input_unset(
    sandbox: LocalDockerSandbox, fixture_id, plan, spec
):
    """`run_pipeline` injects `input_data = {}` when none provided —
    the entrypoint guard reads `SEED_DEFAULT` / `N_STEPS_DEFAULT`."""
    skill_content = render_simulation_module(plan, spec)
    result = await run_pipeline(
        sandbox,
        skill_content=skill_content,
        input_data=None,
        timeout_seconds=_SANDBOX_TIMEOUT_S,
        memory_mb=_SANDBOX_MEMORY_MB,
    )
    assert result.ok, f"unexpected error: {result.error!r}"
    assert result.outputs is not None
    assert result.outputs["seed"] == plan.seed_default
    assert result.outputs["n_steps"] == plan.n_steps_default
