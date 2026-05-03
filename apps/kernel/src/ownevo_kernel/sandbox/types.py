"""Sandbox runtime contract.

D3 — agent-generated code runs in a hardened sandbox. The MVP
implementation is local Docker (`local_docker.py`); Phase-2 swaps to e2b
or Modal stay bounded by keeping consumers behind this Protocol.

Failure semantics map directly onto `ownevo_format.ToolCallResult`:
  - status="ok"               → execution completed normally (exit 0)
  - status="error", error_class=None
                              → user code raised a Python exception
                                (logical tool-internal failure)
  - status="error", error_class="Timeout"
                              → wall-clock budget exceeded; sandbox killed
  - status="error", error_class="OOM"
                              → memory limit exceeded; kernel OOM-killed
  - status="error", error_class="Crash"
                              → segfault, signal, or other interpreter
                                death the sandbox didn't trigger

The gate runner advances `best_ever_score` only when error_class is None
(i.e., status="ok" OR a logical-error iteration the agent itself owns).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

SandboxErrorClass = Literal["Timeout", "OOM", "Crash"]
SandboxStatus = Literal["ok", "error"]


@dataclass(frozen=True)
class SandboxResult:
    """The outcome of one sandbox run.

    `output` is captured stdout; `stderr` is captured stderr (often the
    Python traceback when status="error", error_class=None).
    """

    status: SandboxStatus
    output: str
    stderr: str
    exit_code: int
    duration_ms: int
    error: str | None
    error_class: SandboxErrorClass | None

    def __post_init__(self) -> None:
        # Mirror the AgentEvent ToolCallResult invariants so a caller can
        # construct the AgentEvent directly from this result.
        if self.status == "ok":
            if self.error is not None:
                raise ValueError("SandboxResult: error must be None when status='ok'")
            if self.error_class is not None:
                raise ValueError("SandboxResult: error_class must be None when status='ok'")
        else:
            if self.error is None:
                raise ValueError("SandboxResult: error required when status='error'")


@runtime_checkable
class SandboxRuntime(Protocol):
    """Minimum surface for any sandbox provider (Docker / e2b / Modal)."""

    async def run(
        self,
        code: str,
        *,
        timeout_seconds: float,
        memory_mb: int,
    ) -> SandboxResult: ...
