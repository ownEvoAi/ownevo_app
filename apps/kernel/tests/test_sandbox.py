"""LocalDockerSandbox integration tests — D3 failure-class mapping.

These run real containers via the host's Docker daemon. Skipped when
Docker isn't reachable so unit-only CI stays green.

Each test asserts the SandboxResult shape that maps onto the
AgentEvent ToolCallResult contract:

  status="ok"                               → exit 0
  status="error", error_class=None          → exit 100 (logical)
  status="error", error_class="Timeout"     → wall-clock kill
  status="error", error_class="OOM"         → cgroup OOM-kill
  status="error", error_class="Crash"       → SIGSEGV / signal / other
"""

from __future__ import annotations

import asyncio

import pytest
from ownevo_kernel.sandbox import LocalDockerSandbox, SandboxResult, docker_available


def _docker_ok() -> bool:
    return asyncio.run(docker_available())


pytestmark = pytest.mark.skipif(
    not _docker_ok(),
    reason="Docker daemon not reachable; skipping sandbox integration tests",
)


@pytest.fixture
def sandbox() -> LocalDockerSandbox:
    return LocalDockerSandbox()


async def test_happy_path_captures_stdout(sandbox: LocalDockerSandbox):
    result = await sandbox.run(
        "print('hello from sandbox')",
        timeout_seconds=15,
        memory_mb=128,
    )
    assert isinstance(result, SandboxResult)
    assert result.status == "ok"
    assert result.error is None
    assert result.error_class is None
    assert result.exit_code == 0
    assert "hello from sandbox" in result.output


async def test_logical_python_exception_is_not_a_sandbox_error(sandbox: LocalDockerSandbox):
    """User code raising an exception → status='error', error_class=None.
    The gate runner WILL count this iteration; the agent owns the failure."""
    result = await sandbox.run(
        "raise ValueError('intentional')",
        timeout_seconds=15,
        memory_mb=128,
    )
    assert result.status == "error"
    assert result.error_class is None
    assert result.exit_code == 100
    assert "ValueError" in result.stderr
    assert "intentional" in result.stderr


async def test_timeout_classifies_as_Timeout(sandbox: LocalDockerSandbox):
    """Wall-clock budget exceeded → error_class='Timeout'.
    Gate runner does NOT advance best_ever_score on this."""
    result = await sandbox.run(
        "import time; time.sleep(30)",
        timeout_seconds=2,
        memory_mb=128,
    )
    assert result.status == "error"
    assert result.error_class == "Timeout"
    assert "timeout" in (result.error or "").lower()


async def test_oom_classifies_as_OOM(sandbox: LocalDockerSandbox):
    """Memory limit exceeded → error_class='OOM'.
    Gate runner does NOT advance best_ever_score."""
    # Allocate ~256MB inside a container limited to 64MB. The cgroup
    # OOM-killer should fire before the bytearray finishes allocating.
    result = await sandbox.run(
        "x = bytearray(256 * 1024 * 1024); print(len(x))",
        timeout_seconds=15,
        memory_mb=64,
    )
    assert result.status == "error"
    assert result.error_class == "OOM", (
        f"Expected OOM, got {result.error_class!r}; stderr={result.stderr!r}"
    )


async def test_segfault_classifies_as_Crash(sandbox: LocalDockerSandbox):
    """SIGSEGV from a deref of NULL → error_class='Crash'.
    The interpreter died unexpectedly, not on a clean Python path."""
    result = await sandbox.run(
        "import ctypes; ctypes.string_at(0)",
        timeout_seconds=15,
        memory_mb=128,
    )
    assert result.status == "error"
    assert result.error_class == "Crash"


async def test_sandbox_has_no_network(sandbox: LocalDockerSandbox):
    """--network=none means even DNS-free socket calls should fail.
    Establishes the network-isolation contract for D3."""
    code = (
        "import socket\n"
        "try:\n"
        "    s = socket.socket()\n"
        "    s.settimeout(2)\n"
        "    s.connect(('1.1.1.1', 80))\n"
        "    print('UNEXPECTED CONNECT')\n"
        "except OSError as e:\n"
        "    print(f'blocked: {type(e).__name__}')\n"
    )
    result = await sandbox.run(code, timeout_seconds=15, memory_mb=128)
    assert result.status == "ok"
    assert "UNEXPECTED CONNECT" not in result.output
    assert "blocked:" in result.output


async def test_sandbox_rootfs_is_read_only(sandbox: LocalDockerSandbox):
    """--read-only blocks writes to /; /tmp tmpfs remains writable."""
    code = (
        "try:\n"
        "    open('/etc/foo', 'w').write('x')\n"
        "    print('UNEXPECTED WRITE')\n"
        "except OSError as e:\n"
        "    print(f'blocked: {type(e).__name__}')\n"
        "open('/tmp/ok', 'w').write('y')\n"
        "print('tmp ok')\n"
    )
    result = await sandbox.run(code, timeout_seconds=15, memory_mb=128)
    assert result.status == "ok"
    assert "UNEXPECTED WRITE" not in result.output
    assert "blocked:" in result.output
    assert "tmp ok" in result.output


def test_sandbox_result_invariants_match_tool_call_result():
    """Defensive: SandboxResult mirrors the AgentEvent.ToolCallResult
    error-field invariants so a caller can pass them straight through."""
    with pytest.raises(ValueError, match="error must be None when status='ok'"):
        SandboxResult(
            status="ok",
            output="",
            stderr="",
            exit_code=0,
            duration_ms=1,
            error="should not be set",
            error_class=None,
        )
    with pytest.raises(ValueError, match="error_class must be None when status='ok'"):
        SandboxResult(
            status="ok",
            output="",
            stderr="",
            exit_code=0,
            duration_ms=1,
            error=None,
            error_class="Timeout",
        )
    with pytest.raises(ValueError, match="error required when status='error'"):
        SandboxResult(
            status="error",
            output="",
            stderr="",
            exit_code=1,
            duration_ms=1,
            error=None,
            error_class=None,
        )
