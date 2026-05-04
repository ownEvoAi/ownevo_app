"""probe_tool_calling — quick tool-call sanity check for a local model.

This is the cheapest filter in the local-model evaluation funnel: send one
turn with one tool, verify the model emits a `tool_calls` response. If a
model can't pass this, it has no chance in the full M5 improvement loop.

What this CANNOT tell you (don't over-read it):
  * Whether the model handles long system prompts (use the M5 loop for that)
  * Whether the model survives multi-turn dialogue (F4 in
    docs/local-model-testing.md — 8B models stall in the read-loop)
  * Whether generated code is structurally valid (use probe_skill_quality)

Backends supported (mirrors run_improvement_loop.py):
  --api-format anthropic   AsyncAnthropic + /v1/messages    (LM Studio default)
  --api-format openai      AsyncOpenAI    + /v1/chat/completions  (Ollama / vLLM)

URL resolution mirrors the loop runner:
  --llm-base-url               explicit override
  $OWNEVO_LLM_BASE_URL         per-call override
  $OWNEVO_LLM_HOST + format    host:1234 (lms) / host:11434/v1 (ollama)
  default host:                localhost  (set $OWNEVO_LLM_HOST for a
                               remote desktop / LAN box)

Exit codes:
  0  pass — model emitted a tool_calls response
  1  fail — model responded but did not call the tool (`fail-no-tool`)
  2  http/transport error (model rejected, server timed out, etc.)

Outputs a single JSON line on stdout (whether pass or fail) so the
script is loop-friendly:

    for m in qwen3:8b granite4.1:30b; do
      uv run --extra agent python apps/kernel/scripts/probe_tool_calling.py \
        --api-format openai --llm-model "$m"
    done | jq -c '{model,result,elapsed_s,finish_reason}'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass

ENV_LLM_BASE_URL = "OWNEVO_LLM_BASE_URL"
ENV_LLM_MODEL = "OWNEVO_LLM_MODEL"
ENV_LLM_API_KEY = "OWNEVO_LLM_API_KEY"
ENV_LLM_API_FORMAT = "OWNEVO_LLM_API_FORMAT"
ENV_LLM_HOST = "OWNEVO_LLM_HOST"

_DEFAULT_LLM_HOST = "localhost"


def _resolve_base_url(api_format: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    if env := os.environ.get(ENV_LLM_BASE_URL):
        return env
    host = os.environ.get(ENV_LLM_HOST, _DEFAULT_LLM_HOST)
    if api_format == "openai":
        return f"http://{host}:11434/v1"
    return f"http://{host}:1234"


# Single tool, simple schema — every tool-calling model should handle this.
PROBE_PROMPT = (
    "Call the read_skill tool with skill_id='m5.predictor'. "
    "Use the tool — do not respond in plain text."
)
PROBE_TOOL_NAME = "read_skill"
PROBE_TOOL_DESC = "Read a skill from the registry by id"
PROBE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill_id": {"type": "string", "description": "the skill id"},
    },
    "required": ["skill_id"],
}


@dataclass
class ProbeResult:
    api_format: str
    base_url: str
    model: str
    elapsed_s: float
    result: str  # "pass" | "fail-no-tool" | "error"
    finish_reason: str | None = None
    n_tool_calls: int = 0
    tool_args: str | None = None
    error: str | None = None
    content_preview: str | None = None


async def probe_openai(model: str, base_url: str, api_key: str,
                       timeout: float, ollama_num_ctx: int | None) -> ProbeResult:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    create_kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": PROBE_PROMPT}],
        "tools": [{
            "type": "function",
            "function": {
                "name": PROBE_TOOL_NAME,
                "description": PROBE_TOOL_DESC,
                "parameters": PROBE_TOOL_SCHEMA,
            },
        }],
        "tool_choice": "auto",
        "stream": False,
        "max_tokens": 256,
    }
    if ollama_num_ctx is not None:
        # Pass through extra_body — Ollama honors options.num_ctx,
        # other OpenAI-compatible backends ignore harmlessly. Same idiom
        # as run_improvement_loop's --ollama-num-ctx (PR #24).
        create_kwargs["extra_body"] = {"options": {"num_ctx": ollama_num_ctx}}

    started = time.time()
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(**create_kwargs), timeout=timeout
        )
    except Exception as e:
        return ProbeResult(
            api_format="openai", base_url=base_url, model=model,
            elapsed_s=round(time.time() - started, 1),
            result="error", error=f"{type(e).__name__}: {e}",
        )
    elapsed = round(time.time() - started, 1)

    choice = resp.choices[0]
    finish = choice.finish_reason
    tool_calls = choice.message.tool_calls or []
    if tool_calls and finish == "tool_calls":
        tc = tool_calls[0]
        return ProbeResult(
            api_format="openai", base_url=base_url, model=model,
            elapsed_s=elapsed, result="pass",
            finish_reason=finish, n_tool_calls=len(tool_calls),
            tool_args=tc.function.arguments,
        )
    return ProbeResult(
        api_format="openai", base_url=base_url, model=model,
        elapsed_s=elapsed, result="fail-no-tool",
        finish_reason=finish, n_tool_calls=len(tool_calls),
        content_preview=(choice.message.content or "")[:200],
    )


async def probe_anthropic(model: str, base_url: str, api_key: str,
                          timeout: float) -> ProbeResult:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key, base_url=base_url)
    started = time.time()
    try:
        msg = await asyncio.wait_for(
            client.messages.create(
                model=model,
                max_tokens=256,
                messages=[{"role": "user", "content": PROBE_PROMPT}],
                tools=[{
                    "name": PROBE_TOOL_NAME,
                    "description": PROBE_TOOL_DESC,
                    "input_schema": PROBE_TOOL_SCHEMA,
                }],
            ),
            timeout=timeout,
        )
    except Exception as e:
        return ProbeResult(
            api_format="anthropic", base_url=base_url, model=model,
            elapsed_s=round(time.time() - started, 1),
            result="error", error=f"{type(e).__name__}: {e}",
        )
    elapsed = round(time.time() - started, 1)

    tool_blocks = [b for b in msg.content if getattr(b, "type", None) == "tool_use"]
    text_blocks = [b for b in msg.content if getattr(b, "type", None) == "text"]
    if tool_blocks and msg.stop_reason == "tool_use":
        return ProbeResult(
            api_format="anthropic", base_url=base_url, model=model,
            elapsed_s=elapsed, result="pass",
            finish_reason=msg.stop_reason, n_tool_calls=len(tool_blocks),
            tool_args=json.dumps(tool_blocks[0].input),
        )
    return ProbeResult(
        api_format="anthropic", base_url=base_url, model=model,
        elapsed_s=elapsed, result="fail-no-tool",
        finish_reason=msg.stop_reason, n_tool_calls=len(tool_blocks),
        content_preview=(text_blocks[0].text if text_blocks else "")[:200],
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="probe_tool_calling",
        description=__doc__.split("\n\n")[0],
    )
    p.add_argument(
        "--api-format", choices=["anthropic", "openai"],
        default=os.environ.get(ENV_LLM_API_FORMAT, "openai"),
        help=f"Default: ${ENV_LLM_API_FORMAT} or 'openai' (Ollama).",
    )
    p.add_argument("--llm-base-url", default=None)
    p.add_argument(
        "--llm-model",
        default=os.environ.get(ENV_LLM_MODEL),
        help=f"Required if ${ENV_LLM_MODEL} is unset.",
    )
    p.add_argument(
        "--llm-api-key",
        default=os.environ.get(ENV_LLM_API_KEY, "lm-studio"),
    )
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument(
        "--ollama-num-ctx", type=int, default=None,
        help=("Forwarded as extra_body.options.num_ctx (Ollama only). "
              "Bypasses Ollama's silent 2048-token cap (see PR #24)."),
    )
    return p.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    if not args.llm_model:
        print("error: --llm-model is required (or set $OWNEVO_LLM_MODEL)",
              file=sys.stderr)
        return 2

    base_url = _resolve_base_url(args.api_format, args.llm_base_url)
    if args.api_format == "openai":
        result = await probe_openai(
            model=args.llm_model, base_url=base_url, api_key=args.llm_api_key,
            timeout=args.timeout, ollama_num_ctx=args.ollama_num_ctx,
        )
    else:
        result = await probe_anthropic(
            model=args.llm_model, base_url=base_url, api_key=args.llm_api_key,
            timeout=args.timeout,
        )

    print(json.dumps(result.__dict__))
    if result.result == "pass":
        return 0
    if result.result == "fail-no-tool":
        return 1
    return 2


def main() -> int:
    args = parse_args(sys.argv[1:])
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
