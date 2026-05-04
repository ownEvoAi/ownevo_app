"""run_pipeline integration tests — Docker-required.

Exercises the I/O contract (input_data global, JSON-on-stdout output
parsing) and the per-task timeout layer that sits above the sandbox
per-call timeout.
"""

from __future__ import annotations

import asyncio

import pytest
from ownevo_kernel.agent_tools import PipelineResult, run_pipeline
from ownevo_kernel.sandbox import LocalDockerSandbox, docker_available


def _docker_ok() -> bool:
    return asyncio.run(docker_available())


pytestmark = pytest.mark.skipif(
    not _docker_ok(),
    reason="Docker daemon not reachable",
)


@pytest.fixture
def sandbox() -> LocalDockerSandbox:
    return LocalDockerSandbox()


# ---------------------------------------------------------------------------
# I/O contract
# ---------------------------------------------------------------------------


async def test_input_data_is_available_as_global(sandbox: LocalDockerSandbox):
    """Skills read `input_data` directly — no file I/O needed."""
    skill = (
        "import json\n"
        "result = input_data['x'] + input_data['y']\n"
        "print(json.dumps({'sum': result}))\n"
    )
    r = await run_pipeline(
        sandbox,
        skill_content=skill,
        input_data={"x": 5, "y": 7},
        timeout_seconds=15,
        memory_mb=128,
    )
    assert r.ok
    assert r.outputs == {"sum": 12}


async def test_output_parses_last_json_line(sandbox: LocalDockerSandbox):
    """Skills can print debug noise earlier in stdout; only the last
    line needs to be JSON."""
    skill = (
        "import json\n"
        "print('debug: starting')\n"
        "print('debug: midway')\n"
        "print(json.dumps({'final': True}))\n"
    )
    r = await run_pipeline(
        sandbox,
        skill_content=skill,
        timeout_seconds=15,
        memory_mb=128,
    )
    assert r.ok
    assert r.outputs == {"final": True}
    assert "debug: starting" in r.raw_stdout


async def test_non_json_output_keeps_raw_stdout(sandbox: LocalDockerSandbox):
    """If the skill doesn't emit JSON, outputs is None but raw_stdout
    is preserved so the agent can see what happened."""
    skill = "print('hello world')\n"
    r = await run_pipeline(
        sandbox,
        skill_content=skill,
        timeout_seconds=15,
        memory_mb=128,
    )
    assert r.ok
    assert r.outputs is None
    assert "hello world" in r.raw_stdout


async def test_skill_with_no_input(sandbox: LocalDockerSandbox):
    """input_data defaults to {} so a skill that doesn't need it
    still works."""
    skill = "import json; print(json.dumps({'has_input': bool(input_data)}))\n"
    r = await run_pipeline(
        sandbox,
        skill_content=skill,
        timeout_seconds=15,
        memory_mb=128,
    )
    assert r.ok
    assert r.outputs == {"has_input": False}


# ---------------------------------------------------------------------------
# Failure paths — D3 mapping carried through
# ---------------------------------------------------------------------------


async def test_skill_exception_surfaces_as_logical_error(sandbox: LocalDockerSandbox):
    """User-code exception → status=error, error_class=None (the agent
    owns the failure; gate counts it). Same semantics as raw sandbox."""
    skill = "raise ValueError('boom')\n"
    r = await run_pipeline(
        sandbox,
        skill_content=skill,
        timeout_seconds=15,
        memory_mb=128,
    )
    assert r.status == "error"
    assert r.error_class is None
    assert "ValueError" in r.raw_stderr


async def test_sandbox_timeout_surfaces_with_timeout_class(sandbox: LocalDockerSandbox):
    """Sandbox per-call timeout → error_class='Timeout'."""
    skill = "import time; time.sleep(30)\n"
    r = await run_pipeline(
        sandbox,
        skill_content=skill,
        timeout_seconds=2,
        memory_mb=128,
    )
    assert r.status == "error"
    assert r.error_class == "Timeout"


# ---------------------------------------------------------------------------
# Per-task timeout layer
# ---------------------------------------------------------------------------


async def test_per_task_timeout_fires_independently():
    """Per-task timeout is the upper bound on the whole call, distinct
    from sandbox per-call timeout. We simulate a slow sandbox by passing
    a fake that just sleeps; per-task timeout must fire."""

    class _SlowSandbox:
        async def run(self, code, *, timeout_seconds, memory_mb, extra_volumes=None):
            del extra_volumes
            await asyncio.sleep(timeout_seconds + 5)
            raise AssertionError("should have been cancelled by per-task timeout")

    r = await run_pipeline(
        _SlowSandbox(),
        skill_content="pass",
        timeout_seconds=10,
        task_timeout_seconds=1,  # bound the whole call
        memory_mb=128,
    )
    assert r.status == "error"
    assert r.error_class == "Timeout"
    assert "per-task" in (r.error or "")


async def test_pipeline_result_is_frozen_dataclass():
    """Defensive: the result type is immutable so a downstream caller
    can't mutate one trace's outputs and corrupt another. frozen=True
    raises dataclasses.FrozenInstanceError on field assignment."""
    import dataclasses

    r = PipelineResult(
        status="ok",
        outputs={"k": 1},
        raw_stdout="",
        raw_stderr="",
        duration_ms=1,
        error=None,
        error_class=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.status = "error"  # type: ignore[misc]
