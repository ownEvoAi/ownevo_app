"""Runtime dispatch from `workflow.agent_model_id` to a usable chat client.

`providers.py` owns the allowlist (which provider+model pairs the
operator has enabled); this module owns the dispatch table that maps
the chosen slug to an actual SDK client. Two surfaces consume the
result:

  * The agent solver (`eval_runner/agent_solver.py`) — either an
    `AsyncAnthropic` client (Anthropic provider) or any
    `AsyncOpenAI`-shaped duck-type (every other provider). The solver
    already accepts both via its `client` / `openai_client` parameters.
  * The iteration runner (`iteration_runner.py`) — picks the workflow's
    stored slug, calls `build_chat_client`, and threads the resulting
    handle into `run_nl_gen_demo_loop`.

Boot-time checks: `check_provider_api_keys` warns the operator at API
startup when an enabled provider has no API key set. Combined with the
runtime `RouterError` raised on a disabled-provider slug, this keeps
the picker UI honest — a workflow can't point at a model the operator
hasn't actually wired up.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .providers import enabled_providers, parse_slug

if TYPE_CHECKING:  # pragma: no cover - type-only imports
    from anthropic import AsyncAnthropic


_logger = logging.getLogger(__name__)


# Per-provider routing record. The `api_key_env` is the env var the
# provider's SDK pulls credentials from; `base_url` is the OpenAI-compat
# endpoint the SDK should target (None means "use the SDK default" —
# anthropic + openai cloud).
@dataclass(frozen=True)
class _ProviderRoute:
    api_key_env: str
    base_url: str | None


# Provider id → routing record. Kept in lock-step with `providers.PROVIDERS`.
# OpenAI-compat providers all use AsyncOpenAI with a provider-specific
# base_url; Ollama is the exception (routes through OllamaChatClient so
# `options.think=false` reaches qwen3-family models — see ollama_native.py).
_ROUTES: dict[str, _ProviderRoute] = {
    "anthropic": _ProviderRoute(
        api_key_env="ANTHROPIC_API_KEY",
        base_url=None,  # AsyncAnthropic honours ANTHROPIC_BASE_URL itself.
    ),
    "openai": _ProviderRoute(
        api_key_env="OPENAI_API_KEY",
        base_url=None,  # AsyncOpenAI default endpoint.
    ),
    "xai": _ProviderRoute(
        api_key_env="XAI_API_KEY",
        base_url="https://api.x.ai/v1",
    ),
    "gemini": _ProviderRoute(
        api_key_env="GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    ),
    "fireworks": _ProviderRoute(
        api_key_env="FIREWORKS_API_KEY",
        base_url="https://api.fireworks.ai/inference/v1",
    ),
    "openrouter": _ProviderRoute(
        api_key_env="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
    ),
    "ollama": _ProviderRoute(
        api_key_env="",  # Ollama is keyless (LAN daemon).
        base_url=None,
    ),
}


class RouterError(RuntimeError):
    """Slug failed to resolve to a runnable client.

    Three cases collapse to this error so the iteration runner has one
    failure mode to translate: malformed slug, disabled provider, or
    missing API key. The message names which of the three triggered.
    """


@dataclass
class ChatClientHandle:
    """One workflow's resolved chat client.

    Exactly one of `anthropic_client` / `openai_client` is set. The
    agent solver accepts both — pass the one that's populated into
    `predict_one` / `run_with_agent`.

    The handle is single-use: close it after the iteration to release
    the underlying HTTPX connection pool. `aclose()` is a no-op when
    the client doesn't expose one.
    """

    model: str
    anthropic_client: AsyncAnthropic | None = None
    openai_client: Any | None = None  # AsyncOpenAI or OllamaChatClient.

    async def aclose(self) -> None:
        for client in (self.anthropic_client, self.openai_client):
            close = getattr(client, "close", None) or getattr(client, "aclose", None)
            if close is None:
                continue
            try:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            except Exception:  # pragma: no cover - defensive
                _logger.debug("router: ignoring exception from client.aclose()", exc_info=True)


def build_chat_client(
    slug: str,
    *,
    env: dict[str, str] | None = None,
) -> ChatClientHandle:
    """Resolve a `provider:model` slug into a live chat client.

    Validates the slug against the runtime allowlist (`enabled_providers`)
    before instantiating anything. Raises `RouterError` when the
    provider is disabled, the API key is missing, or the slug is
    malformed. The caller catches `RouterError` and surfaces a 4xx;
    the workflow's stored `agent_model_id` should not have made it past
    the PATCH endpoint, so this path mostly defends against operators
    flipping a provider OFF after a workflow already chose it.

    Args:
        slug: `provider:model`, e.g. `anthropic:claude-sonnet-4-6` or
            `ollama:qwen3-coder:30b`.
        env: Inject for tests; defaults to `os.environ`.
    """
    source = env if env is not None else dict(os.environ)

    try:
        provider_id, model = parse_slug(slug)
    except ValueError as exc:
        raise RouterError(str(exc)) from exc

    # Allowlist check: the runtime config must still expose this provider+model.
    allowed = False
    for provider, models in enabled_providers(env=source):
        if provider.id == provider_id and model in models:
            allowed = True
            break
    if not allowed:
        raise RouterError(
            f"provider {provider_id!r} or model {model!r} is not in the "
            "current allowlist; the operator has disabled it. Re-pick a "
            "model in workflow settings."
        )

    route = _ROUTES[provider_id]
    api_key = source.get(route.api_key_env, "") if route.api_key_env else ""
    if route.api_key_env and not api_key:
        raise RouterError(
            f"provider {provider_id!r} is enabled but {route.api_key_env} "
            "is not set in the kernel environment. The picker UI accepted "
            "the slug because the provider is in the allowlist, but the "
            "runtime cannot dispatch to it. Set the env var or disable "
            "the provider."
        )

    if provider_id == "anthropic":
        from ..api._anthropic_client import build_async_anthropic

        return ChatClientHandle(
            model=model,
            anthropic_client=build_async_anthropic(api_key),
        )

    if provider_id == "ollama":
        from ..eval_runner.ollama_native import OllamaChatClient

        host = source.get("OWNEVO_LLM_HOST") or "localhost"
        base_url = f"http://{host}:11434"
        return ChatClientHandle(
            model=model,
            openai_client=OllamaChatClient(base_url=base_url),
        )

    # OpenAI-compat providers (openai / xai / gemini / fireworks / openrouter).
    from openai import AsyncOpenAI

    if route.base_url:
        client = AsyncOpenAI(api_key=api_key, base_url=route.base_url)
    else:
        client = AsyncOpenAI(api_key=api_key)
    return ChatClientHandle(model=model, openai_client=client)


def check_provider_api_keys(env: dict[str, str] | None = None) -> list[str]:
    """Return a list of warning messages, one per enabled provider missing a key.

    Called at API startup so an operator who flipped `_ENABLED=true`
    without setting the matching `_API_KEY` finds out immediately,
    not on the first iteration. Empty list = all good.
    """
    source = env if env is not None else dict(os.environ)
    warnings: list[str] = []
    for provider, _models in enabled_providers(env=source):
        route = _ROUTES.get(provider.id)
        if route is None or not route.api_key_env:
            continue
        if not source.get(route.api_key_env):
            warnings.append(
                f"provider {provider.id!r} is enabled but {route.api_key_env} "
                "is not set; iterations that pick this provider will fail."
            )
    return warnings


__all__ = [
    "ChatClientHandle",
    "RouterError",
    "build_chat_client",
    "check_provider_api_keys",
]
