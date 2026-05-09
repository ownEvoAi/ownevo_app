"""Unit tests for eval_runner.ollama_native (TODO-25).

Tests cover:
  - is_ollama_url() detection logic
  - _parse_ollama_response() response translation
  - OllamaChatClient payload construction (httpx mocked)
  - qwen3 think:false injection
  - stream=True guard
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ownevo_kernel.eval_runner.ollama_native import (
    DEFAULT_TIMEOUT_SECONDS,
    OLLAMA_DEFAULT_PORT,
    OllamaChatClient,
    OllamaResponse,
    _ollama_api_base,
    _parse_ollama_response,
    is_ollama_url,
)


# ---------------------------------------------------------------------------
# is_ollama_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://localhost:11434/v1", True),
        ("http://localhost:11434", True),
        ("http://localhost:1234/v1", False),   # LMS
        ("http://localhost:8080/v1", False),
        ("https://api.openai.com/v1", False),
        ("http://localhost:11434/api/chat", True),  # direct /api/chat URL
    ],
)
def test_is_ollama_url(url: str, expected: bool) -> None:
    assert is_ollama_url(url) == expected


def test_default_timeout_is_generous() -> None:
    assert DEFAULT_TIMEOUT_SECONDS >= 300.0


@pytest.mark.parametrize(
    "url",
    ["not a url at all :::", ""],
)
def test_is_ollama_url_bad_input_returns_false(url: str) -> None:
    assert is_ollama_url(url) is False


def test_ollama_default_port_constant() -> None:
    assert OLLAMA_DEFAULT_PORT == 11434


# ---------------------------------------------------------------------------
# _ollama_api_base
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://localhost:11434/v1", "http://localhost:11434"),
        ("http://localhost:11434/v1/", "http://localhost:11434"),
        ("http://localhost:11434", "http://localhost:11434"),
        # Non-standard paths must NOT be stripped
        ("http://localhost:11434/openai/v1", "http://localhost:11434/openai/v1"),
        ("http://localhost:11434/api/v1", "http://localhost:11434/api/v1"),
        ("http://localhost:11434/v1/something", "http://localhost:11434/v1/something"),
    ],
)
def test_ollama_api_base(url: str, expected: str) -> None:
    assert _ollama_api_base(url) == expected


# ---------------------------------------------------------------------------
# _parse_ollama_response
# ---------------------------------------------------------------------------


def _make_ollama_tool_response(
    name: str = "predict_label",
    arguments: dict | None = None,
    done_reason: str = "stop",
    prompt_eval_count: int = 100,
    eval_count: int = 20,
) -> dict:
    return {
        "model": "qwen3:14b",
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": name,
                        "arguments": arguments or {"value": True, "rationale": "looks right"},
                    }
                }
            ],
        },
        "done": True,
        "done_reason": done_reason,
        "prompt_eval_count": prompt_eval_count,
        "eval_count": eval_count,
    }


def test_parse_response_tool_call_present() -> None:
    data = _make_ollama_tool_response()
    resp = _parse_ollama_response(data)

    assert isinstance(resp, OllamaResponse)
    assert len(resp.choices) == 1
    choice = resp.choices[0]
    assert choice.finish_reason == "stop"
    assert len(choice.message.tool_calls) == 1
    tc = choice.message.tool_calls[0]
    assert tc.function.name == "predict_label"
    args = json.loads(tc.function.arguments)
    assert args == {"value": True, "rationale": "looks right"}


def test_parse_response_arguments_serialized_to_json_string() -> None:
    """Ollama returns arguments as dict; the wrapper must JSON-serialize them."""
    data = _make_ollama_tool_response(arguments={"value": False, "rationale": "nope"})
    resp = _parse_ollama_response(data)
    raw = resp.choices[0].message.tool_calls[0].function.arguments
    assert isinstance(raw, str)
    assert json.loads(raw)["value"] is False


def test_parse_response_no_tool_calls() -> None:
    data = {
        "model": "qwen3:14b",
        "message": {"role": "assistant", "content": "I cannot help with that."},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 50,
        "eval_count": 10,
    }
    resp = _parse_ollama_response(data)
    assert resp.choices[0].message.tool_calls == []
    assert resp.choices[0].message.content == "I cannot help with that."


def test_parse_response_usage_counts() -> None:
    data = _make_ollama_tool_response(prompt_eval_count=512, eval_count=42)
    resp = _parse_ollama_response(data)
    assert resp.usage.prompt_tokens == 512
    assert resp.usage.completion_tokens == 42


def test_parse_response_missing_keys_graceful() -> None:
    resp = _parse_ollama_response({})
    assert resp.choices[0].message.tool_calls == []
    assert resp.usage.prompt_tokens == 0


def test_parse_response_done_reason_forwarded() -> None:
    data = _make_ollama_tool_response(done_reason="length")
    assert _parse_ollama_response(data).choices[0].finish_reason == "length"


def test_parse_response_ollama_200_error_raises() -> None:
    """Ollama returns HTTP 200 with {'error': '...'} for model-not-found / OOM."""
    data = {"error": "model 'qwen3:14b' not found, try pulling it first"}
    with pytest.raises(RuntimeError, match="Ollama /api/chat error:"):
        _parse_ollama_response(data)


def test_parse_response_done_false_raises() -> None:
    """A streaming chunk (done=false) must not be silently parsed as a full response."""
    data = {
        "model": "qwen3:14b",
        "message": {"role": "assistant", "content": "partial"},
        "done": False,
        "done_reason": None,
        "prompt_eval_count": 0,
        "eval_count": 0,
    }
    with pytest.raises(RuntimeError, match="done=false"):
        _parse_ollama_response(data)


def test_parse_response_arguments_already_string_passthrough() -> None:
    """Some Ollama builds return arguments already serialized as a JSON string."""
    already_serialized = '{"value": true, "rationale": "looks right"}'
    data = {
        "model": "qwen3:14b",
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "predict_label", "arguments": already_serialized}}
            ],
        },
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 10,
        "eval_count": 5,
    }
    resp = _parse_ollama_response(data)
    raw = resp.choices[0].message.tool_calls[0].function.arguments
    # Must not be double-encoded
    assert raw == already_serialized
    assert json.loads(raw)["value"] is True


# ---------------------------------------------------------------------------
# OllamaChatClient — payload construction (httpx mocked)
# ---------------------------------------------------------------------------


def _mock_httpx_response(data: dict) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=data)
    return mock_resp


def _make_mock_http(fake_post) -> MagicMock:
    """Build a mock httpx.AsyncClient with the given async post callable.

    OllamaChatClient holds a shared httpx.AsyncClient created at __init__
    time. Patch httpx.AsyncClient before constructing the client so all
    create() calls route through fake_post.
    """
    mock = MagicMock()
    mock.post = fake_post
    mock.aclose = AsyncMock()
    return mock


@pytest.mark.asyncio
async def test_client_sends_correct_payload() -> None:
    """Verify OllamaChatClient sends the expected JSON to /api/chat."""
    ollama_data = _make_ollama_tool_response()
    mock_resp = _mock_httpx_response(ollama_data)
    captured: list[dict] = []

    async def fake_post(url, *, json, headers):
        captured.append({"url": url, "json": json})
        return mock_resp

    with patch("ownevo_kernel.eval_runner.ollama_native.httpx.AsyncClient",
               return_value=_make_mock_http(fake_post)):
        client = OllamaChatClient("http://localhost:11434/v1")
        resp = await client.chat.completions.create(
            model="qwen3:14b",
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"type": "function", "function": {"name": "predict_label"}}],
            max_tokens=1000,
        )

    assert len(captured) == 1
    sent = captured[0]
    assert sent["url"] == "http://localhost:11434/api/chat"
    payload = sent["json"]
    assert payload["model"] == "qwen3:14b"
    assert payload["stream"] is False
    assert "tools" in payload
    # qwen3 → think:false injected
    assert payload["options"]["think"] is False
    assert payload["options"]["num_predict"] == 1000


@pytest.mark.asyncio
async def test_client_non_qwen3_no_think_option() -> None:
    """Non-qwen3 models must not have think:false injected."""
    ollama_data = _make_ollama_tool_response()
    mock_resp = _mock_httpx_response(ollama_data)
    captured: list[dict] = []

    async def fake_post(url, *, json, headers):
        captured.append(json)
        return mock_resp

    with patch("ownevo_kernel.eval_runner.ollama_native.httpx.AsyncClient",
               return_value=_make_mock_http(fake_post)):
        client = OllamaChatClient("http://localhost:11434/v1")
        await client.chat.completions.create(
            model="gemma4:26b",
            messages=[{"role": "user", "content": "hi"}],
        )

    payload = captured[0]
    options = payload.get("options", {})
    assert "think" not in options


@pytest.mark.asyncio
async def test_client_no_tools_omits_tools_key() -> None:
    """When tools=None, the payload must not include a 'tools' key."""
    ollama_data = _make_ollama_tool_response()
    mock_resp = _mock_httpx_response(ollama_data)
    captured: list[dict] = []

    async def fake_post(url, *, json, headers):
        captured.append(json)
        return mock_resp

    with patch("ownevo_kernel.eval_runner.ollama_native.httpx.AsyncClient",
               return_value=_make_mock_http(fake_post)):
        client = OllamaChatClient("http://localhost:11434")
        await client.chat.completions.create(
            model="qwen3:14b",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert "tools" not in captured[0]


@pytest.mark.asyncio
async def test_client_custom_timeout_forwarded() -> None:
    """OllamaChatClient(timeout=...) must reach httpx.AsyncClient at init time."""
    ollama_data = _make_ollama_tool_response()
    mock_resp = _mock_httpx_response(ollama_data)
    captured_kwargs: list[dict] = []

    async def fake_post(url, *, json, headers):
        return mock_resp

    def capturing_client(*args, **kwargs):
        captured_kwargs.append(kwargs)
        return _make_mock_http(fake_post)

    with patch("ownevo_kernel.eval_runner.ollama_native.httpx.AsyncClient",
               side_effect=capturing_client):
        client = OllamaChatClient("http://localhost:11434/v1", timeout=42.0)
        await client.chat.completions.create(model="qwen3:14b", messages=[])

    assert captured_kwargs[0]["timeout"] == 42.0


@pytest.mark.asyncio
async def test_client_stream_raises() -> None:
    client = OllamaChatClient("http://localhost:11434/v1")
    with pytest.raises(NotImplementedError, match="stream=True"):
        await client.chat.completions.create(
            model="qwen3:14b",
            messages=[],
            stream=True,
        )


@pytest.mark.asyncio
async def test_client_strips_v1_suffix_from_url() -> None:
    """Base URL with /v1 suffix gets stripped before appending /api/chat."""
    ollama_data = _make_ollama_tool_response()
    mock_resp = _mock_httpx_response(ollama_data)
    captured: list[dict] = []

    async def fake_post(url, *, json, headers):
        captured.append({"url": url})
        return mock_resp

    with patch("ownevo_kernel.eval_runner.ollama_native.httpx.AsyncClient",
               return_value=_make_mock_http(fake_post)):
        client = OllamaChatClient("http://localhost:11434/v1")
        await client.chat.completions.create(model="qwen3:14b", messages=[])

    assert captured[0]["url"] == "http://localhost:11434/api/chat"


@pytest.mark.asyncio
async def test_client_http_error_propagates() -> None:
    """HTTP 4xx/5xx from Ollama must propagate as httpx.HTTPStatusError."""
    import httpx as _httpx

    error_resp = MagicMock()
    error_resp.raise_for_status = MagicMock(
        side_effect=_httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        )
    )
    error_resp.json = MagicMock()

    async def fake_post(url, *, json, headers):
        return error_resp

    with patch("ownevo_kernel.eval_runner.ollama_native.httpx.AsyncClient",
               return_value=_make_mock_http(fake_post)):
        client = OllamaChatClient("http://localhost:11434/v1")
        with pytest.raises(_httpx.HTTPStatusError):
            await client.chat.completions.create(model="qwen3:14b", messages=[])


# ---------------------------------------------------------------------------
# nl_gen_smoketest._make_openai_client dispatch (D2)
# ---------------------------------------------------------------------------

import importlib.util as _ilu
import pathlib as _pathlib


def _load_smoketest():
    """Load _make_openai_client from scripts/nl_gen_smoketest.py via importlib."""
    script = (
        _pathlib.Path(__file__).parent.parent
        / "scripts"
        / "nl_gen_smoketest.py"
    )
    spec = _ilu.spec_from_file_location("nl_gen_smoketest", script)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._make_openai_client


def test_make_openai_client_returns_ollama_client_for_ollama_url() -> None:
    """Ollama URLs must dispatch to OllamaChatClient, not AsyncOpenAI."""
    make_client = _load_smoketest()
    for url in ("http://localhost:11434/v1", "http://localhost:11434"):
        client = make_client(url)
        assert isinstance(client, OllamaChatClient), (
            f"Expected OllamaChatClient for {url}"
        )


def test_make_openai_client_returns_openai_for_non_ollama_url() -> None:
    """Non-Ollama URLs must fall through to AsyncOpenAI (mocked — openai is optional)."""
    import sys
    fake_openai_client = MagicMock(name="AsyncOpenAI_instance")
    fake_async_openai = MagicMock(return_value=fake_openai_client)
    fake_openai_mod = MagicMock()
    fake_openai_mod.AsyncOpenAI = fake_async_openai

    with patch.dict(sys.modules, {"openai": fake_openai_mod}):
        make_client = _load_smoketest()
        for url in ("http://localhost:1234/v1", "http://localhost:8080/v1"):
            result = make_client(url)
            assert result is fake_openai_client, f"Expected AsyncOpenAI mock for {url}"
