"""SandboxedTauBenchRunner — runs Sierra tau-bench through LocalDockerSandbox.

Architectural mirror of ``SandboxedM5BenchmarkRunner``: an entrypoint
script runs inside the τ³ sandbox image, prints a JSON result on the
last stdout line, and the kernel-side runner parses it and returns a
``BenchmarkResult`` consumable by the existing gate flow. No tau2 import
on the kernel side — all tau2 calls happen inside the sandbox where the
image has Python 3.12 + the pinned tau2 git rev.

Marshaling contract
-------------------
The entrypoint prints one JSON object as the LAST stdout line. Schema::

    {
        "rewards": {"<task_id>": <reward in [0,1] or null>, ...},
        "n_simulations": <int>,
        "n_evaluated": <int>,    // tasks that produced a verifier result
        "infra_errors": <int>,   // sims that died before evaluation
        "raw_run_dir": <str>,    // tau2's auto-saved run dir under /tau2_data/simulations/
    }

`raw_run_dir` is preserved so the failure analyzer (P1.5 / M7) can read
the full per-conversation traces — Meta-Harness ablation (34.6 → 50.0)
makes preserving full traces non-negotiable.

Skill override
--------------
``skill_override_dir`` is bind-mounted at ``/skill_override`` (read-only).
The entrypoint imports ``agent.py`` from there as ``HarnessAgent`` and
registers it via ``tau2.registry.register_agent_factory``. Without an
override, the entrypoint falls back to the baked-in baseline skill
(see baselines/tau3_retail_v1/, M4).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...sandbox import LocalDockerSandbox
from ..types import BenchmarkResult

if TYPE_CHECKING:
    from ...agent_tools.run_pipeline import PipelineResult

logger = logging.getLogger(__name__)

# Container path the skill override directory bind-mounts to. Outside
# `/sandbox` (reserved for runner.py + user_code.py); the entrypoint
# script reads from this path. A skill_override_dir bind-mount lands
# here read-only and shadows whatever the image's baked-in skill was.
_SKILL_OVERRIDE_MOUNT = "/skill_override"

# tau2 reads TAU2_DATA_DIR at module import time and the image bakes
# the dataset at /tau2_data. The runner doesn't override this — same
# data source for every run.
_TAU2_DATA_DIR = "/tau2_data"


_ENTRYPOINT_SCRIPT = '''\
import importlib
import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path

# input_data is injected by run_pipeline as a Python global.
# Schema (set by SandboxedTauBenchRunner.run):
#   domain: str ("retail" | "airline" | "telecom" | ...)
#   split:  str ("train" | "test")
#   task_ids: list[str] | None  (None = full split)
#   max_concurrency: int
#   skill_override_path: str | None  (path to agent.py inside container)

domain = input_data["domain"]
split = input_data["split"]
task_ids = input_data.get("task_ids")
max_concurrency = int(input_data.get("max_concurrency", 3))
skill_override_path = input_data.get("skill_override_path")

# Load the HarnessAgent class. Skill override has priority; without one
# we fall back to the image-baked-in baseline (path TBD when M4 lands).
HarnessAgent = None
if skill_override_path:
    spec = importlib.util.spec_from_file_location(
        "ownevo_tau3_skill_override", skill_override_path,
    )
    if spec is None or spec.loader is None:
        sys.stderr.write(
            f"failed to load skill override from {skill_override_path}\\n"
        )
        sys.exit(1)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass inside the loaded file can
    # resolve cls.__module__ via sys.modules — Python stdlib idiom.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    HarnessAgent = getattr(module, "HarnessAgent", None)
    if HarnessAgent is None:
        sys.stderr.write(
            f"skill override at {skill_override_path} has no HarnessAgent class\\n"
        )
        sys.exit(1)
else:
    # Fallback: import from the baked-in baseline package (M4 will
    # populate this).
    try:
        from baselines.tau3_retail_v1.agent import HarnessAgent  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "no skill_override_path AND baselines.tau3_retail_v1 unavailable\\n"
        )
        sys.exit(1)

# Register HarnessAgent factory before importing run_domain — registry
# needs to know about "custom_agent" before TextRunConfig validates.
from tau2 import registry
from tau2.data_model.simulation import TextRunConfig
from tau2.run import run_domain


def _create_harness_agent(tools, domain_policy, **kwargs):
    return HarnessAgent(
        tools=tools,
        domain_policy=domain_policy,
        llm=kwargs.get("llm"),
        llm_args=kwargs.get("llm_args"),
    )


if registry.get_agent_factory("custom_agent") is None:
    registry.register_agent_factory(_create_harness_agent, "custom_agent")

agent_model = os.environ.get("AGENT_MODEL")
user_model = os.environ.get("USER_MODEL", agent_model)
if not agent_model:
    sys.stderr.write("AGENT_MODEL env var is required\\n")
    sys.exit(1)

config = TextRunConfig(
    domain=domain,
    agent="custom_agent",
    llm_agent=agent_model,
    llm_user=user_model,
    task_split_name=split,
    task_ids=task_ids,
    max_concurrency=max_concurrency,
)

try:
    results = run_domain(config)
except Exception as e:
    sys.stderr.write(f"run_domain crashed: {type(e).__name__}: {e}\\n")
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)

# Extract per-task rewards. tau2 sets reward_info=None for sims that
# died before the verifier ran (infra errors); we surface those as
# None in BenchmarkResult so val_score treats them as 0.0 without
# claiming the model produced a wrong answer.
rewards: dict[str, float | None] = {}
infra_errors = 0
n_evaluated = 0
infra_diag: list[dict[str, str]] = []
for sim in results.simulations:
    tid = str(sim.task_id)
    if sim.reward_info is None:
        rewards[tid] = None
        infra_errors += 1
        # Surface why the sim died: tau2 attaches termination_reason +
        # an exception/error string to the simulation object. Without
        # this, infra_errors is opaque (the gate gets a None reward
        # and can't tell crash from rate-limit from agent stall).
        diag: dict[str, str] = {"task_id": tid}
        tr = getattr(sim, "termination_reason", None)
        if tr is not None:
            diag["termination_reason"] = str(tr)[:300]
        # tau2 stores the actual exception in sim.info when the task
        # fails permanently (see tau2/runner/progress.py:retry path).
        info = getattr(sim, "info", None) or {}
        if isinstance(info, dict):
            for k in ("error", "error_type", "failed_after_attempts"):
                v = info.get(k)
                if v is not None:
                    diag[k] = str(v)[:600]
            # Full traceback — truncation here hides the actual error
            # frame, so allow a larger budget on this single field.
            tb = info.get("error_traceback")
            if tb is not None:
                diag["error_traceback"] = str(tb)[:4000]
        diag["n_messages"] = str(len(getattr(sim, "messages", None) or []))
        infra_diag.append(diag)
    else:
        rewards[tid] = float(sim.reward_info.reward)
        n_evaluated += 1

# tau2 auto-saves to DATA_DIR/simulations/<run_name>/results.json.
# We don't get the path back from run_domain directly; reconstruct it
# by listing the simulations dir and taking the most recent entry.
sims_root = Path(os.environ.get("TAU2_DATA_DIR", "/tau2_data")) / "simulations"
raw_run_dir = ""
if sims_root.is_dir():
    runs = sorted(
        (p for p in sims_root.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
    )
    if runs:
        raw_run_dir = str(runs[-1])

# Serialize each simulation's full conversation back to the host. The
# container's tmpfs-backed /tau2_data/simulations dir is destroyed when
# the container exits, so without this we lose the per-task message
# history forever and can never re-analyze a failure (task 33 / 49 in
# iter 11 are exactly the case that motivated this — see
# /Users/jit/code/ownevo/backups/tau3_p2_batch1_complete_20260509/README.md
# § Schema note for the postmortem). pydantic objects → JSON via
# `.model_dump(mode="json")` to keep enums / datetimes serializable.
def _dump_sim(sim):
    def _maybe_dump(obj):
        if obj is None:
            return None
        if hasattr(obj, "model_dump"):
            try:
                return obj.model_dump(mode="json")
            except Exception:
                pass
        if hasattr(obj, "__dict__"):
            return {k: str(v) for k, v in obj.__dict__.items()}
        return str(obj)
    return {
        "task_id": str(sim.task_id),
        "reward": (
            float(sim.reward_info.reward) if sim.reward_info is not None else None
        ),
        "reward_info": _maybe_dump(sim.reward_info),
        "termination_reason": (
            str(sim.termination_reason)
            if getattr(sim, "termination_reason", None) is not None else None
        ),
        "info": (
            sim.info if isinstance(getattr(sim, "info", None), dict) else None
        ),
        "messages": [_maybe_dump(m) for m in (getattr(sim, "messages", None) or [])],
        "duration_seconds": getattr(sim, "duration", None),
    }

simulations = [_dump_sim(s) for s in results.simulations]

sys.stdout.write(json.dumps({
    "rewards": rewards,
    "n_simulations": len(results.simulations),
    "n_evaluated": n_evaluated,
    "infra_errors": infra_errors,
    "infra_diag": infra_diag,
    "raw_run_dir": raw_run_dir,
    "simulations": simulations,
}))
'''


class Tau3SandboxError(RuntimeError):
    """The sandboxed τ³ pipeline failed before producing a parseable result.

    Distinct from a sandbox ``error_class`` (Timeout / OOM / Crash) — those
    are surfaced by the gate runner's SANDBOX_ERROR short-circuit. This
    one means the run completed but the caller cannot reconstruct a
    rewards dict from its stdout (missing keys, wrong shapes,
    JSON-parse error, etc.).
    """


@dataclass
class SandboxedTauBenchRunner:
    """``BenchmarkRunner`` implementation for Sierra tau-bench.

    Attributes:
        domain: ``"retail"`` | ``"airline"`` | ``"telecom"`` |
            ``"telecom_full"`` | ``"telecom_small"``. Resolves to the
            corresponding tau2 task set.
        split: ``"train"`` | ``"test"``. The gate runs against ``"test"``
            for val_score; the proposer reads ``"train"`` failure traces.
        agent_model: LiteLLM-style model id for the task agent
            (e.g., ``"anthropic/claude-sonnet-4-6"``,
            ``"ollama_chat/qwen3-coder:30b"``). Passed via the
            ``AGENT_MODEL`` env var so the τ³ image's sitecustomize.py
            also redirects the hardcoded gpt-4.1 evaluator/interface
            defaults to this model.
        user_model: Same shape; defaults to ``agent_model``. tau2's
            user simulator runs this model.
        sandbox: A ``LocalDockerSandbox`` constructed with the τ³ image
            and ``network="bridge"`` so LiteLLM can reach cloud / local
            LLM endpoints. Caller chooses image + resource limits.
        max_concurrency: tau2 batch parallelism. 3 matches NeoSigma's
            default for cloud-API runs; 1 for local-Ollama runs (single
            VRAM slot).
        timeout_seconds: Per-call wall-clock budget. Defaults to 30 min
            — multi-turn LLM conversations across 40+ retail tasks at
            cloud latency take ~15 min; local-Ollama needs ~30+.
        memory_mb: cgroup cap. 1024 MB is enough for tau2 + LiteLLM HTTP
            buffers; LLM weights run server-side.
        skill_override_dir: Optional host directory bind-mounted at
            ``/skill_override`` (read-only). Must contain ``agent.py``
            with a ``HarnessAgent`` class. When set, the entrypoint
            registers it as the tau2 ``custom_agent`` factory instead
            of falling back to the image-baked-in baseline. This is the
            mechanism the gate uses to score agent-proposed skill
            content (analogue of M5's B4.1 override).
        anthropic_api_key: Forwarded to the sandbox env so LiteLLM's
            anthropic provider can authenticate. Required when
            ``agent_model`` or ``user_model`` starts with ``anthropic/``.
        anthropic_api_base: Forwarded as ``ANTHROPIC_API_BASE``. Required
            when ``agent_model`` or ``user_model`` uses an ``anthropic/``
            -prefixed model and the target server is local (e.g. LM Studio
            Anthropic-compat endpoint at ``http://host:1234``).
        openai_api_key: Same for OpenAI provider.
        openai_api_base: Forwarded as ``OPENAI_API_BASE``. Required when
            ``agent_model`` or ``user_model`` uses an ``openai/``-prefixed
            model and the target server is local (e.g. LM Studio).
        ollama_api_base: Forwarded as ``OLLAMA_API_BASE``. Required when
            ``agent_model`` starts with ``ollama_chat/`` or ``ollama/``.
    """

    domain: str
    split: str
    agent_model: str
    sandbox: LocalDockerSandbox
    user_model: str | None = None
    max_concurrency: int = 3
    timeout_seconds: float = 1800.0
    memory_mb: int = 1024
    skill_override_dir: Path | None = None
    anthropic_api_key: str | None = None
    anthropic_api_base: str | None = None
    openai_api_key: str | None = None
    openai_api_base: str | None = None
    ollama_api_base: str | None = None
    last_pipeline_result: PipelineResult | None = field(
        default=None, init=False, repr=False,
    )
    last_raw_run_dir: str | None = field(default=None, init=False, repr=False)
    last_summary: dict[str, Any] | None = field(default=None, init=False, repr=False)
    # Per-task tau2 simulations: full message history, reward_info,
    # termination_reason, info. Populated from the entrypoint's
    # `simulations` JSON. Read by `persist_gate_run` to write per-task
    # `traces` rows so failures can be re-analyzed without re-running.
    last_simulations: list[dict[str, Any]] | None = field(
        default=None, init=False, repr=False,
    )

    def __post_init__(self) -> None:
        if self.skill_override_dir is not None:
            override = Path(self.skill_override_dir).resolve()
            if not override.is_dir():
                raise ValueError(
                    "skill_override_dir must be an existing directory; "
                    f"got {self.skill_override_dir!r}",
                )
            agent_py = override / "agent.py"
            if not agent_py.is_file():
                raise ValueError(
                    f"skill_override_dir must contain agent.py; "
                    f"none found at {agent_py}",
                )
            self.skill_override_dir = override
        if self.user_model is None:
            self.user_model = self.agent_model

    async def run(
        self,
        task_ids: list[str] | None = None,
    ) -> BenchmarkResult:
        from ...agent_tools.run_pipeline import run_pipeline

        # Build the input payload the entrypoint reads as a Python global.
        skill_override_path: str | None = None
        extra_volumes: dict[str, str] = {}
        if self.skill_override_dir is not None:
            extra_volumes[str(self.skill_override_dir)] = _SKILL_OVERRIDE_MOUNT
            skill_override_path = f"{_SKILL_OVERRIDE_MOUNT}/agent.py"

        input_data: dict[str, Any] = {
            "domain": self.domain,
            "split": self.split,
            "task_ids": task_ids,
            "max_concurrency": self.max_concurrency,
            "skill_override_path": skill_override_path,
        }

        # Env vars threaded into the sandbox. AGENT_MODEL drives both
        # the runner config (TextRunConfig.llm_agent) and the
        # sitecustomize.py monkey-patches (NL_ASSERTIONS +
        # ENV_INTERFACE) — same value, two consumers.
        extra_env: dict[str, str] = {
            "AGENT_MODEL": self.agent_model,
            "USER_MODEL": self.user_model or self.agent_model,
            "TAU2_DATA_DIR": _TAU2_DATA_DIR,
        }
        if self.anthropic_api_key:
            extra_env["ANTHROPIC_API_KEY"] = self.anthropic_api_key
        if self.anthropic_api_base:
            extra_env["ANTHROPIC_API_BASE"] = self.anthropic_api_base
        if self.openai_api_key:
            extra_env["OPENAI_API_KEY"] = self.openai_api_key
        if self.openai_api_base:
            extra_env["OPENAI_API_BASE"] = self.openai_api_base
        if self.ollama_api_base:
            extra_env["OLLAMA_API_BASE"] = self.ollama_api_base

        result = await run_pipeline(
            self.sandbox,
            skill_content=_ENTRYPOINT_SCRIPT,
            input_data=input_data,
            timeout_seconds=self.timeout_seconds,
            memory_mb=self.memory_mb,
            extra_volumes=extra_volumes if extra_volumes else None,
            extra_env=extra_env,
        )
        self.last_pipeline_result = result

        if not result.ok:
            raise Tau3SandboxError(
                f"τ³ pipeline did not return ok: status={result.status}, "
                f"error_class={result.error_class}, error={result.error!r}, "
                f"stderr={(result.raw_stderr or '')[-500:]!r}",
            )

        outputs = result.outputs
        if outputs is None or not isinstance(outputs, dict):
            raise Tau3SandboxError(
                "τ³ pipeline returned no parseable JSON on stdout; "
                f"raw stdout tail: {(result.raw_stdout or '')[-500:]!r}",
            )

        rewards = outputs.get("rewards")
        if not isinstance(rewards, dict):
            raise Tau3SandboxError(
                f"τ³ pipeline output missing 'rewards' dict; got: {outputs!r}",
            )

        # Coerce reward values defensively — tau2 returns floats, but
        # JSON round-trip can produce ints if the value happens to be
        # 0 or 1. The gate's val_score property handles None.
        coerced: dict[str, float | None] = {}
        for tid, reward in rewards.items():
            if reward is None:
                coerced[str(tid)] = None
            else:
                coerced[str(tid)] = float(reward)

        self.last_raw_run_dir = outputs.get("raw_run_dir") or None
        self.last_summary = {
            "n_simulations": int(outputs.get("n_simulations", 0)),
            "n_evaluated": int(outputs.get("n_evaluated", 0)),
            "infra_errors": int(outputs.get("infra_errors", 0)),
            "infra_diag": outputs.get("infra_diag") or [],
            "raw_run_dir": self.last_raw_run_dir,
        }
        sims = outputs.get("simulations")
        self.last_simulations = sims if isinstance(sims, list) else None

        return BenchmarkResult(rewards=coerced)
