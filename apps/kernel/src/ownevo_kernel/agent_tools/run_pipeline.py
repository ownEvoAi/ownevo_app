"""run_pipeline — execute a skill version in the sandbox against an input.

The agent's primary action verb. Wraps `LocalDockerSandbox.run` with:
  * a structured input contract — `input_data` is exposed to the skill
    as a Python global, populated from a JSON string injected via a
    short prologue. No file I/O needed; the sandbox bind-mount is
    read-only so on-container writes would fail anyway.
  * structured output parsing — the skill writes one JSON object on the
    last line of stdout; `run_pipeline` parses it into `outputs`. Non-
    parseable stdout preserves as `raw_stdout` for the agent to inspect.
  * a per-task timeout layer above the sandbox per-call timeout — bounds
    the whole call (setup + sandbox run + parse) so a stalled caller
    side doesn't keep the iteration alive past its budget.

I/O contract for skill authors:

    # `input_data` is a dict, available as a global. Read it directly.
    answer = process(input_data["sku"], input_data["store"])
    print(json.dumps({"forecast": answer}))   # last stdout line = result

The gate consumes `PipelineResult.outputs` to compute val_score.
`error_class` mirrors the sandbox classification — gate runner does NOT
advance `best_ever_score` when it's non-null (D3).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from ..sandbox import LocalDockerSandbox, SandboxErrorClass, SandboxResult


@dataclass(frozen=True)
class PipelineResult:
    """Result of one `run_pipeline` call.

    `outputs` is the JSON-decoded last line of stdout when parseable;
    else None and `raw_stdout` carries the bytes the skill produced.
    """

    status: str  # "ok" | "error"
    outputs: dict[str, Any] | None
    raw_stdout: str
    raw_stderr: str
    duration_ms: int
    error: str | None
    error_class: SandboxErrorClass | None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


async def run_pipeline(
    sandbox: LocalDockerSandbox,
    *,
    skill_content: str,
    input_data: dict[str, Any] | None = None,
    timeout_seconds: float = 60.0,
    memory_mb: int = 512,
    task_timeout_seconds: float | None = None,
) -> PipelineResult:
    """Execute `skill_content` in the sandbox with `input_data` injected
    as a Python global. Returns parsed stdout as `outputs`.

    `task_timeout_seconds`, when set, bounds the whole call. Defaults to
    `timeout_seconds + 30.0` so caller-side stalls (e.g., docker exec
    daemon delays) don't keep the iteration alive indefinitely.
    """
    try:
        payload = json.dumps(input_data if input_data is not None else {})
    except (TypeError, ValueError) as exc:
        return PipelineResult(
            status="error",
            outputs=None,
            raw_stdout="",
            raw_stderr="",
            duration_ms=0,
            error=f"input_data is not JSON-serializable: {exc}",
            error_class=None,
        )
    prologue = (
        "import json as _ownevo_json\n"
        f"input_data = _ownevo_json.loads({payload!r})\n"
    )
    code = prologue + skill_content
    task_timeout = task_timeout_seconds or (timeout_seconds + 30.0)

    try:
        result = await asyncio.wait_for(
            sandbox.run(
                code,
                timeout_seconds=timeout_seconds,
                memory_mb=memory_mb,
            ),
            timeout=task_timeout,
        )
    except TimeoutError:
        return PipelineResult(
            status="error",
            outputs=None,
            raw_stdout="",
            raw_stderr="",
            duration_ms=int(task_timeout * 1000),
            error=(
                f"Task timeout exceeded {task_timeout:g}s "
                "(per-task limit, distinct from sandbox per-call timeout)"
            ),
            error_class="Timeout",
        )

    return _build_result(result)


def _build_result(sb: SandboxResult) -> PipelineResult:
    outputs: dict[str, Any] | None = None
    if sb.status == "ok" and sb.output:
        # Skills emit one JSON object on the last stdout line. We don't
        # parse the whole stream — debug prints earlier in stdout don't
        # need to be JSON-clean.
        try:
            last_line = sb.output.rstrip().splitlines()[-1]
            parsed = json.loads(last_line)
            if isinstance(parsed, dict):
                outputs = parsed
        except (json.JSONDecodeError, IndexError):
            outputs = None
    return PipelineResult(
        status=sb.status,
        outputs=outputs,
        raw_stdout=sb.output,
        raw_stderr=sb.stderr,
        duration_ms=sb.duration_ms,
        error=sb.error,
        error_class=sb.error_class,
    )
