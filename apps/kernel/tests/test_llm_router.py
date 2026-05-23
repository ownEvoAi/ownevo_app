"""Unit tests for `kernel/llm/router.py` slug-to-client dispatch.

The router resolves a `provider:model` slug stored on
`workflows.agent_model_id` into a live chat client. These tests verify
the dispatch table picks the right SDK + base URL + API key without
making any live API calls — the constructor args are asserted on the
returned handle's client object.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.llm.router import (
    ChatClientHandle,
    RouterError,
    build_chat_client,
    check_provider_api_keys,
)


def _env(**extras: str) -> dict[str, str]:
    """Baseline env with one provider enabled; tests merge their own keys in."""
    base = {
        "OWNEVO_PROVIDER_ANTHROPIC_ENABLED": "true",
        "OWNEVO_PROVIDER_ANTHROPIC_MODELS": "claude-sonnet-4-6",
        "ANTHROPIC_API_KEY": "test-anthropic-key",
    }
    base.update(extras)
    return base


# ---------------------------------------------------------------------------
# Failure modes — these don't touch any SDK at all
# ---------------------------------------------------------------------------


def test_router_rejects_disabled_provider():
    env = _env()
    with pytest.raises(RouterError, match="not in the current allowlist"):
        build_chat_client("openai:gpt-5", env=env)


def test_router_rejects_unknown_model_on_enabled_provider():
    env = _env()
    with pytest.raises(RouterError, match="not in the current allowlist"):
        build_chat_client("anthropic:claude-opus-4-7", env=env)


def test_router_rejects_missing_api_key():
    env = _env(
        OWNEVO_PROVIDER_OPENAI_ENABLED="true",
        OWNEVO_PROVIDER_OPENAI_MODELS="gpt-5",
    )
    env.pop("OPENAI_API_KEY", None)
    with pytest.raises(RouterError, match="OPENAI_API_KEY"):
        build_chat_client("openai:gpt-5", env=env)


def test_router_rejects_malformed_slug():
    env = _env()
    with pytest.raises(RouterError):
        build_chat_client("anthropic", env=env)


# ---------------------------------------------------------------------------
# Provider dispatch — assert handle shape + (where possible) client config
# ---------------------------------------------------------------------------


def test_router_dispatches_anthropic():
    pytest.importorskip("anthropic")
    env = _env()
    handle = build_chat_client("anthropic:claude-sonnet-4-6", env=env)
    assert isinstance(handle, ChatClientHandle)
    assert handle.model == "claude-sonnet-4-6"
    assert handle.anthropic_client is not None
    assert handle.openai_client is None


def test_router_dispatches_openai_with_default_base_url():
    pytest.importorskip("openai")
    env = _env(
        OWNEVO_PROVIDER_OPENAI_ENABLED="true",
        OWNEVO_PROVIDER_OPENAI_MODELS="gpt-5",
        OPENAI_API_KEY="test-openai-key",
    )
    handle = build_chat_client("openai:gpt-5", env=env)
    assert handle.model == "gpt-5"
    assert handle.anthropic_client is None
    assert handle.openai_client is not None
    # openai SDK exposes the resolved base_url on the client.
    base_url = str(handle.openai_client.base_url)
    assert base_url.startswith("https://api.openai.com")


@pytest.mark.parametrize(
    ("provider", "model", "key_env", "key_value", "expected_host"),
    [
        ("xai", "grok-4", "XAI_API_KEY", "test-xai-key", "api.x.ai"),
        (
            "gemini",
            "gemini-3-pro",
            "GEMINI_API_KEY",
            "test-gemini-key",
            "generativelanguage.googleapis.com",
        ),
        (
            "openrouter",
            "moonshotai/kimi-k2",
            "OPENROUTER_API_KEY",
            "test-openrouter-key",
            "openrouter.ai",
        ),
    ],
)
def test_router_dispatches_openai_compat_providers(
    provider: str,
    model: str,
    key_env: str,
    key_value: str,
    expected_host: str,
):
    pytest.importorskip("openai")
    enabled_env = f"OWNEVO_PROVIDER_{provider.upper()}_ENABLED"
    models_env = f"OWNEVO_PROVIDER_{provider.upper()}_MODELS"
    env = _env(
        **{
            enabled_env: "true",
            models_env: model,
            key_env: key_value,
        }
    )
    handle = build_chat_client(f"{provider}:{model}", env=env)
    assert handle.model == model
    assert handle.anthropic_client is None
    assert handle.openai_client is not None
    base_url = str(handle.openai_client.base_url)
    assert expected_host in base_url


def test_router_dispatches_ollama_without_api_key():
    env = _env(
        OWNEVO_PROVIDER_OLLAMA_ENABLED="true",
        OWNEVO_PROVIDER_OLLAMA_MODELS="qwen3-coder:30b",
        OWNEVO_LLM_HOST="127.0.0.1",
    )
    handle = build_chat_client("ollama:qwen3-coder:30b", env=env)
    assert handle.model == "qwen3-coder:30b"
    assert handle.anthropic_client is None
    assert handle.openai_client is not None
    # OllamaChatClient stores the cleaned api base internally; just
    # verify it isn't the default openai endpoint.
    assert "ollama" in type(handle.openai_client).__name__.lower()


def test_router_dispatches_local_via_explicit_base_url():
    pytest.importorskip("openai")
    env = _env(
        OWNEVO_PROVIDER_LOCAL_ENABLED="true",
        OWNEVO_PROVIDER_LOCAL_MODELS="qwen/qwen3.6-35b-a3b",
        OWNEVO_LOCAL_BASE_URL="http://127.0.0.1:1234/v1",
    )
    handle = build_chat_client("local:qwen/qwen3.6-35b-a3b", env=env)
    assert handle.model == "qwen/qwen3.6-35b-a3b"
    assert handle.anthropic_client is None
    assert handle.openai_client is not None
    assert "127.0.0.1" in str(handle.openai_client.base_url)


def test_router_dispatches_local_falls_back_to_llm_host():
    pytest.importorskip("openai")
    env = _env(
        OWNEVO_PROVIDER_LOCAL_ENABLED="true",
        OWNEVO_PROVIDER_LOCAL_MODELS="qwen/qwen3.6-35b-a3b",
        OWNEVO_LLM_HOST="10.0.0.5",
    )
    env.pop("OWNEVO_LOCAL_BASE_URL", None)
    handle = build_chat_client("local:qwen/qwen3.6-35b-a3b", env=env)
    assert "10.0.0.5" in str(handle.openai_client.base_url)


def test_router_dispatches_local_defaults_to_localhost():
    pytest.importorskip("openai")
    env = _env(
        OWNEVO_PROVIDER_LOCAL_ENABLED="true",
        OWNEVO_PROVIDER_LOCAL_MODELS="qwen/qwen3.6-35b-a3b",
    )
    env.pop("OWNEVO_LOCAL_BASE_URL", None)
    env.pop("OWNEVO_LLM_HOST", None)
    handle = build_chat_client("local:qwen/qwen3.6-35b-a3b", env=env)
    assert "localhost" in str(handle.openai_client.base_url)


# ---------------------------------------------------------------------------
# check_provider_api_keys — startup sanity check
# ---------------------------------------------------------------------------


def test_check_provider_api_keys_all_set():
    env = _env(
        OWNEVO_PROVIDER_OPENAI_ENABLED="true",
        OWNEVO_PROVIDER_OPENAI_MODELS="gpt-5",
        OPENAI_API_KEY="test",
    )
    assert check_provider_api_keys(env=env) == []


def test_check_provider_api_keys_warns_on_missing_key():
    env = _env(
        OWNEVO_PROVIDER_OPENAI_ENABLED="true",
        OWNEVO_PROVIDER_OPENAI_MODELS="gpt-5",
    )
    env.pop("OPENAI_API_KEY", None)
    warnings = check_provider_api_keys(env=env)
    assert len(warnings) == 1
    assert "openai" in warnings[0] and "OPENAI_API_KEY" in warnings[0]


def test_check_provider_api_keys_ollama_does_not_warn():
    # Ollama has no API key; enabling it must not produce a missing-key
    # warning even though there's no env var to read.
    env = {
        "OWNEVO_PROVIDER_OLLAMA_ENABLED": "true",
        "OWNEVO_PROVIDER_OLLAMA_MODELS": "qwen3-coder:30b",
    }
    assert check_provider_api_keys(env=env) == []
