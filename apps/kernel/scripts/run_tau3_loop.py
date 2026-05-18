"""τ³ improvement-loop entrypoint (P1.5 / M9).

One-iteration driver for the τ³-retail workflow. Mirrors the shape of
``scripts/run_improvement_loop.py`` (M5 / BL.3) but specialized for τ³:

  * Skill override is a single ``agent.py`` file (HarnessAgent class) —
    no 6-file package to materialize like M5.
  * Sandbox is ``ownevo-sandbox-tau3:0.1.0`` with ``network='bridge'``
    so LiteLLM inside tau2 can reach cloud / local LLM endpoints.
  * BenchmarkRunner is ``SandboxedTauBenchRunner`` (M3); it takes
    domain + split directly, no dataset-loading dance.

Three LLM roles, all configurable independently:

  1. **Loop agent** — proposes the new ``agent.py`` via ``write_skill``
     tool calls. This is what the script's ``--llm-model`` /
     ``--api-format`` / ``--llm-base-url`` flags configure. Defaults to
     ``qwen3-coder:30b`` on Ollama (free, validated lift driver per
     TODO-19) talking the OpenAI compat endpoint.
  2. **Task agent** — runs INSIDE tau2 inside the sandbox. Plays the
     retail customer-service agent. Configured via
     ``--task-agent-model``; defaults to ``anthropic/claude-sonnet-4-6``
     (the validated Day-1 baseline at val_score=0.8000 on retail test).
  3. **User simulator** — also inside tau2. Plays the customer.
     Configured via ``--task-user-model``; defaults to
     ``anthropic/claude-haiku-4-5-20251001`` (cheaper, simpler role
     per the reference auto-harness convention).

Bootstrap-mode contract is the same as M5's: the very first run
trivially passes (no prior eval suite, no best to beat); from run 2
onward the gate enforces improvement. ``persist_gate_run`` reads
``MAX(best_ever_score_after)`` from the iterations table.

LLM-judge approval gate (condition C) is wired via ``--llm-judge``.
When engaged, every gate-passing proposal is sent to claude-opus-4-7
on Anthropic cloud; admitted → ``approved-awaiting-deploy``, rejected →
``rejected`` with the judge's rationale in the audit trail. Without
``--llm-judge`` the loop runs autonomous (condition B).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse as _urlparse
from uuid import UUID

# baselines/ lives outside src/ — same trick m5_baseline.py uses.
_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from ownevo_kernel.benchmark.tau3 import (  # noqa: E402
    SandboxedTauBenchRunner,
)
from ownevo_kernel.approvals.service import approve_proposal, reject_proposal  # noqa: E402
from ownevo_kernel.approvers.llm_judge.fixtures import LabeledApprovalCase  # noqa: E402
from ownevo_kernel.approvers.llm_judge.judge import judge_proposal_explanation  # noqa: E402
from ownevo_kernel.gate import persist_gate_run  # noqa: E402
from ownevo_kernel.types import ApproverType  # noqa: E402
from ownevo_kernel.middleware.claude_sdk import (  # noqa: E402
    KernelContext,
    run_agent_turn,
    run_agent_turn_openai,
)
from ownevo_kernel.observability import (  # noqa: E402
    fetch_past_attempts,
    format_past_attempts,
)
from ownevo_kernel.sandbox import LocalDockerSandbox  # noqa: E402
from ownevo_kernel.traces import trace_session  # noqa: E402

ENV_DB_URL = "OWNEVO_DATABASE_URL"
ENV_LLM_HOST = "OWNEVO_LLM_HOST"

# Shell hooks for model swap between proposer and eval phases — set when the
# proposer + task agent models can't co-reside in VRAM. The hook runs as a
# shell command (e.g. `lms unload PROPOSER && lms load TASK_AGENT -c 65536`).
ENV_HOOK_AFTER_PROPOSER = "OWNEVO_TAU3_AFTER_PROPOSER_CMD"
ENV_HOOK_AFTER_EVAL = "OWNEVO_TAU3_AFTER_EVAL_CMD"

# Allow only `lms load/unload <model> [--context-length N]` chains joined by
# `&&`.  Rejects anything with shell metacharacters (`;`, `|`, `$`, backticks,
# redirects, etc.) before the command reaches subprocess.
_SAFE_HOOK_RE = re.compile(
    r"^(?:lms\s+(?:load|unload)\s+'?[a-zA-Z0-9/._:@-]+'?"
    r"(?:\s+(?:-c|--context-length)\s+\d+)?)"
    r"(?:\s+&&\s+lms\s+(?:load|unload)\s+'?[a-zA-Z0-9/._:@-]+'?"
    r"(?:\s+(?:-c|--context-length)\s+\d+)?)*$"
)


def _run_shell_hook(name: str, env_var: str) -> None:
    """Run an optional shell-hook command. Failures are surfaced but non-fatal."""
    cmd = os.environ.get(env_var, "").strip()
    if not cmd:
        return
    if not _SAFE_HOOK_RE.match(cmd):
        print(
            f"hook[{name}]: REJECTED — command does not match safe lms-only pattern: {cmd!r}",
            file=sys.stderr,
            flush=True,
        )
        return
    print(f"hook[{name}]: $ {cmd}", flush=True)
    try:
        rc = subprocess.call(cmd, shell=True)
    except Exception as exc:  # noqa: BLE001
        print(f"hook[{name}]: exception: {exc!r}", file=sys.stderr, flush=True)
        return
    if rc != 0:
        print(f"hook[{name}]: WARNING rc={rc}", file=sys.stderr, flush=True)

DEFAULT_WORKFLOW_ID = "tau3-retail-v1"
DEFAULT_SANDBOX_IMAGE = "ownevo-sandbox-tau3:0.1.0"
DEFAULT_DOMAIN = "retail"
DEFAULT_SPLIT = "test"
DEFAULT_TASK_AGENT_MODEL = "anthropic/claude-sonnet-4-6"
DEFAULT_TASK_USER_MODEL = "anthropic/claude-haiku-4-5-20251001"

# Loop agent defaults — matches M5 / BL.3's qwen3-coder Ollama path
# (TODO-19 closure: validated free local lift driver).
_DEFAULT_LLM_HOST = "localhost"
_llm_host = os.environ.get(ENV_LLM_HOST, _DEFAULT_LLM_HOST)
DEFAULT_LLM_BASE_URL_OPENAI = f"http://{_llm_host}:11434/v1"
DEFAULT_LLM_BASE_URL_ANTHROPIC = f"http://{_llm_host}:1234"
DEFAULT_LLM_MODEL = "qwen3-coder:30b"
DEFAULT_LLM_API_KEY = "lm-studio"
DEFAULT_LLM_API_FORMAT = "openai"
DEFAULT_MAX_ITERATIONS = 25
_MAX_SUMMARY_CHARS = 280

_PROMPT_PATH = Path(__file__).parent / "tau3_agent_prompt.md"

CANONICAL_TAU3_SKILL_ID = "tau3.retail.baseline.v1.agent"


# ---------------------------------------------------------------------------
# Kickoff message
# ---------------------------------------------------------------------------


def _kickoff_message(workflow_id: str, past_attempts_block: str = "") -> str:
    base = (
        "You're picking up the τ³-retail improvement loop. The skill "
        f"`{CANONICAL_TAU3_SKILL_ID}` is the agent the gate will run "
        "against tau-bench retail tasks. Your job: read it, find a "
        "specific failure pattern (use `analyze_failures` to list recent "
        "low-reward sims if any exist), and propose ONE focused change.\n"
        "\n"
        "**Skill shape.** The skill body is a Python file containing a "
        "`HarnessAgent` class subclassing `tau2.agent.llm_agent.LLMAgent` "
        "plus an `AGENT_INSTRUCTION` system-prompt string + a "
        "`HarnessState` dataclass. You can edit:\n"
        "  - `AGENT_INSTRUCTION` (system prompt body)\n"
        "  - Add fields to `HarnessState` for cross-turn memory\n"
        "  - Wrap `generate_next_message` to inject context, route by "
        "task type, or post-process the LLM response\n"
        "\n"
        "Do NOT change the class name `HarnessAgent` or break the "
        "`LLMAgent` superclass contract — the gate-runner imports by name.\n"
        "\n"
        "**write_skill takes structured fields, not a serialized file.** "
        "Pass `skill_id` (always "
        f"`{CANONICAL_TAU3_SKILL_ID}`), `kind` (`python`), `body` (the "
        "executable Python source ONLY — no `\"\"\"`, no `---`, no YAML), "
        "`capability_tags` (optional), and `retention` "
        "(`{\"stateless\": true, \"improvement_target\": "
        "\"tau3_retail_test_val_score\"}`). The kernel constructs the "
        "canonical file with frontmatter + docstring wrapper.\n"
        "\n"
        f"workflow_id: {workflow_id}"
    )
    if past_attempts_block:
        return past_attempts_block + "\n" + base
    return base


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CliArgs:
    workflow_id: str
    sandbox_image: str
    domain: str
    split: str
    task_agent_model: str
    task_user_model: str
    task_concurrency: int
    task_timeout_seconds: float
    task_memory_mb: int
    llm_model: str
    llm_base_url: str
    llm_api_key: str
    api_format: str
    no_stream: bool
    ollama_num_ctx: int | None
    max_iterations: int
    seed_first: bool
    llm_judge: bool
    llm_judge_model: str
    llm_judge_base_url: str | None
    llm_judge_api_key: str


def parse_args(argv: list[str]) -> CliArgs:
    parser = argparse.ArgumentParser(
        prog="run_tau3_loop",
        description="τ³ improvement-loop driver (one iteration).",
    )
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--sandbox-image", default=DEFAULT_SANDBOX_IMAGE)
    parser.add_argument("--domain", default=DEFAULT_DOMAIN,
                        choices=["retail", "airline", "telecom"])
    parser.add_argument("--split", default=DEFAULT_SPLIT,
                        choices=["train", "test"])
    parser.add_argument("--task-agent-model", default=DEFAULT_TASK_AGENT_MODEL,
                        help="LiteLLM-style model id for the τ³ task agent "
                             "(runs INSIDE tau2 inside the sandbox).")
    parser.add_argument("--task-user-model", default=DEFAULT_TASK_USER_MODEL,
                        help="LiteLLM-style model id for tau2's user "
                             "simulator. Defaults to a cheaper model.")
    parser.add_argument("--task-concurrency", type=int, default=3,
                        help="tau2 max_concurrency for the gate run.")
    parser.add_argument("--task-timeout-seconds", type=float, default=2400.0,
                        help="Wall-clock budget for the entire gate sandbox "
                             "run (covers all tasks at given concurrency). "
                             "P1 baseline (40 tasks @ c=3) was 960s on Sonnet; "
                             "default 2400s leaves headroom for slower runs.")
    parser.add_argument("--task-memory-mb", type=int, default=1024)
    # Loop-agent flags — same shape as run_improvement_loop.py
    parser.add_argument("--llm-model", default=DEFAULT_LLM_MODEL,
                        help="Loop agent that proposes skill changes.")
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--llm-api-key", default=DEFAULT_LLM_API_KEY)
    parser.add_argument("--api-format", default=DEFAULT_LLM_API_FORMAT,
                        choices=["openai", "anthropic", "ollama"])
    parser.add_argument("--no-stream", action="store_true",
                        help="Force non-streaming Anthropic mode (LMS proxy "
                             "compat). No effect with --api-format=openai.")
    parser.add_argument("--ollama-num-ctx", type=int, default=65536,
                        help="num_ctx for Ollama OpenAI-compat backends "
                             "(F1 mitigation in docs/local-model-testing.md).")
    parser.add_argument("--max-iterations", type=int,
                        default=DEFAULT_MAX_ITERATIONS,
                        help="Cap on loop-agent inner turns "
                             "(write_skill / read_skill / analyze_failures).")
    parser.add_argument("--no-seed", action="store_false", dest="seed_first",
                        default=True)
    parser.add_argument(
        "--llm-judge",
        action="store_true",
        default=False,
        help="Condition C: gate-passing proposals go through the LLM judge "
             "before approval. Admitted → approved-awaiting-deploy; "
             "rejected → rejected with rationale. rc=0 either way.",
    )
    parser.add_argument(
        "--llm-judge-model",
        default="claude-opus-4-7",
        help="Model id for the LLM judge (default: claude-opus-4-7). "
             "Override to a local model id when using --llm-judge-base-url.",
    )
    parser.add_argument(
        "--llm-judge-base-url",
        default=None,
        help="Base URL for the judge's Anthropic-compat endpoint. "
             "Default: Anthropic cloud (https://api.anthropic.com). "
             "Set to e.g. http://192.168.1.50:1234 to use LMS as judge.",
    )
    parser.add_argument(
        "--llm-judge-api-key",
        default=None,
        help="API key for the judge endpoint. Defaults to ANTHROPIC_API_KEY "
             "env var. Use 'lm-studio' or any non-empty string for local endpoints.",
    )

    ns = parser.parse_args(argv)
    base_url = ns.llm_base_url or (
        DEFAULT_LLM_BASE_URL_OPENAI if ns.api_format in ("openai", "ollama")
        else DEFAULT_LLM_BASE_URL_ANTHROPIC
    )
    return CliArgs(
        workflow_id=ns.workflow_id,
        sandbox_image=ns.sandbox_image,
        domain=ns.domain,
        split=ns.split,
        task_agent_model=ns.task_agent_model,
        task_user_model=ns.task_user_model,
        task_concurrency=ns.task_concurrency,
        task_timeout_seconds=ns.task_timeout_seconds,
        task_memory_mb=ns.task_memory_mb,
        llm_model=ns.llm_model,
        llm_base_url=base_url,
        llm_api_key=ns.llm_api_key,
        api_format=ns.api_format,
        no_stream=ns.no_stream,
        ollama_num_ctx=ns.ollama_num_ctx,
        max_iterations=ns.max_iterations,
        seed_first=ns.seed_first,
        llm_judge=ns.llm_judge,
        llm_judge_model=ns.llm_judge_model,
        llm_judge_base_url=ns.llm_judge_base_url,
        llm_judge_api_key=(
            ns.llm_judge_api_key
            or os.environ.get("ANTHROPIC_API_KEY", "")
        ),
    )


# ---------------------------------------------------------------------------
# Env loading — same .env loader as tau3_baseline.py
# ---------------------------------------------------------------------------


_DOTENV_PATH = Path(__file__).resolve().parents[3] / ".env"  # ownevo_app/.env


def _load_dotenv_into_environ() -> None:
    if not _DOTENV_PATH.is_file():
        return
    pattern = re.compile(r"^(?P<key>[A-Z_][A-Z0-9_]*)\s*=\s*(?P<value>.*)$")
    for line in _DOTENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = pattern.match(line)
        if not m:
            continue
        key = m.group("key")
        if key in os.environ:
            continue
        val = m.group("value")
        if val and val[0] in {'"', "'"} and val[-1] == val[0]:
            val = val[1:-1]
        os.environ[key] = val


# ---------------------------------------------------------------------------
# Skill-override materialization — much simpler than M5 (1 file)
# ---------------------------------------------------------------------------


class UnknownProposedSkillError(ValueError):
    """The agent proposed a skill_id that doesn't match the τ³ retail
    canonical skill. The loop only knows how to override
    ``tau3.retail.baseline.v1.agent``."""


def _materialize_tau3_skill_override(dst: Path, proposal: _AgentProposal) -> None:
    """Write proposal.content to ``dst/agent.py``.

    The container's image bakes the baseline at
    ``/opt/ownevo/apps/kernel/baselines/tau3_retail_v1/``. The runner
    bind-mounts ``dst`` at ``/skill_override`` and the entrypoint
    imports ``HarnessAgent`` from ``/skill_override/agent.py`` directly,
    bypassing the baked-in baseline. Permissions: world-readable so the
    container's uid 0 (no DAC override) can read the file.
    """
    if "/" in proposal.skill_id or "\x00" in proposal.skill_id:
        raise UnknownProposedSkillError(
            f"agent proposed skill_id with illegal path character: {proposal.skill_id!r}"
        )
    if proposal.skill_id != CANONICAL_TAU3_SKILL_ID:
        raise UnknownProposedSkillError(
            f"agent proposed unknown skill_id {proposal.skill_id!r}; "
            f"the τ³ retail loop only edits {CANONICAL_TAU3_SKILL_ID!r}",
        )

    dst.mkdir(parents=True, exist_ok=True)
    os.chmod(dst, 0o755)
    target = dst / "agent.py"
    target.write_text(proposal.content, encoding="utf-8")
    os.chmod(target, 0o644)


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
    """Walk trace events forward, pair tool_call_start + tool_call_result
    by call_id, and return the most recent successful write_skill.

    Same pattern as run_improvement_loop._extract_latest_write_skill.
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
    try:
        version_id = UUID(version_id_raw) if isinstance(version_id_raw, str) else None
    except ValueError:
        version_id = None
    if version_id is None or not isinstance(version_seq, int):
        return None

    diff_summary = args.get("diff_summary") if isinstance(args, dict) else None
    if not isinstance(diff_summary, str):
        diff_summary = None

    return _AgentProposal(
        skill_id=skill_id,
        content=content,
        diff_summary=diff_summary,
        version_id=version_id,
        version_seq=version_seq,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main_async(args: CliArgs) -> int:
    _load_dotenv_into_environ()

    db_url = os.environ.get(ENV_DB_URL)
    if not db_url:
        print(f"error: {ENV_DB_URL} is not set.", file=sys.stderr)
        return 4

    import asyncpg  # noqa: PLC0415

    from scripts.tau3_register import seed_tau3_retail  # noqa: PLC0415

    try:
        conn = await asyncpg.connect(db_url, timeout=10)
    except (asyncpg.PostgresError, OSError) as exc:
        print(f"error: could not connect to DB: {exc}", file=sys.stderr)
        return 4

    try:
        if args.seed_first:
            seed_result = await seed_tau3_retail(
                conn, workflow_id=args.workflow_id,
                domain=args.domain,
                seed_eval_cases=(args.domain == "retail"),
            )
            print(
                f"seed: workflow={seed_result.workflow_id} "
                f"skill={'registered' if seed_result.skill_registered else 'already-current'}",
            )

        # Sandbox for the gate run — τ³ profile (bridge network for
        # cloud LLM egress, AGENT_MODEL env so the sitecustomize
        # patches redirect tau2's hardcoded gpt-4.1 evaluator default).
        gate_sandbox = LocalDockerSandbox(
            image=args.sandbox_image,
            network="bridge",
            cpus=2.0,
            pids_limit=512,
            tmpfs_size_mb=128,
        )

        # Sandbox for the loop agent's tool calls (read_skill /
        # write_skill / analyze_failures / run_pipeline). Loop agent
        # doesn't run user code, but `run_pipeline` may; reuse the
        # default M5-style sandbox (network=none).
        loop_tool_sandbox = LocalDockerSandbox(
            tmpfs_size_mb=128,
        )

        actor = f"agent:{args.llm_model}"
        kernel_context = KernelContext(
            conn=conn,
            sandbox=loop_tool_sandbox,
            actor=actor,
            default_workflow_id=args.workflow_id,
        )

        if args.api_format == "ollama":
            from ownevo_kernel.eval_runner.ollama_native import OllamaChatClient  # noqa: PLC0415
            client = OllamaChatClient(base_url=args.llm_base_url)
        elif args.api_format == "openai":
            from openai import AsyncOpenAI  # noqa: PLC0415
            # 1800s timeout matches the ollama_native.py bump (commit 9a700f1).
            # Dense / thinking LMS proposers (glm-4.7-flash, qwen3.6:27b) can
            # take 5-15 min to emit the first streaming token; the 600s SDK
            # default trips a httpx.ReadTimeout mid-stream.
            client = AsyncOpenAI(
                api_key=args.llm_api_key,
                base_url=args.llm_base_url,
                timeout=1800.0,
            )
        else:
            from anthropic import AsyncAnthropic  # noqa: PLC0415
            client = AsyncAnthropic(
                api_key=args.llm_api_key,
                base_url=args.llm_base_url,
                timeout=1800.0,
            )

        # tau3-specific system prompt is optional — fall back to a
        # minimal generic prompt if the file isn't shipped yet. (The
        # M5 loop has m5_agent_prompt.md; tau3 may grow its own as
        # the proposer iterates.)
        system_prompt = (
            _PROMPT_PATH.read_text() if _PROMPT_PATH.is_file()
            else "You are an autonomous improvement-loop coding agent. Use "
                 "the provided kernel tools to read and modify skills."
        )

        _p = _urlparse(args.llm_base_url)
        _safe_url = f"{_p.scheme}://{_p.hostname}:{_p.port or ''}"
        _stream_flag = "" if args.api_format in ("openai", "ollama") else (
            " no_stream=True" if args.no_stream else ""
        )
        print(
            f"loop-agent: model={args.llm_model} base_url={_safe_url} "
            f"api_format={args.api_format}{_stream_flag} "
            f"max_iterations={args.max_iterations}",
        )
        print(
            f"task-agent: {args.task_agent_model}  "
            f"user-sim: {args.task_user_model}  "
            f"split={args.split} concurrency={args.task_concurrency}",
        )

        try:
            past_attempts = await fetch_past_attempts(
                conn, workflow_id=args.workflow_id,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"past-attempts: skipped ({exc})", file=sys.stderr)
            past_attempts = []
        past_attempts_block = format_past_attempts(past_attempts)
        if past_attempts_block:
            print(
                f"past-attempts: {len(past_attempts)} prior iteration(s) "
                "prepended to kickoff",
            )

        async with trace_session(conn, workflow_id=args.workflow_id) as collector:
            kickoff = _kickoff_message(args.workflow_id, past_attempts_block)
            if args.api_format == "ollama":
                from ownevo_kernel.middleware.claude_sdk import run_agent_turn_ollama  # noqa: PLC0415
                agent_result = await run_agent_turn_ollama(
                    client,
                    system=system_prompt,
                    user_message=kickoff,
                    kernel_context=kernel_context,
                    collector=collector,
                    model=args.llm_model,
                    max_iterations=args.max_iterations,
                )
            elif args.api_format == "openai":
                agent_result = await run_agent_turn_openai(
                    client,
                    system=system_prompt,
                    user_message=kickoff,
                    kernel_context=kernel_context,
                    collector=collector,
                    model=args.llm_model,
                    max_iterations=args.max_iterations,
                    ollama_num_ctx=args.ollama_num_ctx,
                )
            else:
                _is_cloud_anthropic = _urlparse(args.llm_base_url).hostname == "api.anthropic.com"
                agent_result = await run_agent_turn(
                    client,
                    system=system_prompt,
                    user_message=kickoff,
                    kernel_context=kernel_context,
                    collector=collector,
                    model=args.llm_model,
                    max_iterations=args.max_iterations,
                    no_stream=args.no_stream,
                    enable_prompt_caching=_is_cloud_anthropic,
                )
            collector.set_token_usage(dict(agent_result.token_usage))

            print(
                f"loop-agent: stop_reason={agent_result.stop_reason} "
                f"iterations={agent_result.iterations} "
                f"tool_calls={agent_result.tool_call_count} "
                f"tool_errors={agent_result.tool_error_count} "
                f"tokens={agent_result.token_usage}",
            )

            proposal = _extract_latest_write_skill(collector.events)

        if not agent_result.succeeded:
            print(
                f"error: loop agent did not finish cleanly "
                f"(stop_reason={agent_result.stop_reason}); skipping gate.",
                file=sys.stderr,
            )
            if proposal is not None:
                print(
                    f"warning: orphaned skill_version not gated: "
                    f"version_id={proposal.version_id}",
                    file=sys.stderr,
                )
            return 5

        if proposal is None:
            print(
                "error: loop agent did not register any skill change "
                "(no successful write_skill); nothing to gate.",
                file=sys.stderr,
            )
            return 6

        print(
            f"proposal: skill_id={proposal.skill_id} "
            f"version_id={proposal.version_id} "
            f"version_seq={proposal.version_seq}",
        )

        # Model-swap hook: e.g. unload proposer + load task-agent before eval.
        # See ENV_HOOK_AFTER_PROPOSER in module header.
        _run_shell_hook("after_proposer", ENV_HOOK_AFTER_PROPOSER)

        with tempfile.TemporaryDirectory(prefix="ownevo-tau3-skill-override-") as tmpdir:
            override_dir = Path(tmpdir)
            try:
                _materialize_tau3_skill_override(override_dir, proposal)
            except UnknownProposedSkillError as exc:
                print(
                    f"error: {exc} "
                    f"(orphaned skill_version={proposal.version_id})",
                    file=sys.stderr,
                )
                return 7

            runner = SandboxedTauBenchRunner(
                domain=args.domain,
                split=args.split,
                agent_model=args.task_agent_model,
                user_model=args.task_user_model,
                sandbox=gate_sandbox,
                max_concurrency=args.task_concurrency,
                timeout_seconds=args.task_timeout_seconds,
                memory_mb=args.task_memory_mb,
                skill_override_dir=override_dir,
                anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
                anthropic_api_base=os.environ.get("ANTHROPIC_API_BASE"),
                openai_api_key=os.environ.get("OPENAI_API_KEY"),
                openai_api_base=os.environ.get("OPENAI_API_BASE"),
                ollama_api_base=os.environ.get("OLLAMA_API_BASE"),
            )

            # Tier-1 #3: one-task smoke before the full 40-task gate. Catches
            # runtime bugs the write_skill validator can't see (uninitialized
            # attrs in generate_next_message, missing-class-but-passes-import,
            # tau2-side template incompat). If the smoke task hits infra_error
            # we abort BEFORE persist_gate_run opens its transaction — no
            # iteration row, no eval-wide commit, ~25 min saved. Disable
            # with OWNEVO_TAU3_SKIP_SMOKE=1.
            #
            # Task ID choice: retail/test task IDs are not sequential 0..N;
            # we try a small list of known-existing IDs and use the first
            # one tau2 accepts. Override the candidate list with
            # OWNEVO_TAU3_SMOKE_TASK_ID (single ID).
            if os.environ.get("OWNEVO_TAU3_SKIP_SMOKE", "").lower() not in ("1", "true", "yes"):
                override_id = os.environ.get("OWNEVO_TAU3_SMOKE_TASK_ID", "").strip()
                if override_id:
                    smoke_candidates = [override_id]
                else:
                    # Observed in retail/test from prior runs — pick the smallest.
                    smoke_candidates = ["1", "5", "2", "3", "9"]
                smoke_summary: dict | None = None
                smoke_task_id: str | None = None
                for cand in smoke_candidates:
                    print(f"smoke: trying task_ids=['{cand}']", flush=True)
                    try:
                        await runner.run(task_ids=[cand])
                    except Exception as exc:  # noqa: BLE001
                        msg = str(exc)
                        # "Not all tasks were found" → task ID doesn't exist
                        # in this domain/split. Try the next candidate
                        # without failing the run.
                        if "Not all tasks were found" in msg:
                            print(
                                f"smoke: task_id={cand!r} not in domain/split, "
                                f"trying next",
                                flush=True,
                            )
                            continue
                        print(
                            f"error: smoke crashed: {type(exc).__name__}: {exc}",
                            file=sys.stderr,
                        )
                        return 8
                    smoke_summary = runner.last_summary or {}
                    smoke_task_id = cand
                    break
                if smoke_summary is None:
                    print(
                        f"smoke: no candidate task_id resolved "
                        f"(tried {smoke_candidates}); skipping smoke check, "
                        f"proceeding to full eval",
                        file=sys.stderr,
                    )
                else:
                    smoke_evaluated = int(smoke_summary.get("n_evaluated", 0))
                    smoke_infra = int(smoke_summary.get("infra_errors", 0))
                    print(
                        f"smoke: task_id={smoke_task_id!r} "
                        f"evaluated={smoke_evaluated} "
                        f"infra_errors={smoke_infra}",
                        flush=True,
                    )
                    if smoke_infra > 0 or smoke_evaluated == 0:
                        diag_list = smoke_summary.get("infra_diag", [])
                        diag = ""
                        if diag_list:
                            err = diag_list[0].get("error", "")
                            diag = f"\n  first_error={err[:500]}"
                        print(
                            f"error: one-task smoke failed — aborting before "
                            f"full eval to save ~25 min. "
                            f"infra_errors={smoke_infra}/1 "
                            f"evaluated={smoke_evaluated}/1"
                            f"{diag}\n"
                            f"  (set OWNEVO_TAU3_SKIP_SMOKE=1 to bypass)",
                            file=sys.stderr,
                        )
                        return 9

            persisted = await persist_gate_run(
                conn,
                runner,
                workflow_id=args.workflow_id,
                skill_id=proposal.skill_id,
                proposed_content=proposal.content,
                plain_language_summary=(
                    proposal.diff_summary
                    or agent_result.final_text[:_MAX_SUMMARY_CHARS]
                    or f"agent-proposed change to {proposal.skill_id}"
                ),
                actor=actor,
                proposed_skill_version_id=proposal.version_id,
                prior_eval_task_ids=(),
                best_ever_score=None,
            )

        gr = persisted.gate_result
        val_score_str = f"{gr.val_score:.4f}" if gr.val_score is not None else "None"
        best_after = (
            f"{gr.best_ever_score_after:.4f}"
            if gr.best_ever_score_after is not None else "None"
        )
        print(
            f"\ngate: decision={gr.decision.name} "
            f"val_score={val_score_str} "
            f"best_ever_after={best_after}",
        )
        print(
            f"  iteration_index={persisted.iteration.iteration_index} "
            f"state={persisted.iteration.state} "
            f"proposal_id={persisted.proposal.id}",
        )
        if runner.last_summary:
            print(f"  raw_summary={json.dumps(runner.last_summary)}")
        elif gr.decision.name == "SANDBOX_ERROR" and runner.last_pipeline_result is not None:
            # last_summary stays None when the sandbox itself fails (result.ok=False)
            # before tau-bench produces any diagnostics. Surface the pipeline error
            # so the failure mode is visible without re-running with extra flags.
            pr = runner.last_pipeline_result
            print(
                f"  pipeline_error: status={pr.status} "
                f"error_class={pr.error_class} "
                f"error={pr.error!r}",
            )
            stderr_tail = (pr.raw_stderr or "")[-2000:]
            if stderr_tail:
                print(f"  pipeline_stderr_tail={stderr_tail!r}")

        if args.llm_judge and gr.decision.name == "PASS":
            import anthropic as _anthropic  # noqa: PLC0415
            # Default: Anthropic cloud (bypasses local ANTHROPIC_API_BASE).
            # Override with --llm-judge-base-url for local LMS judge.
            _judge_base_url = args.llm_judge_base_url or "https://api.anthropic.com"
            _judge_client = _anthropic.AsyncAnthropic(
                api_key=args.llm_judge_api_key,
                base_url=_judge_base_url,
            )
            print(
                f"judge-client: model={args.llm_judge_model} "
                f"base_url={_judge_base_url}",
            )
            _plain_summary = (
                proposal.diff_summary
                or agent_result.final_text[:_MAX_SUMMARY_CHARS]
                or f"agent-proposed change to {proposal.skill_id}"
            )
            _case = LabeledApprovalCase(
                case_id=str(persisted.proposal.id),
                proposal_summary=proposal.diff_summary or f"Updated {proposal.skill_id}",
                cluster_name="tau3 retail task-completion failures",
                metric_direction_expected="up",
                explanation=_plain_summary,
                ground_truth_verdict="admit",  # not used by judge; required by dataclass
                bucket="structural",           # not used by judge; required by dataclass
            )
            try:
                _judgment = await judge_proposal_explanation(
                    _judge_client, _case, model=args.llm_judge_model,
                )
            except Exception as _judge_exc:
                # Judge API failure (network, auth, malformed response) must not abort the
                # loop series — tau3_local_loop.sh treats any non-zero rc as "stopping series".
                # Safe fallback: reject so the proposal doesn't sit in gate-passed limbo.
                print(
                    f"judge: error — {_judge_exc!r}; rejecting proposal as safe fallback",
                    file=sys.stderr,
                )
                await reject_proposal(
                    conn,
                    proposal_id=persisted.proposal.id,
                    decided_by="llm-judge",
                    approver_type=ApproverType.LLM_JUDGE,
                    comment=f"judge error: {_judge_exc}",
                )
            else:
                print(
                    f"\njudge: verdict={_judgment.verdict} "
                    f"rationale={_judgment.rationale!r}",
                )
                if _judgment.verdict == "admit":
                    await approve_proposal(
                        conn,
                        proposal_id=persisted.proposal.id,
                        decided_by="llm-judge",
                        approver_type=ApproverType.LLM_JUDGE,
                        comment=_judgment.rationale,
                    )
                    print("judge: proposal admitted → approved-awaiting-deploy")
                else:
                    await reject_proposal(
                        conn,
                        proposal_id=persisted.proposal.id,
                        decided_by="llm-judge",
                        approver_type=ApproverType.LLM_JUDGE,
                        comment=_judgment.rationale,
                    )
                    print(
                        "judge: proposal rejected — gate-blocked (val_score recorded, "
                        "deploy blocked)",
                        file=sys.stderr,
                    )

        return 0
    finally:
        await conn.close()
        # Cycle-cleanup hook: e.g. unload task-agent + load proposer for next cycle.
        _run_shell_hook("after_eval", ENV_HOOK_AFTER_EVAL)


def main() -> int:
    args = parse_args(sys.argv[1:])
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
