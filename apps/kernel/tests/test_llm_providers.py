"""Unit tests for `kernel/llm/providers.py` env-driven allowlist."""

from __future__ import annotations

import pytest
from ownevo_kernel.llm import (
    PROVIDERS,
    enabled_providers,
    is_model_allowed,
    parse_slug,
)

# ---------------------------------------------------------------------------
# parse_slug
# ---------------------------------------------------------------------------


def test_parse_slug_simple():
    assert parse_slug("anthropic:claude-sonnet-4-6") == (
        "anthropic",
        "claude-sonnet-4-6",
    )


def test_parse_slug_model_with_colon():
    # Ollama models embed a `:<size>` suffix; only the first colon splits.
    assert parse_slug("ollama:qwen3-coder:30b") == (
        "ollama",
        "qwen3-coder:30b",
    )


def test_parse_slug_model_with_slash():
    # OpenRouter model ids are namespaced `vendor/model`.
    assert parse_slug("openrouter:meta-llama/llama-4-70b") == (
        "openrouter",
        "meta-llama/llama-4-70b",
    )


@pytest.mark.parametrize(
    "bad",
    [
        "noslash",
        ":empty-provider",
        "anthropic:",
        "",
        ":",
    ],
)
def test_parse_slug_rejects_malformed(bad: str):
    with pytest.raises(ValueError):
        parse_slug(bad)


def test_parse_slug_rejects_unknown_provider():
    with pytest.raises(ValueError):
        parse_slug("acme:foo-bar")


# ---------------------------------------------------------------------------
# enabled_providers
# ---------------------------------------------------------------------------


def test_enabled_providers_empty_env_returns_nothing():
    assert enabled_providers(env={}) == []


def test_enabled_providers_returns_only_truthy_flags():
    env = {
        "OWNEVO_PROVIDER_ANTHROPIC_ENABLED": "true",
        "OWNEVO_PROVIDER_ANTHROPIC_MODELS": "claude-sonnet-4-6,claude-opus-4-7",
        "OWNEVO_PROVIDER_OPENAI_ENABLED": "false",
        "OWNEVO_PROVIDER_OPENAI_MODELS": "gpt-5",
    }
    out = enabled_providers(env=env)
    assert len(out) == 1
    provider, models = out[0]
    assert provider.id == "anthropic"
    assert models == ("claude-sonnet-4-6", "claude-opus-4-7")


def test_enabled_providers_drops_enabled_but_empty_model_list():
    env = {
        "OWNEVO_PROVIDER_ANTHROPIC_ENABLED": "true",
        "OWNEVO_PROVIDER_ANTHROPIC_MODELS": "",
    }
    assert enabled_providers(env=env) == []


def test_enabled_providers_preserves_declaration_order():
    # Even if env sets providers out of order, the result follows
    # PROVIDERS' declaration order so the picker UI is stable.
    env = {
        "OWNEVO_PROVIDER_LOCAL_ENABLED": "true",
        "OWNEVO_PROVIDER_LOCAL_MODELS": "qwen/qwen3.6-35b-a3b",
        "OWNEVO_PROVIDER_ANTHROPIC_ENABLED": "true",
        "OWNEVO_PROVIDER_ANTHROPIC_MODELS": "claude-sonnet-4-6",
    }
    out = enabled_providers(env=env)
    declaration = [p.id for p in PROVIDERS]
    seen_order = [p.id for p, _ in out]
    # Each enabled provider sits in the same relative slot as in PROVIDERS.
    assert seen_order == sorted(seen_order, key=declaration.index)


def test_enabled_providers_trims_whitespace_in_model_list():
    env = {
        "OWNEVO_PROVIDER_OPENAI_ENABLED": "1",
        "OWNEVO_PROVIDER_OPENAI_MODELS": " gpt-5 , gpt-5-mini ",
    }
    out = enabled_providers(env=env)
    assert out[0][1] == ("gpt-5", "gpt-5-mini")


@pytest.mark.parametrize("truthy", ["true", "True", "TRUE", "1", "yes", "y", "on"])
def test_enabled_providers_accepts_common_truthy_values(truthy: str):
    env = {
        "OWNEVO_PROVIDER_ANTHROPIC_ENABLED": truthy,
        "OWNEVO_PROVIDER_ANTHROPIC_MODELS": "claude-sonnet-4-6",
    }
    assert len(enabled_providers(env=env)) == 1


@pytest.mark.parametrize("falsy", ["false", "0", "no", "", "off"])
def test_enabled_providers_rejects_common_falsy_values(falsy: str):
    env = {
        "OWNEVO_PROVIDER_ANTHROPIC_ENABLED": falsy,
        "OWNEVO_PROVIDER_ANTHROPIC_MODELS": "claude-sonnet-4-6",
    }
    assert enabled_providers(env=env) == []


# ---------------------------------------------------------------------------
# is_model_allowed
# ---------------------------------------------------------------------------


def _two_provider_env() -> dict[str, str]:
    return {
        "OWNEVO_PROVIDER_ANTHROPIC_ENABLED": "true",
        "OWNEVO_PROVIDER_ANTHROPIC_MODELS": "claude-sonnet-4-6,claude-opus-4-7",
        "OWNEVO_PROVIDER_LOCAL_ENABLED": "true",
        "OWNEVO_PROVIDER_LOCAL_MODELS": "qwen/qwen3.6-35b-a3b",
    }


def test_is_model_allowed_happy_path():
    env = _two_provider_env()
    assert is_model_allowed("anthropic:claude-sonnet-4-6", env=env)
    assert is_model_allowed("local:qwen/qwen3.6-35b-a3b", env=env)


def test_is_model_allowed_rejects_unknown_model_under_enabled_provider():
    env = _two_provider_env()
    assert not is_model_allowed("anthropic:claude-haiku-4-5", env=env)


def test_is_model_allowed_rejects_disabled_provider():
    env = _two_provider_env()
    assert not is_model_allowed("openai:gpt-5", env=env)


def test_is_model_allowed_rejects_malformed():
    env = _two_provider_env()
    assert not is_model_allowed("no-colon", env=env)
    assert not is_model_allowed("", env=env)
