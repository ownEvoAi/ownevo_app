"""Bootstrap improvement-loop entrypoint (BL.3).

Wires the kernel's substrate end-to-end so one round of M5 improvement
fires from the command line:

    LM Studio  ──►  AsyncAnthropic                  (Anthropic Messages API,
       │            run_agent_turn                   speaks to LM Studio's
       │            ↓                                native `/v1/messages`
       │            5 kernel tools (KernelContext)   adapter)
       │            ↓
       │       TraceCollector  + asyncpg             (events + skill registry)
       │            ↓
       │       LocalDockerSandbox                    (run_pipeline tool runs
       │                                              candidate skill bodies)
       └──►  After the turn:
             1. Find the latest write_skill in the trace
             2. persist_gate_run(SandboxedM5BenchmarkRunner)  → iteration row +
                                                                proposal row +
                                                                audit entries
             3. Print structured summary

Bootstrap-mode contract (per docs/PLAN.md v3.8 § Pre-W3 Bootstrap loop)
----------------------------------------------------------------------
* `prior_eval_task_ids=[]` — no regression suite yet (B3.3 seeds it).
* `best_ever_score=None` is the *caller-supplied* fallback. Inside
  `persist_gate_run` the DB-authoritative `MAX(best_ever_score_after)`
  takes over from run 2 onward, so concurrent re-runs always gate
  against the latest committed score.
* The very first run trivially passes (no prior suite, no best to
  beat). Run 2 onward enforces improvement only.

Skill override into the sandbox (B4.1)
--------------------------------------
The sandbox image bakes the v1 skill bodies at
`/opt/ownevo/apps/kernel/baselines/m5_lightgbm/skill_v1/`. To score the
agent's *proposed* change instead of the baked-in baseline, this script
materializes all 6 baseline skill files plus `__init__.py` to a host
tempdir, overwrites the one the agent rewrote, and passes the tempdir to
`SandboxedM5BenchmarkRunner` as `skill_override_dir=`. The runner adds a
read-only bind-mount that shadows the image's `skill_v1/` package, so the
container's `from baselines.m5_lightgbm import run_baseline` ends up
importing the override. The tempdir lives until `persist_gate_run`
returns, then cleans up.

LLM backend
-----------
Defaults to LM Studio at `http://<OWNEVO_LLM_HOST>:1234` with model
`qwen/qwen3-coder-30b` (LM Studio exposes a native Anthropic
`/v1/messages` endpoint, so `AsyncAnthropic` works unchanged).
Override via env:
  * `OWNEVO_LLM_HOST`      — hostname/IP of the local LLM server
                             (default: `localhost`; set this when running
                             against a remote desktop / LAN box)
  * `OWNEVO_LLM_BASE_URL`  — full base URL, overrides OWNEVO_LLM_HOST;
                             e.g. `http://localhost:4000` for LiteLLM
  * `OWNEVO_LLM_MODEL`     — any tool-calling-capable model id
  * `OWNEVO_LLM_API_KEY`   — usually ignored by local backends; defaults
                             to the literal `"lm-studio"`

Exit codes
----------
0  iteration recorded — agent proposed a change, gate ran, rows written
2  M5 data dir missing or malformed
3  fold construction failed
4  could not connect to the DB
5  agent loop failed (sandbox_error_propagated / max_iterations / refusal)
6  agent did not register any skill change — no proposal to gate
7  agent proposed a skill the baseline override doesn't recognize
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse as _urlparse
from uuid import UUID

# `apps/kernel/baselines/` lives outside `src/` — same trick m5_baseline.py uses.
_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from baselines.m5_lightgbm import SKILL_FILES, materialize_skill_v1_dir  # noqa: E402
from ownevo_kernel.benchmark import SandboxedM5BenchmarkRunner  # noqa: E402
from ownevo_kernel.datasets import (  # noqa: E402
    M5DatasetError,
    load_m5,
    make_held_out_fold,
)
from ownevo_kernel.gate import persist_gate_run  # noqa: E402
from ownevo_kernel.middleware.claude_sdk import (  # noqa: E402
    KernelContext,
    run_agent_turn,
    run_agent_turn_openai,
)
from ownevo_kernel.sandbox import LocalDockerSandbox  # noqa: E402
from ownevo_kernel.traces import trace_session  # noqa: E402
from scripts.seed_m5_baseline import DEFAULT_WORKFLOW_ID  # noqa: E402

ENV_M5_DIR = "OWNEVO_M5_DIR"
ENV_DB_URL = "OWNEVO_DATABASE_URL"
ENV_M5_SANDBOX_IMAGE = "OWNEVO_M5_SANDBOX_IMAGE"
ENV_LLM_BASE_URL = "OWNEVO_LLM_BASE_URL"
ENV_LLM_MODEL = "OWNEVO_LLM_MODEL"
ENV_LLM_API_KEY = "OWNEVO_LLM_API_KEY"
ENV_MAX_ITERATIONS = "OWNEVO_AGENT_MAX_ITERATIONS"
ENV_LLM_API_FORMAT = "OWNEVO_LLM_API_FORMAT"
ENV_LLM_HOST = "OWNEVO_LLM_HOST"

_DEFAULT_LLM_HOST = "localhost"
_llm_host = os.environ.get(ENV_LLM_HOST, _DEFAULT_LLM_HOST)

DEFAULT_SANDBOX_IMAGE = "ownevo-sandbox-m5:0.1.0"
DEFAULT_LLM_BASE_URL = f"http://{_llm_host}:1234"
DEFAULT_LLM_BASE_URL_OPENAI = f"http://{_llm_host}:11434/v1"
DEFAULT_LLM_MODEL = "qwen/qwen3-coder-30b"
DEFAULT_LLM_API_KEY = "lm-studio"
DEFAULT_MAX_ITERATIONS = 25
_DEFAULT_TMPFS_MB = 512
_MAX_SUMMARY_CHARS = 280

_PROMPT_PATH = Path(__file__).parent / "m5_agent_prompt.md"

def _kickoff_message(workflow_id: str) -> str:
    return (
        "You're picking up the M5 demand-prediction workflow at the v1 "
        "LightGBM baseline. Read one skill, propose one focused improvement, "
        "validate via run_pipeline, register with write_skill, then end your "
        "turn.\n"
        "\n"
        "**write_skill takes structured fields, not a serialized file.** "
        "Pass `skill_id`, `kind` (e.g. `python`), `body` (the executable "
        "Python source ONLY — no `\"\"\"`, no `---`, no YAML), "
        "`capability_tags` (optional list of strings), and `retention` "
        "(an object — for the M5 baseline skills use `{\"stateless\": true}`). "
        "The kernel constructs the canonical file with frontmatter and "
        "docstring wrapper. read_skill still returns the full canonical "
        "file so you can see the shape, but you don't re-emit it.\n"
        "\n"
        f"workflow_id: {workflow_id}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CliArgs:
    m5_dir: Path
    val_days: int
    test_days: int
    workflow_id: str
    sandbox_image: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    max_iterations: int
    seed_first: bool
    api_format: str  # "anthropic" | "openai"
    no_stream: bool  # only meaningful when api_format="anthropic"
    ollama_num_ctx: int | None  # only meaningful when api_format="openai" (Ollama)
    sandbox_mem_mb: int


def parse_args(argv: list[str]) -> CliArgs:
    parser = argparse.ArgumentParser(
        prog="run_improvement_loop",
        description="Bootstrap M5 improvement loop (BL.3).",
    )
    parser.add_argument(
        "--m5-dir",
        type=Path,
        default=Path(os.environ.get(ENV_M5_DIR, "data/m5")),
        help=f"Path to the M5 CSVs (default: ${ENV_M5_DIR} or ./data/m5).",
    )
    parser.add_argument("--val-days", type=int, default=28)
    parser.add_argument("--test-days", type=int, default=28)
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument(
        "--sandbox-image",
        default=os.environ.get(ENV_M5_SANDBOX_IMAGE, DEFAULT_SANDBOX_IMAGE),
        help=f"Docker image (default: ${ENV_M5_SANDBOX_IMAGE} or {DEFAULT_SANDBOX_IMAGE}).",
    )
    parser.add_argument(
        "--api-format",
        choices=["anthropic", "openai"],
        default=os.environ.get(ENV_LLM_API_FORMAT, "anthropic"),
        help=(
            "API format the backend speaks. 'anthropic' uses AsyncAnthropic + "
            "/v1/messages (LM Studio, LiteLLM proxy). 'openai' uses AsyncOpenAI + "
            "/v1/chat/completions (direct Ollama, vLLM, etc.). "
            f"Default: ${ENV_LLM_API_FORMAT} or 'anthropic'."
        ),
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help=(
            "Use messages.create() (non-streaming) instead of messages.stream(). "
            "Only applies when --api-format=anthropic. Bypasses streaming tool-call "
            "translation bugs in LiteLLM proxy when fronting Ollama."
        ),
    )
    parser.add_argument(
        "--llm-base-url",
        default=None,
        help=(
            f"Base URL for the LLM backend. Default depends on --api-format: "
            f"'anthropic' → ${ENV_LLM_BASE_URL} or {DEFAULT_LLM_BASE_URL} "
            f"(LM Studio); 'openai' → {DEFAULT_LLM_BASE_URL_OPENAI} (Ollama)."
        ),
    )
    parser.add_argument(
        "--llm-model",
        default=os.environ.get(ENV_LLM_MODEL, DEFAULT_LLM_MODEL),
        help=f"Model id. Default: ${ENV_LLM_MODEL} or {DEFAULT_LLM_MODEL}.",
    )
    parser.add_argument(
        "--llm-api-key",
        default=os.environ.get(ENV_LLM_API_KEY, DEFAULT_LLM_API_KEY),
        help=(
            f"API key. Local backends (LM Studio, LiteLLM-proxied Ollama) "
            f"usually ignore this. Default: ${ENV_LLM_API_KEY} or 'lm-studio'."
        ),
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=int(os.environ.get(ENV_MAX_ITERATIONS, DEFAULT_MAX_ITERATIONS)),
        help=(
            f"Cap on agent tool-use turns "
            f"(default: ${ENV_MAX_ITERATIONS} or {DEFAULT_MAX_ITERATIONS})."
        ),
    )
    parser.add_argument(
        "--sandbox-mem-mb",
        type=int,
        default=_DEFAULT_TMPFS_MB,
        help=f"Sandbox tmpfs + memory limit in MB (default: {_DEFAULT_TMPFS_MB}).",
    )
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help=(
            "Skip the bootstrap seed (BL.1) call before the loop. Use only "
            "when you've already run `make seed-m5-baseline`."
        ),
    )
    parser.add_argument(
        "--ollama-num-ctx",
        type=int,
        default=None,
        help=(
            "Per-request context length forwarded to the backend as "
            "`extra_body={'options': {'num_ctx': N}}`. Required for Ollama "
            "via /v1 because AsyncOpenAI doesn't pass `options.num_ctx` "
            "natively, and Ollama's /v1 default is smaller than the "
            "daemon-level OLLAMA_CONTEXT_LENGTH. Recommended: 65536. "
            "Only meaningful with --api-format=openai; non-Ollama backends "
            "(LMS, vLLM) ignore the field."
        ),
    )
    ns = parser.parse_args(argv)

    if ns.ollama_num_ctx is not None and ns.ollama_num_ctx <= 0:
        parser.error(f"--ollama-num-ctx must be a positive integer; got {ns.ollama_num_ctx}")

    # Resolve base URL default per api_format when not explicitly supplied
    if ns.llm_base_url is not None:
        base_url = ns.llm_base_url
    elif ns.api_format == "openai":
        base_url = os.environ.get(ENV_LLM_BASE_URL, DEFAULT_LLM_BASE_URL_OPENAI)
    else:
        base_url = os.environ.get(ENV_LLM_BASE_URL, DEFAULT_LLM_BASE_URL)

    return CliArgs(
        m5_dir=ns.m5_dir,
        val_days=ns.val_days,
        test_days=ns.test_days,
        workflow_id=ns.workflow_id,
        sandbox_image=ns.sandbox_image,
        llm_base_url=base_url,
        llm_model=ns.llm_model,
        llm_api_key=ns.llm_api_key,
        max_iterations=ns.max_iterations,
        seed_first=not ns.no_seed,
        api_format=ns.api_format,
        no_stream=ns.no_stream,
        ollama_num_ctx=ns.ollama_num_ctx,
        sandbox_mem_mb=ns.sandbox_mem_mb,
    )


# ---------------------------------------------------------------------------
# Entry — async core
# ---------------------------------------------------------------------------


async def main_async(args: CliArgs) -> int:
    db_url = os.environ.get(ENV_DB_URL)
    if not db_url:
        print(
            f"error: {ENV_DB_URL} is not set; the loop needs a migrated "
            "Postgres for skill registry + audit log writes.",
            file=sys.stderr,
        )
        return 4

    try:
        catalog = load_m5(args.m5_dir)
    except M5DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        fold = make_held_out_fold(
            catalog,
            val_days=args.val_days,
            test_days=args.test_days,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    import asyncpg

    from scripts.seed_m5_baseline import seed_baseline

    try:
        conn = await asyncpg.connect(db_url, timeout=10)
    except (asyncpg.PostgresError, OSError) as exc:
        print(f"error: could not connect to DB: {exc}", file=sys.stderr)
        return 4

    try:
        if args.seed_first:
            seed_result = await seed_baseline(conn, workflow_id=args.workflow_id)
            n_total = len(seed_result.registered) + len(seed_result.skipped)
            print(
                f"seed: workflow={seed_result.workflow_id} "
                f"registered={len(seed_result.registered)}/{n_total} "
                f"skipped={len(seed_result.skipped)}/{n_total}",
            )

        sandbox = LocalDockerSandbox(
            image=args.sandbox_image,
            tmpfs_size_mb=args.sandbox_mem_mb,
        )

        actor = f"agent:{args.llm_model}"
        kernel_context = KernelContext(
            conn=conn,
            sandbox=sandbox,
            actor=actor,
            default_workflow_id=args.workflow_id,
        )

        if args.api_format == "openai":
            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                api_key=args.llm_api_key,
                base_url=args.llm_base_url,
            )
        else:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(
                api_key=args.llm_api_key,
                base_url=args.llm_base_url,
            )

        system_prompt = _PROMPT_PATH.read_text()

        _p = _urlparse(args.llm_base_url)
        _safe_url = f"{_p.scheme}://{_p.hostname}:{_p.port or ''}"
        _stream_flag = "" if args.api_format == "openai" else (
            " no_stream=True" if args.no_stream else ""
        )
        print(
            f"agent: model={args.llm_model} base_url={_safe_url} "
            f"api_format={args.api_format}{_stream_flag} "
            f"max_iterations={args.max_iterations}",
        )
        if args.no_stream and args.api_format == "openai":
            print(
                "warning: --no-stream has no effect with --api-format=openai "
                "(the OpenAI path always streams via /v1/chat/completions)",
                file=sys.stderr,
            )

        async with trace_session(conn, workflow_id=args.workflow_id) as collector:
            if args.api_format == "openai":
                agent_result = await run_agent_turn_openai(
                    client,
                    system=system_prompt,
                    user_message=_kickoff_message(args.workflow_id),
                    kernel_context=kernel_context,
                    collector=collector,
                    model=args.llm_model,
                    max_iterations=args.max_iterations,
                    ollama_num_ctx=args.ollama_num_ctx,
                )
            else:
                # Enable Anthropic prompt caching only when talking to the
                # real cloud API. LMS Anthropic shim caches automatically
                # without cache_control markers (per F8 in
                # docs/local-model-testing.md), and adding markers there is
                # wasted bytes (and may not be honored by their compat
                # layer). cloud Anthropic is where the savings are real:
                # cache_read tokens cost ~10% of cache_creation, with
                # 5-minute TTL — multi-iteration cost drops ~80%.
                _is_cloud_anthropic = "api.anthropic.com" in args.llm_base_url
                agent_result = await run_agent_turn(
                    client,
                    system=system_prompt,
                    user_message=_kickoff_message(args.workflow_id),
                    kernel_context=kernel_context,
                    collector=collector,
                    model=args.llm_model,
                    max_iterations=args.max_iterations,
                    no_stream=args.no_stream,
                    enable_prompt_caching=_is_cloud_anthropic,
                )
            collector.set_token_usage(dict(agent_result.token_usage))

            print(
                f"agent: stop_reason={agent_result.stop_reason} "
                f"iterations={agent_result.iterations} "
                f"tool_calls={agent_result.tool_call_count} "
                f"tool_errors={agent_result.tool_error_count} "
                f"tokens={agent_result.token_usage}",
            )

            proposal = _extract_latest_write_skill(collector.events)

        if not agent_result.succeeded:
            print(
                f"error: agent did not finish cleanly "
                f"(stop_reason={agent_result.stop_reason}); skipping gate run.",
                file=sys.stderr,
            )
            if proposal is not None:
                # skill_version row exists in DB but no gate record — log so the
                # audit trail shows why version_id was never gated.
                print(
                    f"warning: orphaned skill version not gated: "
                    f"skill_id={proposal.skill_id} version_id={proposal.version_id}",
                    file=sys.stderr,
                )
            return 5

        if proposal is None:
            print(
                "error: agent did not register any skill change "
                "(no successful write_skill); nothing to gate.",
                file=sys.stderr,
            )
            return 6

        print(
            f"proposal: skill_id={proposal.skill_id} "
            f"version_id={proposal.version_id} "
            f"version_seq={proposal.version_seq}",
        )

        with tempfile.TemporaryDirectory(prefix="ownevo-skill-override-") as tmpdir:
            override_dir = Path(tmpdir)
            try:
                _materialize_skill_override(override_dir, proposal)
            except UnknownProposedSkillError as exc:
                print(
                    f"error: {exc} "
                    f"(orphaned skill_version={proposal.version_id})",
                    file=sys.stderr,
                )
                return 7

            runner = SandboxedM5BenchmarkRunner(
                catalog_dir=args.m5_dir,
                fold=fold,
                sandbox=sandbox,
                skill_override_dir=override_dir,
            )

            persisted = await persist_gate_run(
                conn,
                runner,
                workflow_id=args.workflow_id,
                skill_id=proposal.skill_id,
                proposed_content=proposal.content,
                plain_language_summary=proposal.diff_summary
                    or agent_result.final_text[:_MAX_SUMMARY_CHARS]
                    or f"agent-proposed change to {proposal.skill_id}",
                actor=actor,
                proposed_skill_version_id=proposal.version_id,
                prior_eval_task_ids=(),
                best_ever_score=None,
            )

        gate = persisted.gate_result
        summary = {
            "iteration_id": str(persisted.iteration.id),
            "iteration_index": persisted.iteration.iteration_index,
            "decision": gate.decision.value,
            "rationale": gate.rationale,
            "val_score": gate.val_score,
            "best_ever_score_before": gate.best_ever_score_before,
            "best_ever_score_after": gate.best_ever_score_after,
            "proposal_id": str(persisted.proposal.id),
            "proposal_state": persisted.proposal.state.value,
            "audit_started_id": str(persisted.audit_started_id),
            "audit_completed_id": str(persisted.audit_completed_id),
        }
        print(json.dumps(summary, indent=2))
        return 0
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Skill-override materialization (B4.1)
# ---------------------------------------------------------------------------


class UnknownProposedSkillError(ValueError):
    """The agent registered a skill_id that doesn't map onto one of the
    6 baseline files. The bootstrap loop only knows how to override the
    v1 LightGBM pipeline; a brand-new skill_id has no slot to fill."""


def _materialize_skill_override(dst: Path, proposal: _AgentProposal) -> None:
    """Copy the 6 baseline skill files + ``__init__.py`` into ``dst``,
    then overwrite the one the agent rewrote with ``proposal.content``.

    The container's image bakes the v1 skills at
    ``/opt/ownevo/apps/kernel/baselines/m5_lightgbm/skill_v1/``. A
    bind-mount of ``dst`` on top of that path lets the orchestrator's
    ``from .skill_v1 import ...`` resolve to the override instead.

    Skill-id → filename: ``m5.baseline.v1.feature_engineer`` →
    ``feature_engineer.py``. Anything outside the 6 known files raises
    :class:`UnknownProposedSkillError`.

    Permissions: the sandbox container drops CAP_DAC_OVERRIDE, so its
    uid 0 cannot bypass DAC. ``materialize_skill_v1_dir`` relaxes the
    dir and per-file modes to world-readable so the bind-mount is
    consumable inside the container.
    """
    if "/" in proposal.skill_id or "\x00" in proposal.skill_id:
        raise UnknownProposedSkillError(
            f"agent proposed skill_id with illegal path character: {proposal.skill_id!r}"
        )

    proposed_fname = proposal.skill_id.rsplit(".", 1)[-1] + ".py"
    if proposed_fname not in SKILL_FILES:
        raise UnknownProposedSkillError(
            f"agent proposed unknown skill_id {proposal.skill_id!r}; "
            f"override expects one of {SKILL_FILES!r}"
        )

    materialize_skill_v1_dir(dst)
    (dst / proposed_fname).write_text(proposal.content, encoding="utf-8")
    os.chmod(dst / proposed_fname, 0o644)


# ---------------------------------------------------------------------------
# Trace inspection — find the agent's proposal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AgentProposal:
    skill_id: str
    content: str
    diff_summary: str | None
    version_id: UUID
    version_seq: int


def _extract_latest_write_skill(events) -> _AgentProposal | None:
    """Walk the trace events forward, collect successful write_skill pairs, and
    return the last one (most recent successful write_skill call).

    The agent's input dict (skill_id, kind, body, retention, optional
    diff_summary) is on the paired ToolCallStart; the canonical
    constructed content + registered version_id/version_seq are on the
    ToolCallResult. We pair by `call_id` and read `content` from the
    result so the bind-mount path always sees what the registry actually
    persisted (post-construction by `build_skill_content`), not whatever
    the agent's structured args were.

    Returns None if the agent never called write_skill or every call errored.
    """
    starts: dict[str, dict] = {}
    pairs: list[tuple[dict, dict]] = []
    for event in events:
        ev_type = getattr(event, "type", None)
        if ev_type == "tool_call_start" and getattr(event, "name", None) == "write_skill":
            starts[event.call_id] = event.args
        elif ev_type == "tool_call_result" and getattr(event, "name", None) == "write_skill":
            args = starts.pop(event.call_id, None)
            if args is None:
                continue
            if event.status != "ok":
                continue
            output = event.output if isinstance(event.output, dict) else {}
            pairs.append((args, output))

    if not pairs:
        return None

    args, output = pairs[-1]
    skill_id = output.get("skill_id") or args.get("skill_id")
    content = output.get("content")
    if not isinstance(skill_id, str) or not isinstance(content, str):
        return None
    version_id_raw = output.get("version_id")
    version_seq = output.get("version_seq")
    if not isinstance(version_id_raw, str) or not isinstance(version_seq, int):
        return None
    try:
        version_id = UUID(version_id_raw)
    except ValueError:
        return None

    diff_summary = args.get("diff_summary")
    if diff_summary is not None and not isinstance(diff_summary, str):
        diff_summary = None

    return _AgentProposal(
        skill_id=skill_id,
        content=content,
        diff_summary=diff_summary,
        version_id=version_id,
        version_seq=version_seq,
    )


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> int:
    args = parse_args(sys.argv[1:])
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
