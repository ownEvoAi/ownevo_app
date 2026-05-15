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

Discovered during a τ³ sanity run on 2026-05-08.
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
import sys as _sys
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
                # Empty evaluator response — log so infra_diag surfaces it.
                # Returning {} lets tau2 continue rather than retrying 4× and
                # raising INFRASTRUCTURE_ERROR; tau2 will score this task
                # against an empty assertions list (typically → reward=1.0 by
                # convention). The trade-off is accepted: 4 deterministic
                # infra-errors vs a possible false-pass on those tasks.
                _sys.stderr.write(
                    "[sitecustomize] NL evaluator returned empty string; "
                    "returning {} — task reward may be inflated\n"
                )
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
                    try:
                        return _json.loads(m.group(0), *args, **kwargs)
                    except _json.JSONDecodeError:
                        pass
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


def _patch_litellm_lms_think_off() -> None:
    """Inject ``chat_template_kwargs.enable_thinking=False`` for qwen3-family
    models on the LM Studio Anthropic-format provider.

    LM Studio serves qwen3 base models (e.g. ``qwen/qwen3-32b``,
    ``qwen/qwen3-14b``) with thinking mode ON by default when loaded without a
    custom Jinja template.  The thinking suppression that qwen3.5/qwen3.6
    models get via LMS's "v13 froggeric" template is NOT auto-applied to
    qwen3 base.  Without suppression the model emits extended ``<think>``
    chains, producing poor retail answers (avg ≈ 0.24 observed 2026-05-13).

    LM Studio accepts ``chat_template_kwargs`` as an extension field in the
    Anthropic-format request body.  LiteLLM forwards ``extra_body`` contents
    verbatim to the underlying HTTP request for the ``anthropic/`` provider,
    so injecting it here is equivalent to toggling the LMS UI control.

    Approach: monkey-patch ``litellm.completion``/``acompletion`` to inject
    ``extra_body={"chat_template_kwargs": {"enable_thinking": False}}`` when:
      - the model name starts with ``anthropic/``, AND
      - the model name contains ``qwen3`` (case-insensitive), AND
      - the model name does NOT start with ``anthropic/claude`` (real Anthropic
        cloud models don't support this field; leave them untouched).

    Existing ``extra_body`` keys are preserved; only
    ``chat_template_kwargs.enable_thinking`` is forced to False.

    Idempotent (re-applying is a no-op).
    """
    try:
        import litellm  # type: ignore[import-not-found]
    except ImportError:
        return
    if getattr(litellm, "_ownevo_lms_think_off_applied", False):
        return

    def _maybe_inject_lms_think(call_kwargs: dict) -> None:
        model = call_kwargs.get("model", "") or ""
        if not isinstance(model, str):
            return
        if not model.startswith("anthropic/"):
            return
        if model.startswith("anthropic/claude"):
            return
        if "qwen3" not in model.lower():
            return
        existing_extra = call_kwargs.get("extra_body")
        if isinstance(existing_extra, dict):
            ctk = dict(existing_extra.get("chat_template_kwargs") or {})
            ctk["enable_thinking"] = False
            call_kwargs["extra_body"] = {**existing_extra, "chat_template_kwargs": ctk}
        else:
            call_kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

    _orig_completion = litellm.completion
    _orig_acompletion = litellm.acompletion

    def _patched_completion(*args, **kwargs):  # type: ignore[no-untyped-def]
        _maybe_inject_lms_think(kwargs)
        return _orig_completion(*args, **kwargs)

    async def _patched_acompletion(*args, **kwargs):  # type: ignore[no-untyped-def]
        _maybe_inject_lms_think(kwargs)
        return await _orig_acompletion(*args, **kwargs)

    litellm.completion = _patched_completion  # type: ignore[assignment]
    litellm.acompletion = _patched_acompletion  # type: ignore[assignment]
    litellm._ownevo_lms_think_off_applied = True  # type: ignore[attr-defined]


