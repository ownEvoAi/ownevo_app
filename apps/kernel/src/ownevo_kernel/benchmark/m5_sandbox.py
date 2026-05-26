"""SandboxedM5BenchmarkRunner — runs the M5 baseline through LocalDockerSandbox (W2.6 #11c).

The in-process counterpart (`m5.py`) imports `baselines.m5_lightgbm` and
runs the orchestrator directly. This class does the same work but
through a hardened Docker container — same skill bodies, same fold,
same scoring, just isolated inside the sandbox image
(`apps/kernel/sandbox/Dockerfile.m5`).

Why a separate runner instead of a flag on `M5BenchmarkRunner`
--------------------------------------------------------------
The two paths take materially different inputs: the in-process runner
takes a Python `M5Catalog` object (path-aware filesystem handles); the
sandboxed runner takes a host-directory path that gets bind-mounted
into the container. Constructing both shapes off one class would push
the "two universes" disjunction into every method. Two classes; one
shared `M5RunArtifacts` aggregation helper. Both implement the
`BenchmarkRunner` Protocol.

Marshaling contract
-------------------
The orchestrator script runs inside the sandbox and prints one JSON
object as the last line of stdout. Schema:

    {
        "predictions": list[list[float]],   # (n_series, n_test_days)
        "actuals":     list[list[float]],
        "series_ids":  list[str],
        "weights":     list[float],
        "scales":      list[float],
    }

All four arrays are aligned on `series_ids` row-order (same invariant
as `M5PipelineOutput`). The runner re-validates shapes after parsing
and surfaces the same scoring artifacts as the in-process path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from ..datasets.m5_metric import M5Fold, rmse, wrmsse
from ..sandbox import LocalDockerSandbox
from .m5 import M5PipelineOutput, M5RunArtifacts, _compute_rewards, _validate_pipeline_output
from .types import BenchmarkResult

if TYPE_CHECKING:
    # Importing the agent_tools package eagerly pulls in `metrics.py`,
    # which imports asyncpg. The M5 sandbox image deliberately omits
    # asyncpg (no DB connections — `--network=none`), so this stays
    # type-only. The `run_pipeline` callable is imported lazily inside
    # `run()` below.
    from ..agent_tools.run_pipeline import PipelineResult

# Container path the catalog directory bind-mounts to. Outside `/sandbox`
# (reserved for runner.py + user_code.py); the entrypoint script reads
# this path from `input_data["catalog_dir"]`, so it can move without
# breaking the wire contract.
_CATALOG_MOUNT = "/data/m5"

# Container path the M5 skill files live at inside the sandbox image.
# Mirrors the layout produced by `apps/kernel/sandbox/Dockerfile.m5`
# (which copies `apps/kernel/baselines` to `/opt/ownevo/apps/kernel/baselines`).
# A `skill_override_dir` bind-mount lands here read-only and shadows the
# baked-in v1 skill bodies, so the gate can score whatever the agent
# proposed instead of the on-disk baseline ().
_SKILL_V1_MOUNT = "/opt/ownevo/apps/kernel/baselines/m5_lightgbm/skill_v1"

_ENTRYPOINT_SCRIPT = '''\
import json
import sys
from pathlib import Path

from ownevo_kernel.datasets import load_m5
from ownevo_kernel.datasets.m5_metric import M5Fold
from baselines.m5_lightgbm import run_baseline

cat = load_m5(Path(input_data["catalog_dir"]))
fold = M5Fold(
    train=tuple(input_data["fold"]["train"]),
    validation=tuple(input_data["fold"]["validation"]),
    test=tuple(input_data["fold"]["test"]),
)
series_ids = input_data.get("series_ids")

out = run_baseline(cat, fold, series_ids=series_ids)

# Last stdout line = parseable JSON. run_pipeline reads only the final
# line; printing anything else here would shift parsing target.
sys.stdout.write(json.dumps({
    "predictions": out.predictions.tolist(),
    "actuals":     out.actuals.tolist(),
    "series_ids":  list(out.series_ids),
    "weights":     out.weights.tolist(),
    "scales":      out.scales.tolist(),
}))
'''


class M5SandboxError(RuntimeError):
    """The sandboxed pipeline failed before producing a parseable result.

    Distinct from a sandbox `error_class` (Timeout / OOM / Crash) — those
    are surfaced by the gate runner's SANDBOX_ERROR short-circuit. This
    one means the run completed but the caller cannot reconstruct an
    `M5PipelineOutput` from its stdout (missing keys, wrong shapes,
    non-finite values, etc.).
    """


@dataclass
class SandboxedM5BenchmarkRunner:
    """`BenchmarkRunner` implementation that drives the M5 baseline in Docker.

    Attributes:
        catalog_dir: Host path containing the M5 CSVs. Bind-mounted
            read-only at `/data/m5` inside the container. **The path
            and its files must be world-readable** (chmod o+rx the
            dir, o+r the CSVs) — the sandbox container drops
            CAP_DAC_OVERRIDE, so its uid 0 cannot bypass DAC. Local
            dev with `chmod -R a+rX data/m5` is enough.
        fold: Train/val/test column split. JSON-marshaled to the entrypoint.
        sandbox: A `LocalDockerSandbox` constructed with the M5 image
            (e.g., `LocalDockerSandbox(image="ownevo-sandbox-m5:0.1.0",
            tmpfs_size_mb=512)`). The runner does not configure the
            sandbox itself — caller chooses image + resource limits.
        timeout_seconds: Per-call wall-clock budget passed to the
            sandbox. Defaults to 10 min — generous so even a real-M5
            run on cold image fits.
        memory_mb: Per-call cgroup memory cap. Defaults to 4 GiB so a
            full-catalog LightGBM fit doesn't OOM on first try.
        skill_override_dir: Optional host directory bind-mounted over
            the baked-in `skill_v1/` package inside the container. Must
            contain `__init__.py` plus the 6 baseline skill modules so
            the orchestrator's `from .skill_v1 import ...` resolves.
            When set, the container imports the override instead of the
            image's v1 bodies — this is how the loop scores the agent's
            proposed skill content (B4.1). When `None`, the runner falls
            back to whatever shipped in the image.
    """

    catalog_dir: Path
    fold: M5Fold
    sandbox: LocalDockerSandbox
    timeout_seconds: float = 600.0
    memory_mb: int = 4096
    skill_override_dir: Path | None = None
    last_artifacts: M5RunArtifacts | None = field(default=None, init=False, repr=False)
    last_pipeline_result: PipelineResult | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        catalog = Path(self.catalog_dir).resolve()
        if not catalog.is_dir():
            raise ValueError(
                f"catalog_dir must be an existing directory; got {self.catalog_dir!r}"
            )
        self.catalog_dir = catalog
        if self.skill_override_dir is not None:
            override = Path(self.skill_override_dir).resolve()
            if not override.is_dir():
                raise ValueError(
                    f"skill_override_dir must be an existing directory; "
                    f"got {self.skill_override_dir!r}"
                )
            self.skill_override_dir = override

    async def run(
        self,
        task_ids: list[str] | None = None,
    ) -> BenchmarkResult:
        from ..agent_tools.run_pipeline import run_pipeline

        input_data: dict[str, Any] = {
            "catalog_dir": _CATALOG_MOUNT,
            "fold": {
                "train": list(self.fold.train),
                "validation": list(self.fold.validation),
                "test": list(self.fold.test),
            },
        }
        if task_ids is not None:
            input_data["series_ids"] = list(task_ids)

        extra_volumes: dict[str, str] = {str(self.catalog_dir): _CATALOG_MOUNT}
        if self.skill_override_dir is not None:
            extra_volumes[str(self.skill_override_dir)] = _SKILL_V1_MOUNT

        result = await run_pipeline(
            self.sandbox,
            skill_content=_ENTRYPOINT_SCRIPT,
            input_data=input_data,
            timeout_seconds=self.timeout_seconds,
            memory_mb=self.memory_mb,
            extra_volumes=extra_volumes,
        )
        self.last_pipeline_result = result

        if not result.ok:
            raise M5SandboxError(
                f"Sandboxed M5 pipeline did not return ok: status={result.status}, "
                f"error_class={result.error_class}, error={result.error!r}, "
                f"stderr={(result.raw_stderr or '')[-500:]!r}",
            )

        out = _parse_pipeline_output(result.outputs, result.raw_stdout)
        _validate_pipeline_output(out)

        rewards = _compute_rewards(
            out.predictions, out.actuals, out.scales, out.series_ids,
        )
        agg_wrmsse = wrmsse(
            out.predictions, out.actuals,
            weights=out.weights, scales=out.scales,
        )
        agg_rmse = rmse(out.predictions, out.actuals)

        self.last_artifacts = M5RunArtifacts(
            predictions=out.predictions,
            actuals=out.actuals,
            series_ids=tuple(out.series_ids),
            weights=out.weights,
            scales=out.scales,
            rmse=agg_rmse,
            wrmsse=agg_wrmsse,
            rewards=dict(rewards),
        )
        return BenchmarkResult(rewards={k: v for k, v in rewards.items()})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_REQUIRED_KEYS = ("predictions", "actuals", "series_ids", "weights", "scales")


def _parse_pipeline_output(
    outputs: dict[str, Any] | None,
    raw_stdout: str,
) -> M5PipelineOutput:
    """Reconstruct `M5PipelineOutput` from the parsed JSON the entrypoint
    printed. `raw_stdout` is included in error messages so a malformed
    response is debuggable without re-running the pipeline."""
    if outputs is None:
        tail = raw_stdout.rstrip().splitlines()[-1] if raw_stdout.strip() else "<empty>"
        raise M5SandboxError(
            f"Sandboxed pipeline did not emit a JSON object on the last stdout line. "
            f"Last line: {tail[:500]!r}"
        )

    missing = [k for k in _REQUIRED_KEYS if k not in outputs]
    if missing:
        raise M5SandboxError(
            f"Sandboxed pipeline JSON missing keys: {missing!r}. "
            f"Got keys: {sorted(outputs.keys())!r}"
        )

    try:
        predictions = np.asarray(outputs["predictions"], dtype=np.float64)
        actuals = np.asarray(outputs["actuals"], dtype=np.float64)
        weights = np.asarray(outputs["weights"], dtype=np.float64)
        scales = np.asarray(outputs["scales"], dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise M5SandboxError(
            f"Sandboxed pipeline JSON arrays failed to coerce to float64: {exc}"
        ) from exc

    series_ids = outputs["series_ids"]
    if not isinstance(series_ids, list) or not all(isinstance(s, str) for s in series_ids):
        raise M5SandboxError(
            f"Sandboxed pipeline series_ids must be list[str]; got {type(series_ids).__name__}"
        )

    return M5PipelineOutput(
        predictions=predictions,
        actuals=actuals,
        series_ids=series_ids,
        weights=weights,
        scales=scales,
    )
