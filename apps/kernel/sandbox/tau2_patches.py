"""Sandbox-boot monkey-patches for tau2 (Sierra tau2-bench).

Two LLM call sites in tau2 are NOT exposed via TextRunConfig and default
to hardcoded ``"gpt-4.1-2025-04-14"``:

- ``tau2.evaluator.evaluator_nl_assertions.DEFAULT_LLM_NL_ASSERTIONS`` —
  scores tasks at end of conversation by checking natural-language
  assertions against the trajectory.
- ``tau2.environment.utils.interface_agent.DEFAULT_LLM_ENV_INTERFACE`` —
  helper LLM used by some environment tools.

Both look up their constants by name at call time, so reassigning the
module-globals redirects the call. Without this redirect a τ³ run
configured for `ollama_chat/qwen3-coder:30b` or `anthropic/claude-sonnet-4-6`
still makes openai-provider calls for the post-conversation evaluator,
which (a) leaks cost to a different account and (b) fails outright in
sandboxes whose egress allowlist doesn't include OpenAI.

Discovered during the τ³ test plan's sanity-A run on 2026-05-08 — see
`docs/TAU3_LOCAL_TESTPLAN.md` § P0.4 for the full diagnostic story.
**Worth upstreaming as a tau2 issue** — these defaults should be
configurable via TextRunConfig.

Mechanism: this file is installed as ``sitecustomize.py`` in the
sandbox image's site-packages, so it runs once at every Python startup
inside the container. The patch is idempotent (re-applying is a no-op)
and silent on import failure (kernel unit tests that import this file
without tau2 installed must not crash).
"""

from __future__ import annotations

import os


def _patch_tau2_defaults() -> None:
    target = os.environ.get("AGENT_MODEL")
    if not target:
        # No override requested — leave tau2's defaults alone. This
        # path runs in dev shells / CI sanity tests where AGENT_MODEL
        # isn't set; the patches only fire when a benchmark run
        # actually wires AGENT_MODEL via the sandbox env.
        return

    try:
        import tau2.config as _config  # type: ignore[import-not-found]
        import tau2.evaluator.evaluator_nl_assertions as _nl_eval  # type: ignore[import-not-found]
        import tau2.environment.utils.interface_agent as _env_iface  # type: ignore[import-not-found]
    except ImportError:
        # tau2 isn't on the path. This happens during kernel unit
        # tests — silently no-op so importing this file from a host
        # interpreter doesn't fail.
        return

    _config.DEFAULT_LLM_NL_ASSERTIONS = target
    _config.DEFAULT_LLM_ENV_INTERFACE = target
    _nl_eval.DEFAULT_LLM_NL_ASSERTIONS = target
    _env_iface.DEFAULT_LLM_ENV_INTERFACE = target


_patch_tau2_defaults()
