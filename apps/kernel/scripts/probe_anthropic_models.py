"""Probe which Anthropic models a given API key can call.

Sends a minimal one-token "say hi" message to each model in the list
and reports OK / FAIL with the error class.

Usage:
    ANTHROPIC_API_KEY=sk-... python scripts/probe_anthropic_models.py
    python scripts/probe_anthropic_models.py --model claude-opus-4-7 \\
                                             --model claude-sonnet-4-5

Exit 0 iff every probed model returned 200; 1 if any failed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass

DEFAULT_MODELS = [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
]


@dataclass(frozen=True)
class ProbeResult:
    model: str
    ok: bool
    detail: str
    elapsed_ms: int


async def _probe_one(client, model: str) -> ProbeResult:
    started = time.perf_counter()
    try:
        msg = await client.messages.create(
            model=model,
            max_tokens=8,
            messages=[{"role": "user", "content": "Reply with the single word 'ok'."}],
        )
        elapsed = int((time.perf_counter() - started) * 1000)
        text = "".join(
            getattr(b, "text", "") for b in msg.content if getattr(b, "type", None) == "text"
        )
        return ProbeResult(
            model=model,
            ok=True,
            detail=f"stop={msg.stop_reason!r} text={text[:40]!r}",
            elapsed_ms=elapsed,
        )
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        cls = type(exc).__name__
        msg_text = str(exc)[:200]
        return ProbeResult(
            model=model,
            ok=False,
            detail=f"{cls}: {msg_text}",
            elapsed_ms=elapsed,
        )


async def _async_main(models: list[str]) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is unset — set it before probing.",
            file=sys.stderr,
        )
        return 2

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic()

    print(f"{'MODEL':<40} {'STATUS':<6} {'MS':>6}  DETAIL")
    print("-" * 90)

    all_ok = True
    for model in models:
        result = await _probe_one(client, model)
        if not result.ok:
            all_ok = False
        status = "OK" if result.ok else "FAIL"
        print(
            f"{result.model:<40} {status:<6} {result.elapsed_ms:>6}  {result.detail}"
        )

    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="probe-anthropic-models")
    parser.add_argument(
        "--model",
        action="append",
        help=(
            "Model id to probe. May be passed multiple times. Defaults to "
            "haiku 4.5 + sonnet 4.6 + opus 4.7."
        ),
    )
    ns = parser.parse_args(argv)
    models = ns.model if ns.model else DEFAULT_MODELS
    return asyncio.run(_async_main(models))


if __name__ == "__main__":
    sys.exit(main())
