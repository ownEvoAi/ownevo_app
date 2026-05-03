"""LocalDockerSandbox — D3 reference implementation.

Runs a snippet of Python in a hardened Docker container:

  --network none              no outbound traffic
  --read-only                 root filesystem is immutable
  --tmpfs /tmp                writable scratch space, size-capped
  --cap-drop ALL              drop every Linux capability
  --security-opt no-new-privileges
                              the container can't acquire new caps
  --memory / --memory-swap    cgroup memory limit (no swap)
  --cpus                      CPU quota
  --pids-limit                fork-bomb guard

Failure classification (see `types.SandboxResult` doc):

  * Timeout — we hit our own wall-clock budget; we `docker kill` the
    container and tag the result.
  * OOM     — `docker inspect` reports `State.OOMKilled = true` after
    exit. The kernel killed the process; the agent isn't to blame.
  * Crash   — non-zero exit code that's neither the user-exception
    sentinel (100) nor OOM. Covers SIGSEGV, signals, interpreter
    death, and the crash-remap sentinel (102 = remapped os._exit(100)).
  * (None)  — exit 0 (success) or exit 100 (runner observed child
    exit code 1, Python's default for an uncaught exception; logical
    failure the agent owns).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from .types import SandboxResult

DEFAULT_IMAGE = "python:3.11-slim"
"""Small enough (~50MB compressed) for fast pulls; matches the kernel's
target Python version."""

_USER_EXCEPTION_EXIT_CODE = 100
"""Runner exit code that means 'user code raised a Python exception'.
Distinguishes a logical failure the agent owns from interpreter death,
signals, or sandbox-runtime kills."""

_RUNNER_CRASH_REMAP_EXIT_CODE = 102
"""TODO-17 hardening: when user code's subprocess exits with our
internal user-exception sentinel (100), the runner remaps to this
value so the classifier sees a Crash rather than a user-owned logical
failure. Closes the `os._exit(100)` spoof that previously let an
agent set its own `error_class=None`."""

# Wrapper script: runs user code as a subprocess so user os._exit() /
# signals manipulate only the user-code subprocess exit code, not the
# runner's. The runner's own exit code is derived from a fixed policy
# over the child's returncode (see comments inside). Mounted read-only
# at /sandbox/runner.py. TODO-17 hardening (closes `os._exit(100)`
# spoof and the same-process attack surface; the `os._exit(0)` case
# remains observably indistinguishable from clean exit at the process
# boundary, with the run_pipeline JSON-output requirement providing
# defense-in-depth).
_RUNNER_SCRIPT = f"""\
import subprocess
import sys

proc = subprocess.run(
    [sys.executable, "/sandbox/user_code.py"],
)
rc = proc.returncode
# Policy:
#   * 0 → 0. Clean exit, sys.exit(0), or os._exit(0); classifier
#     status='ok'. We cannot distinguish os._exit(0) from clean exit
#     at the process boundary; the metric layer provides
#     defense-in-depth.
#   * 1 → user-exception sentinel. Python's default returncode for
#     an uncaught exception; classifier returns error_class=None.
#   * user-exception sentinel → crash-remap. A user attempting to
#     spoof the user-exception path; classifier returns Crash.
#   * negative (signal N) → min(255, 128+|N|). Standard signal-exit
#     convention; preserves OOM detection via inspect.State.OOMKilled
#     and Crash detection for SIGSEGV.
#   * any other → passthrough; classifier returns Crash.
if rc == 0:
    sys.exit(0)
if rc == 1:
    sys.exit({_USER_EXCEPTION_EXIT_CODE})
if rc == {_USER_EXCEPTION_EXIT_CODE}:
    sys.exit({_RUNNER_CRASH_REMAP_EXIT_CODE})
if rc < 0:
    sys.exit(min(255, 128 + (-rc)))
