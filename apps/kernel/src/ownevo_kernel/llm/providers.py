"""Env-driven LLM provider config.

The runtime-enabled provider list + allowed models is owned by the
operator via environment variables. The API enforces the allowlist
when the web form PATCHes `workflows.agent_model_id`; the web form
fetches the list via `GET /api/models` and renders it grouped by
provider.

Env shape per provider:

    OWNEVO_PROVIDER_<UPPER>_ENABLED=true|false
    OWNEVO_PROVIDER_<UPPER>_MODELS=model-a,model-b,model-c

The API-key envs (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.) are
read by the dispatch router in Phase 2. This module only cares about
which provider+model pairs are *allowed* to be selected.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final, Literal

ProviderId = Literal[
    "anthropic",
    "openai",
    "xai",
    "gemini",
    "fireworks",
    "openrouter",
    "ollama",
]


@dataclass(frozen=True)
class ProviderConfig:
    """Static metadata for one LLM provider.

    `id` is the slug prefix stored in `workflows.agent_model_id`
    (e.g. `anthropic` in `anthropic:claude-sonnet-4-6`). `label` is the
    human-readable name the web form shows as the `<optgroup label>`.
    `enabled_env` and `models_env` are the env-var names the operator
    flips to expose the provider at runtime.
    """

    id: ProviderId
    label: str
    enabled_env: str
    models_env: str


PROVIDERS: Final[tuple[ProviderConfig, ...]] = (
    ProviderConfig(
        id="anthropic",
        label="Anthropic",
        enabled_env="OWNEVO_PROVIDER_ANTHROPIC_ENABLED",
        models_env="OWNEVO_PROVIDER_ANTHROPIC_MODELS",
    ),
    ProviderConfig(
        id="openai",
        label="OpenAI",
        enabled_env="OWNEVO_PROVIDER_OPENAI_ENABLED",
        models_env="OWNEVO_PROVIDER_OPENAI_MODELS",
    ),
    ProviderConfig(
        id="xai",
        label="xAI",
        enabled_env="OWNEVO_PROVIDER_XAI_ENABLED",
        models_env="OWNEVO_PROVIDER_XAI_MODELS",
    ),
    ProviderConfig(
        id="gemini",
        label="Google Gemini",
        enabled_env="OWNEVO_PROVIDER_GEMINI_ENABLED",
        models_env="OWNEVO_PROVIDER_GEMINI_MODELS",
    ),
    ProviderConfig(
        id="fireworks",
        label="Fireworks",
        enabled_env="OWNEVO_PROVIDER_FIREWORKS_ENABLED",
        models_env="OWNEVO_PROVIDER_FIREWORKS_MODELS",
    ),
    ProviderConfig(
        id="openrouter",
        label="OpenRouter",
        enabled_env="OWNEVO_PROVIDER_OPENROUTER_ENABLED",
        models_env="OWNEVO_PROVIDER_OPENROUTER_MODELS",
    ),
    ProviderConfig(
        id="ollama",
        label="Ollama (local)",
        enabled_env="OWNEVO_PROVIDER_OLLAMA_ENABLED",
        models_env="OWNEVO_PROVIDER_OLLAMA_MODELS",
    ),
)


_TRUE_VALUES = frozenset({"1", "true", "t", "yes", "y", "on"})


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUE_VALUES


def _parse_models(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(m for m in (m.strip() for m in raw.split(",")) if m)


def enabled_providers(
    env: dict[str, str] | None = None,
) -> list[tuple[ProviderConfig, tuple[str, ...]]]:
    """Return enabled providers paired with their allowed model lists.

    `env` is injectable for tests; defaults to `os.environ`. A provider
    is included only if its `_ENABLED` flag is truthy AND its `_MODELS`
    list is non-empty. An enabled provider with an empty model list is
    silently dropped — there's nothing for the picker to show.
    """
    source = env if env is not None else dict(os.environ)
    out: list[tuple[ProviderConfig, tuple[str, ...]]] = []
    for provider in PROVIDERS:
        if not _is_truthy(source.get(provider.enabled_env)):
            continue
        models = _parse_models(source.get(provider.models_env))
        if not models:
            continue
        out.append((provider, models))
    return out


# Frozenset of all known provider IDs — built once at import, used by parse_slug.
_KNOWN_PROVIDER_IDS: frozenset[str] = frozenset(p.id for p in PROVIDERS)


def parse_slug(slug: str) -> tuple[ProviderId, str]:
    """Split a `provider:model` slug into its parts.

    Raises `ValueError` on a missing colon or empty side. The model
    side may contain colons (e.g. `ollama:qwen3-coder:30b`) — only the
    first `:` is the separator. The provider side is validated against
    the known `ProviderId` literal.
    """
    if ":" not in slug:
        raise ValueError(
            f"agent model slug missing provider prefix: {slug!r}"
        )
    provider_part, _, model_part = slug.partition(":")
    if not provider_part or not model_part:
        raise ValueError(f"agent model slug has empty side: {slug!r}")
    if provider_part not in _KNOWN_PROVIDER_IDS:
        raise ValueError(
            f"unknown provider {provider_part!r} in slug {slug!r}"
        )
    return provider_part, model_part  # type: ignore[return-value]


def is_model_allowed(
    slug: str,
    env: dict[str, str] | None = None,
) -> bool:
    """Validate a slug against the runtime-enabled allowlist.

    Returns False on a malformed slug, an unknown provider, a disabled
    provider, or a model that isn't in the provider's allowed list.
    Used by the PATCH endpoint to refuse model swaps that the operator
    hasn't explicitly enabled.
    """
    try:
        provider_id, model = parse_slug(slug)
    except ValueError:
        return False
    for provider, models in enabled_providers(env=env):
        if provider.id == provider_id:
            return model in models
    return False
