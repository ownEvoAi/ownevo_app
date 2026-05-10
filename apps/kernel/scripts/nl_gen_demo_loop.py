"""W6 NL-gen end-to-end demo loop CLI (PLAN.md row 6.1).

Drives `nl_gen.loop.run_nl_gen_demo_loop` over one of the three hand-
authored NL-gen fixtures (demand-prediction / credit-risk /
contract-review). Each cycle: agent solver runs → failures cluster →
W6 instruction proposer writes an addendum → next cycle inherits the
cumulative instruction. The lift curve is the demo's headline visual.

Exit codes
----------
0  loop completed; any required gates passed
1  one or more `--require-*` gates failed
2  argparse / preflight failure (no Anthropic key, etc.)

Usage
-----
  make nl-gen-demo-loop                                        # demand-prediction, 3 cycles
  python scripts/nl_gen_demo_loop.py --workflow credit-risk
  python scripts/nl_gen_demo_loop.py --cycles 5 --require-climbing
  python scripts/nl_gen_demo_loop.py --pretty --include-instructions
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from ownevo_kernel.nl_gen.fixtures import (  # noqa: E402
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)
from ownevo_kernel.nl_gen.instruction_proposer import (  # noqa: E402
    DEFAULT_MAX_TOKENS as DEFAULT_PROPOSER_MAX_TOKENS,
    DEFAULT_MODEL as DEFAULT_PROPOSER_MODEL,
)
from ownevo_kernel.nl_gen.loop import (  # noqa: E402
    DEFAULT_N_CYCLES,
    DemoLoopReport,
    run_nl_gen_demo_loop,
)

ENV_API_KEY = "ANTHROPIC_API_KEY"
ENV_LLM_BASE_URL = "OWNEVO_LLM_BASE_URL"

DEFAULT_AGENT_MODEL = "claude-sonnet-4-6"
DEFAULT_BASE_URL = "https://api.anthropic.com"


@dataclass(frozen=True)
class CliArgs:
    workflow: str
    cycles: int
    agent_model: str
    proposer_model: str
    proposer_max_tokens: int
    base_url: str
    api_key: str
    pretty: bool
    include_instructions: bool
    progress: bool
    require_climbing: bool
    require_lift: float | None
    require_meets_target: bool

    def __repr__(self) -> str:
        return (
            f"CliArgs(workflow={self.workflow!r}, cycles={self.cycles}, "
            f"agent_model={self.agent_model!r}, proposer_model={self.proposer_model!r}, "
            f"base_url={self.base_url!r}, api_key=<redacted>)"
        )


def _positive_int(s: str) -> int:
    try:
        v = int(s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected integer, got {s!r}") from exc
    if v <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {v}")
    return v


def _parse_args(argv: list[str] | None = None) -> CliArgs:
    parser = argparse.ArgumentParser(
        description="W6 NL-gen end-to-end demo loop (cycle the agent + cluster + edit instruction).",
    )
    parser.add_argument(
        "--workflow",
        choices=sorted(FIXTURES.keys()),
        default="demand-prediction",
        help=(
            "Fixture workflow to drive. Default demand-prediction "
            "(recall-gated supply-chain forecast)."
        ),
    )
    parser.add_argument(
        "--cycles",
        type=_positive_int,
        default=DEFAULT_N_CYCLES,
        help=(
            f"Number of cycles. Default {DEFAULT_N_CYCLES}. The first "
            f"cycle is baseline; the last skips the proposer call. Each "
            f"cycle costs one full agent pass over the case set + (except "
            f"the last) one proposer call."
        ),
    )
    parser.add_argument(
        "--agent-model",
        default=DEFAULT_AGENT_MODEL,
        help=f"Anthropic model for the agent solver. Default: {DEFAULT_AGENT_MODEL}.",
    )
    parser.add_argument(
        "--proposer-model",
        default=DEFAULT_PROPOSER_MODEL,
        help=(
            f"Anthropic model for the W6 instruction proposer. "
            f"Default: {DEFAULT_PROPOSER_MODEL}."
        ),
    )
    parser.add_argument(
        "--proposer-max-tokens",
        type=_positive_int,
        default=DEFAULT_PROPOSER_MAX_TOKENS,
        help="Output cap on each proposer call. Default 1,500.",
    )
    parser.add_argument(
        "--anthropic-base-url",
        default=os.environ.get(ENV_LLM_BASE_URL, DEFAULT_BASE_URL),
        help=(
            f"Base URL for the Anthropic-compatible API. Default: "
            f"${ENV_LLM_BASE_URL} or {DEFAULT_BASE_URL}."
        ),
    )
    parser.add_argument(
        "--anthropic-api-key",
        default=None,
        help=(
            f"Anthropic API key. Default: ${ENV_API_KEY}. Required when "
            f"talking to the cloud."
        ),
    )
    parser.add_argument("--pretty", action="store_true", help="Indent JSON output.")
    parser.add_argument(
        "--include-instructions",
        action="store_true",
        help=(
            "Include the full per-cycle instruction text in the JSON "
            "output. Off by default to keep CLI output readable."
        ),
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help=(
            "Stream one stderr line per cycle as the loop runs "
            "(metric / failure count / cluster count / top label). "
            "Useful for live demos so the screen isn't silent during the "
            "agent passes; off by default to keep machine-parsable runs "
            "stdout-only."
        ),
    )
    parser.add_argument(
        "--require-climbing",
        action="store_true",
        help="Exit 1 if the lift curve doesn't end strictly above its start.",
    )
    parser.add_argument(
        "--require-lift",
        type=float,
        default=None,
        help=(
            "Exit 1 if (final_metric_value - baseline_metric_value) < N. "
            "Useful as the demo gate; unset for diagnostic runs."
        ),
    )
    parser.add_argument(
        "--require-meets-target",
        action="store_true",
        help=(
            "Exit 1 if the final cycle's metric doesn't clear the workflow's "
            "target_value."
        ),
    )

    ns = parser.parse_args(argv)

    if ns.require_lift is not None and ns.require_lift <= 0:
        parser.error("--require-lift must be > 0 (use a positive threshold)")

    api_key = ns.anthropic_api_key or os.environ.get(ENV_API_KEY)
    if not api_key:
        parser.error(
            f"--anthropic-api-key not set and ${ENV_API_KEY} is empty"
        )

    return CliArgs(
        workflow=ns.workflow,
        cycles=ns.cycles,
        agent_model=ns.agent_model,
        proposer_model=ns.proposer_model,
        proposer_max_tokens=ns.proposer_max_tokens,
        base_url=ns.anthropic_base_url,
        api_key=api_key,
        pretty=ns.pretty,
        include_instructions=ns.include_instructions,
        progress=ns.progress,
        require_climbing=ns.require_climbing,
        require_lift=ns.require_lift,
        require_meets_target=ns.require_meets_target,
    )


def _check_gates(args: CliArgs, report: DemoLoopReport) -> list[str]:
    failures: list[str] = []
    if args.require_climbing and not report.is_climbing():
        failures.append(
            f"--require-climbing: lift curve {list(report.lift_curve)} "
            "did not climb monotonically + end above its start"
        )
    if args.require_lift is not None:
        lift = report.absolute_lift
        if lift is None or lift < args.require_lift:
            failures.append(
                f"--require-lift: absolute_lift={lift} "
                f"< threshold={args.require_lift}"
            )
    if args.require_meets_target:
        if not report.cycles or not report.cycles[-1].meets_target:
            final = report.cycles[-1].metric_value if report.cycles else None
            failures.append(
                f"--require-meets-target: final cycle metric={final} did "
                f"not clear target={report.metric_target}"
            )
    return failures


def _redact_instructions_in_dict(d: dict, *, include: bool) -> dict:
    """Strip the verbose instruction text from each cycle unless
    ``--include-instructions`` was passed. Preserves shape — replaces
    long strings with their length so reviewers know SOMETHING was
    written without flooding the CLI."""
    if include:
        return d
    out = dict(d)
    out["cycles"] = [_redact_cycle(c) for c in d.get("cycles", [])]
    return out


def _redact_cycle(c: dict) -> dict:
    cc = dict(c)
    for key in ("instruction_before", "instruction_after"):
        if cc.get(key):
            cc[key] = f"<{len(cc[key])} chars; pass --include-instructions to see>"
    edit = cc.get("instruction_edit")
    if edit:
        cc["instruction_edit"] = {
            "cluster_label": edit.get("cluster_label"),
            "rationale": edit.get("rationale"),
            "appended_text_chars": len(edit.get("appended_text") or ""),
        }
    return cc


async def main_async(args: CliArgs) -> int:
    spec = FIXTURES[args.workflow]
    plan = SIM_PLAN_FIXTURES[args.workflow]
    case_set = EVAL_CASE_SET_FIXTURES[args.workflow]
    metric = METRIC_FIXTURES[args.workflow]

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=args.api_key, base_url=args.base_url)

    print(
        f"loop: workflow={args.workflow} cycles={args.cycles} "
        f"agent_model={args.agent_model} proposer_model={args.proposer_model} "
        f"metric={metric.family}@{metric.target_value:.2f}",
        file=sys.stderr,
    )

    # `--progress` attaches a stderr handler to the loop logger so each
    # `cycle N/M: metric=...` info line streams as the cycle ends. The
    # JSON dump on stdout is unaffected — machine-parsable runs that
    # don't pass --progress still get a single stdout document.
    if args.progress:
        loop_logger = logging.getLogger("ownevo_kernel.nl_gen.loop")
        loop_logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        loop_logger.addHandler(handler)

    report = await run_nl_gen_demo_loop(
        spec=spec,
        plan=plan,
        case_set=case_set,
        metric=metric,
        client=client,
        n_cycles=args.cycles,
        agent_model=args.agent_model,
        proposer_model=args.proposer_model,
        proposer_max_tokens=args.proposer_max_tokens,
    )

    payload = _redact_instructions_in_dict(
        report.to_dict(),
        include=args.include_instructions,
    )
    print(json.dumps(payload, indent=2 if args.pretty else None))

    failures = _check_gates(args, report)
    if failures:
        for line in failures:
            print(f"error: {line}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
