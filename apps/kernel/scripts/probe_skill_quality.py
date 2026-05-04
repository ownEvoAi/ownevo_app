"""probe_skill_quality — does the model produce structurally valid skill files?

Sends the M5 predictor.py to the model with a focused 1-line modification
request, parses the response, and checks:

  AST parses                         → catches em-dashes / smart-quotes in CODE
                                       positions (Ollama qwen3:30b-a3b, Qwq:32b
                                       both fail this)
  YAML frontmatter `id:` line intact → catches models that drop the docstring
                                       header when rewriting (qwen3.5:27b/35b)
  `def predict(model, features, fold)` signature intact
                                     → catches models that change the function
                                       contract the gate runner depends on
  modification (clip floor 0.5) is present
                                     → smoke check that the model actually
                                       made the requested change

What this CANNOT tell you (don't over-read it):
  * Whether the model can drive the full M5 loop (8B-class models per F4 in
    docs/local-model-testing.md pass this probe but stall in the read-loop)
  * Whether the model would call the right TOOL — this is a "rewrite the
    file" prompt that bypasses the agent's tool surface entirely. It's a
    proxy for codegen, not a real workflow.
  * Whether semantic understanding is correct — we only check that 0.5
    appears somewhere; we don't run the modified code

Backends, env vars, exit codes mirror probe_tool_calling.py.

  0  pass — all checks succeeded
  1  fail — at least one check failed (issues listed in the JSON output)
  2  http/transport error
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ENV_LLM_BASE_URL = "OWNEVO_LLM_BASE_URL"
ENV_LLM_MODEL = "OWNEVO_LLM_MODEL"
ENV_LLM_API_KEY = "OWNEVO_LLM_API_KEY"
ENV_LLM_API_FORMAT = "OWNEVO_LLM_API_FORMAT"
ENV_LLM_HOST = "OWNEVO_LLM_HOST"

_DEFAULT_LLM_HOST = "localhost"

# Default skill we ask the model to rewrite. Lives in this repo so the
# probe is self-contained — no DB or workflow setup required.
_DEFAULT_SKILL = (
    Path(__file__).resolve().parents[1]
    / "baselines" / "m5_lightgbm" / "skill_v1" / "predictor.py"
)

SYSTEM = (
    "You are an agent improving an M5 demand-forecasting pipeline. You will be "
    "given a Python skill file. Make ONE focused change and return the COMPLETE "
    "modified file. Rules:\n"
    "1. Preserve the YAML frontmatter exactly — including the `id:` line.\n"
    "2. Preserve the `def predict(model, features, fold)` function signature.\n"
    "3. Use only ASCII characters in code (no em-dash, no smart quotes).\n"
    "4. Return ONLY the file contents — no commentary, no markdown fences.\n"
)

USER_TEMPLATE = (
    "Here is the current skill file:\n\n"
    "```python\n{skill}\n```\n\n"
    "Change the `np.clip(..., 0.0, None)` floor from `0.0` to `0.5` "
    "(small change to bias predictions slightly upward — sales are "
    "rarely zero). Return the complete modified file."
)

CODE_FENCE_RE = re.compile(r"^```(?:python|py)?\s*\n(.*?)\n```\s*$", re.DOTALL)
SIGNATURE_RE = re.compile(
    r"def\s+predict\s*\(\s*model[^,]*,\s*features[^,]*,\s*fold[^)]*\)",
    re.DOTALL,
)
FRONTMATTER_ID_RE = re.compile(
    r"^id:\s*m5\.baseline\.v1\.predictor\s*$", re.MULTILINE,
)
BAD_CHARS = {
    "–": "EN-DASH", "—": "EM-DASH",
    "‘": "LEFT-SINGLE-QUOTE", "’": "RIGHT-SINGLE-QUOTE",
    "“": "LEFT-DOUBLE-QUOTE", "”": "RIGHT-DOUBLE-QUOTE",
    " ": "NBSP",
}


def _resolve_base_url(api_format: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    if env := os.environ.get(ENV_LLM_BASE_URL):
        return env
    host = os.environ.get(ENV_LLM_HOST, _DEFAULT_LLM_HOST)
    if api_format == "openai":
        return f"http://{host}:11434/v1"
    return f"http://{host}:1234"


def strip_fences(text: str) -> str:
    text = text.strip()
    m = CODE_FENCE_RE.match(text)
    if m:
        return m.group(1)
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def validate(generated: str) -> list[str]:
    """Return list of failed checks (empty list = pass)."""
    issues: list[str] = []

    # 1. AST parse — catches em-dash/smart-quote when in CODE position.
    parse_ok = True
    try:
        ast.parse(generated)
    except SyntaxError as e:
        parse_ok = False
        issues.append(f"syntax-error: {e.msg} (line {e.lineno})")

    # 2. If parse failed AND there are bad chars, surface them as the cause.
    #    (Bad chars inside docstrings/strings are fine if they parse — the
    #    source itself has 1 em-dash in a docstring. We only flag bad chars
    #    when they're a syntax-breaking issue.)
    if not parse_ok:
        for ch, label in BAD_CHARS.items():
            n = generated.count(ch)
            if n:
                issues.append(f"contains-{label.lower()} ({n}x)")

    # 3. Frontmatter id intact.
    if not FRONTMATTER_ID_RE.search(generated):
        issues.append("frontmatter-id-missing-or-changed")

    # 4. Function signature intact.
    if not SIGNATURE_RE.search(generated):
        issues.append("predict-signature-broken")

    # 5. Modification present.
    if "0.5" not in generated:
        issues.append("modification-not-applied (no 0.5)")

    return issues


@dataclass
class ProbeResult:
    api_format: str
    base_url: str
    model: str
    elapsed_s: float
    result: str  # "pass" | "fail" | "error"
    issues: list[str] = field(default_factory=list)
    raw_len: int = 0
    code_len: int = 0
    error: str | None = None
    code_preview: str | None = None  # only set when result != "pass"


async def probe_openai(model: str, base_url: str, api_key: str,
                       skill_text: str, timeout: float,
                       ollama_num_ctx: int | None) -> ProbeResult:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    create_kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER_TEMPLATE.format(skill=skill_text)},
        ],
        "stream": False,
        "max_tokens": 4096,
        "temperature": 0.2,
    }
    if ollama_num_ctx is not None:
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
    raw = resp.choices[0].message.content or ""
    code = strip_fences(raw)
    issues = validate(code)
    return ProbeResult(
        api_format="openai", base_url=base_url, model=model,
        elapsed_s=elapsed, raw_len=len(raw), code_len=len(code),
        result="pass" if not issues else "fail",
        issues=issues,
        code_preview=None if not issues else code[:300],
    )


async def probe_anthropic(model: str, base_url: str, api_key: str,
                          skill_text: str, timeout: float) -> ProbeResult:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key, base_url=base_url)
    started = time.time()
    try:
        msg = await asyncio.wait_for(
            client.messages.create(
                model=model,
                max_tokens=4096,
                system=SYSTEM,
                messages=[{
                    "role": "user",
                    "content": USER_TEMPLATE.format(skill=skill_text),
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
    raw = "".join(
        b.text for b in msg.content if getattr(b, "type", None) == "text"
    )
    code = strip_fences(raw)
    issues = validate(code)
    return ProbeResult(
        api_format="anthropic", base_url=base_url, model=model,
        elapsed_s=elapsed, raw_len=len(raw), code_len=len(code),
        result="pass" if not issues else "fail",
        issues=issues,
        code_preview=None if not issues else code[:300],
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="probe_skill_quality",
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
    p.add_argument(
        "--skill-path", type=Path, default=_DEFAULT_SKILL,
        help=f"Skill file to ask the model to rewrite. Default: {_DEFAULT_SKILL}",
    )
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument(
        "--ollama-num-ctx", type=int, default=None,
        help="Forwarded as extra_body.options.num_ctx (Ollama only).",
    )
    return p.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    if not args.llm_model:
        print("error: --llm-model is required (or set $OWNEVO_LLM_MODEL)",
              file=sys.stderr)
        return 2
    if not args.skill_path.exists():
        print(f"error: skill file not found: {args.skill_path}", file=sys.stderr)
        return 2

    skill_text = args.skill_path.read_text()
    base_url = _resolve_base_url(args.api_format, args.llm_base_url)

    if args.api_format == "openai":
        result = await probe_openai(
            model=args.llm_model, base_url=base_url, api_key=args.llm_api_key,
            skill_text=skill_text, timeout=args.timeout,
            ollama_num_ctx=args.ollama_num_ctx,
        )
    else:
        result = await probe_anthropic(
            model=args.llm_model, base_url=base_url, api_key=args.llm_api_key,
            skill_text=skill_text, timeout=args.timeout,
        )

    print(json.dumps(result.__dict__))
    if result.result == "pass":
        return 0
    if result.result == "fail":
        return 1
    return 2


def main() -> int:
    args = parse_args(sys.argv[1:])
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
