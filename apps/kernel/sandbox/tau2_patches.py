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
from pathlib import Path


def _ensure_writable_simulations_dir() -> None:
    """Create the tmpfs target the image-baked simulations symlink points at.

    Dockerfile.tau3 symlinks /tau2_data/simulations → /tmp/tau3_sims so
    that tau2's `run_domain` can write per-run results.json under a
    --read-only rootfs. The /tmp/tau3_sims directory itself doesn't
    exist at container start (tmpfs is fresh per run); we create it
    before tau2 imports so the symlink resolves to a real dir."""
    Path("/tmp/tau3_sims").mkdir(parents=True, exist_ok=True)


def _patch_nl_evaluator_resilience() -> None:
    """Tolerate non-JSON content from the NL-assertions evaluator LLM.

    ``tau2.evaluator.evaluator_nl_assertions.evaluate_nl_assertions``
    calls ``json.loads(assistant_message.content)`` on whatever the
    evaluator LLM (Sonnet by default in our config) returns. The model
    sometimes wraps its JSON in ```json markdown fences, prepends prose,
    or returns an empty string. tau2 retries 4× with the same prompt,
    same deterministic crash, then surfaces it as
    TerminationReason.INFRASTRUCTURE_ERROR.

    Observed at 4/40 (10%) on the retail-test split with concurrency=3
    on 2026-05-08, persistent across the patched and unpatched substrate.
    Worth upstreaming as a tau2 issue. Until then we shim the module's
    ``json.loads`` to:
      1. accept empty strings as ``{}``;
      2. strip ```json/``` fences before retrying;
      3. extract the first {...} block as a fallback;
      4. give up only if all of the above fail.
    """
    try:
        import tau2.evaluator.evaluator_nl_assertions as _nl  # type: ignore[import-not-found]
    except ImportError:
        return
    if getattr(_nl, "_ownevo_nl_resilience_applied", False):
        return
    import json as _json
    import re as _re

    _fence_re = _re.compile(r"```(?:json)?\s*(.*?)```", _re.DOTALL)
    _braces_re = _re.compile(r"\{.*?\}", _re.DOTALL)

    class _ResilientJsonShim:
        def __getattr__(self, name: str):
            return getattr(_json, name)

        @staticmethod
        def loads(s, *args, **kwargs):
            if isinstance(s, (str, bytes, bytearray)) and not s:
                return {}
            if isinstance(s, str):
                try:
                    return _json.loads(s, *args, **kwargs)
                except _json.JSONDecodeError:
                    pass
                m = _fence_re.search(s)
                if m:
                    try:
                        return _json.loads(m.group(1), *args, **kwargs)
                    except _json.JSONDecodeError:
                        pass
                m = _braces_re.search(s)
                if m:
                    return _json.loads(m.group(0), *args, **kwargs)
            return _json.loads(s, *args, **kwargs)

    _nl.json = _ResilientJsonShim()  # type: ignore[attr-defined]
    _nl._ownevo_nl_resilience_applied = True  # type: ignore[attr-defined]


def _patch_tool_call_args_resilience() -> None:
    """Tolerate empty-string tool_call arguments from LLM responses.

    tau2.utils.llm_utils.generate calls
    ``json.loads(tool_call.function.arguments)`` on every tool call
    coming back from LiteLLM. Both Sonnet and Haiku occasionally emit
    `arguments=""` when a tool takes no parameters, which raises
    ``JSONDecodeError: Expecting value: line 1 column 1 (char 0)``.
    tau2's run_with_retry then retries 4 times — same model, same
    prompt, deterministic crash — and surfaces it as
    TerminationReason.INFRASTRUCTURE_ERROR.

    Observed at 7/40 (17.5%) on the retail-test split with concurrency=3
    on 2026-05-08. Worth upstreaming as a tau2 hardening issue; until
    then we wrap json.loads at module import. Idempotent.
    """
    try:
        import tau2.utils.llm_utils as _llm  # type: ignore[import-not-found]
    except ImportError:
        return
    if getattr(_llm, "_ownevo_args_patch_applied", False):
        return
    import json as _json

    class _SafeJsonShim:
        """Drop-in replacement for the json module inside _llm.

        Forwards every attribute except `loads`, which coerces
        empty-string input to {}. Keeps the rest of the sandbox's
        json behavior untouched — only this one module sees it.
        """

        def __getattr__(self, name: str):
            return getattr(_json, name)

        @staticmethod
        def loads(s, *args, **kwargs):
            if isinstance(s, (str, bytes, bytearray)) and not s:
                return {}
            return _json.loads(s, *args, **kwargs)

    _llm.json = _SafeJsonShim()  # type: ignore[attr-defined]
    _llm._ownevo_args_patch_applied = True  # type: ignore[attr-defined]


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


_ensure_writable_simulations_dir()
try:
    _patch_tau2_defaults()
except Exception as _exc:  # noqa: BLE001
    import sys as _sys
    _sys.stderr.write(f"[sitecustomize] _patch_tau2_defaults failed: {_exc}\n")
try:
    _patch_tool_call_args_resilience()
except Exception as _exc:  # noqa: BLE001
    import sys as _sys
    _sys.stderr.write(f"[sitecustomize] _patch_tool_call_args_resilience failed: {_exc}\n")
try:
    _patch_nl_evaluator_resilience()
except Exception as _exc:  # noqa: BLE001
    import sys as _sys
    _sys.stderr.write(f"[sitecustomize] _patch_nl_evaluator_resilience failed: {_exc}\n")