def _patch_litellm_ollama_think_off() -> None:
    """Inject ``options.think=False`` for qwen3-family models on ollama_chat.

    LiteLLM's ``ollama_chat`` provider does NOT auto-strip thinking like
    our ``OllamaChatClient`` does — only the kernel-side runner has that
    plumbing. So when the τ³ task agent (running inside the sandbox via
    LiteLLM, not OllamaChatClient) is configured as
    ``ollama_chat/qwen3.6:35b-a3b``, the model generates indefinite
    thinking traces and hangs at Ollama's ~10-min ``/api/chat`` internal
    inference timeout. tau2 then retries the same prompt, hangs again,
    surfaces ``TerminationReason.INFRASTRUCTURE_ERROR`` after exhausting
    its retry budget. End result: 0/40 retail tasks evaluate.

    Observed 2026-05-10 in ``qwen36ollama_native_smoke3``: every single
    ``POST /api/chat`` returned ``500 | 10m0s`` for 2+ hours straight,
    seen via ``docker logs ollama``. ``/no_think`` system-prompt
    directive does NOT work on qwen3.5/qwen3.6 lineage (per F14g) — the
    only reliable suppression path is ``options.think=false`` in the
    Ollama request payload.

    Approach: monkey-patch ``litellm.completion``/``acompletion`` at the
    entry point to inject ``options={"think": False}`` (preserving any
    existing options dict) when:
      - the model name starts with ``ollama_chat/`` or ``ollama/``, AND
      - the model name contains ``qwen3`` (case-insensitive).

    Idempotent (re-applying is a no-op).
    """
    try:
        import litellm  # type: ignore[import-not-found]
    except ImportError:
        return
    if getattr(litellm, "_ownevo_ollama_think_off_applied", False):
        return

    def _maybe_inject_think(call_kwargs: dict) -> None:
        model = call_kwargs.get("model", "") or ""
        if not isinstance(model, str):
            return
        if not (model.startswith("ollama_chat/") or model.startswith("ollama/")):
            return
        if "qwen3" not in model.lower():
            return
        existing = call_kwargs.get("options")
        merged: dict = {"think": False}
        if isinstance(existing, dict):
            merged = {**existing, "think": False}
        call_kwargs["options"] = merged

    _orig_completion = litellm.completion
    _orig_acompletion = litellm.acompletion

    def _patched_completion(*args, **kwargs):  # type: ignore[no-untyped-def]
        _maybe_inject_think(kwargs)
        return _orig_completion(*args, **kwargs)

    async def _patched_acompletion(*args, **kwargs):  # type: ignore[no-untyped-def]
        _maybe_inject_think(kwargs)
        return await _orig_acompletion(*args, **kwargs)

    litellm.completion = _patched_completion  # type: ignore[assignment]
    litellm.acompletion = _patched_acompletion  # type: ignore[assignment]
    litellm._ownevo_ollama_think_off_applied = True  # type: ignore[attr-defined]


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


try:
    _ensure_writable_simulations_dir()
except Exception as _exc:  # noqa: BLE001
    _sys.stderr.write(f"[sitecustomize] _ensure_writable_simulations_dir failed: {_exc}\n")
try:
    _patch_tau2_defaults()
except Exception as _exc:  # noqa: BLE001
    _sys.stderr.write(f"[sitecustomize] _patch_tau2_defaults failed: {_exc}\n")
try:
    _patch_tool_call_args_resilience()
except Exception as _exc:  # noqa: BLE001
    _sys.stderr.write(f"[sitecustomize] _patch_tool_call_args_resilience failed: {_exc}\n")
try:
    _patch_nl_evaluator_resilience()
except Exception as _exc:  # noqa: BLE001
    _sys.stderr.write(f"[sitecustomize] _patch_nl_evaluator_resilience failed: {_exc}\n")
try:
    _patch_litellm_lms_think_off()
except Exception as _exc:  # noqa: BLE001
    _sys.stderr.write(f"[sitecustomize] _patch_litellm_lms_think_off failed: {_exc}\n")
try:
    _patch_litellm_ollama_think_off()
except Exception as _exc:  # noqa: BLE001
    _sys.stderr.write(f"[sitecustomize] _patch_litellm_ollama_think_off failed: {_exc}\n")