sys.exit(rc)
"""

_KILL_GRACE_SECONDS = 5.0
"""How long we wait for a killed container to wind down before giving up
on `proc.communicate()`."""


class LocalDockerSandbox:
    """Hardened-Docker implementation of `SandboxRuntime`.

    The class doesn't hold any per-run state; one instance can serve
    many concurrent calls (each run gets its own container name and
    temp dir).
    """

    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        cpus: float = 1.0,
        pids_limit: int = 256,
        tmpfs_size_mb: int = 64,
    ) -> None:
        self.image = image
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.tmpfs_size_mb = tmpfs_size_mb

    async def run(
        self,
        code: str,
        *,
        timeout_seconds: float,
        memory_mb: int,
    ) -> SandboxResult:
        if memory_mb <= 0:
            raise ValueError(f"memory_mb must be positive, got {memory_mb}")
        container_name = f"ownevo-sb-{uuid.uuid4().hex[:12]}"
        host_dir = Path(tempfile.mkdtemp(prefix="ownevo-sandbox-"))
        try:
            # The container drops CAP_DAC_OVERRIDE, so root inside cannot
            # bypass file permission checks. Make the bind-mount source
            # world-readable so the container's uid 0 (which doesn't match
            # the host user's uid) can still read its inputs.
            os.chmod(host_dir, 0o755)
            runner = host_dir / "runner.py"
            user = host_dir / "user_code.py"
            runner.write_text(_RUNNER_SCRIPT, encoding="utf-8")
            user.write_text(code, encoding="utf-8")
            os.chmod(runner, 0o644)
            os.chmod(user, 0o644)

            cmd = self._build_command(container_name, host_dir, memory_mb)

            start = time.monotonic()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            timed_out = False
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                timed_out = True
                await self._kill_container(container_name)
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=_KILL_GRACE_SECONDS,
                    )
                except TimeoutError:
                    stdout_b, stderr_b = b"", b""
            except asyncio.CancelledError:
                # Outer task was cancelled (e.g. run_pipeline's per-task
                # timeout fired). Kill the container before propagating so
                # it doesn't keep running until its own timeout expires.
                await self._kill_container(container_name)
                with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(
                        proc.communicate(),
                        timeout=_KILL_GRACE_SECONDS,
                    )
                raise

            duration_ms = int((time.monotonic() - start) * 1000)

            inspect = await self._inspect(container_name)
            await self._remove_container(container_name)

            return self._classify(
                stdout=stdout_b.decode(errors="replace"),
                stderr=stderr_b.decode(errors="replace"),
                duration_ms=duration_ms,
                inspect=inspect,
                timed_out=timed_out,
                timeout_seconds=timeout_seconds,
                proc_returncode=proc.returncode,
            )
        finally:
            shutil.rmtree(host_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_command(
        self,
        container_name: str,
        host_dir: Path,
        memory_mb: int,
    ) -> list[str]:
        return [
            "docker",
            "run",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            f"/tmp:size={self.tmpfs_size_mb}m,mode=1777",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            f"{memory_mb}m",
            "--memory-swap",
            f"{memory_mb}m",
            "--cpus",
            f"{self.cpus}",
            "--pids-limit",
            str(self.pids_limit),
            "--volume",
            f"{host_dir}:/sandbox:ro",
            "--workdir",
            "/sandbox",
            self.image,
            "python",
            "/sandbox/runner.py",
        ]

    async def _kill_container(self, name: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "docker", "kill", "--signal", "SIGKILL", name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    async def _remove_container(self, name: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    async def _inspect(self, name: str) -> dict:
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout_b, _ = await proc.communicate()
        if proc.returncode != 0:
            return {}
        try:
            data = json.loads(stdout_b.decode())
            return data[0] if isinstance(data, list) and data else {}
        except (json.JSONDecodeError, IndexError):
            return {}

    @staticmethod
    def _classify(
        *,
        stdout: str,
        stderr: str,
        duration_ms: int,
        inspect: dict,
        timed_out: bool,
        timeout_seconds: float,
        proc_returncode: int | None,
    ) -> SandboxResult:
        state = inspect.get("State", {}) if isinstance(inspect, dict) else {}
        oom_killed = bool(state.get("OOMKilled", False))
        # ExitCode from inspect is authoritative; fall back to the docker-cli
        # subprocess return code if inspect failed.
        exit_code = state.get("ExitCode")
        if exit_code is None:
            exit_code = proc_returncode if proc_returncode is not None else -1

        if timed_out:
            return SandboxResult(
                status="error",
                output=stdout,
                stderr=stderr,
                exit_code=exit_code,
                duration_ms=duration_ms,
                error=f"Sandbox timeout exceeded {timeout_seconds:g}s",
                error_class="Timeout",
            )
        if oom_killed:
            return SandboxResult(
                status="error",
                output=stdout,
                stderr=stderr,
                exit_code=exit_code,
                duration_ms=duration_ms,
                error="Sandbox memory limit exceeded (OOM-killed)",
                error_class="OOM",
            )
        if exit_code == 0:
            return SandboxResult(
                status="ok",
                output=stdout,
                stderr=stderr,
                exit_code=0,
                duration_ms=duration_ms,
                error=None,
                error_class=None,
            )
        if exit_code == _USER_EXCEPTION_EXIT_CODE:
            # Runner caught a Python exception from user code — logical
            # failure the agent owns, not a sandbox-runtime failure.
            return SandboxResult(
                status="error",
                output=stdout,
                stderr=stderr,
                exit_code=_USER_EXCEPTION_EXIT_CODE,
                duration_ms=duration_ms,
                error=stderr.strip() or "User code raised an exception",
                error_class=None,
            )
        return SandboxResult(
            status="error",
            output=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            error=f"Sandbox crash (exit {exit_code})",
            error_class="Crash",
        )


async def docker_available() -> bool:
    """Best-effort check that the Docker daemon is reachable. Used by tests
    to skip when Docker isn't installed or not running."""
    if shutil.which("docker") is None:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "info",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5)
        return proc.returncode == 0
    except (TimeoutError, OSError):
        return False
