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
    OllamaChatClient,
    OllamaResponse,
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
        ("http://192.168.1.50:11434/v1", True),
        ("http://192.168.1.50:11434", True),
        ("http://localhost:1234/v1", False),   # LMS
        ("http://localhost:8080/v1", False),
        ("https://api.openai.com/v1", False),
        ("http://localhost:11434/api/chat", True),  # direct /api/chat URL
    ],
)
def test_is_ollama_url(url: str, expected: bool) -> None:
    assert is_ollama_url(url) == expected


def test_is_ollama_url_bad_input_returns_false() -> None:
    assert is_ollama_url("not a url at all :::") is False
    assert is_ollama_url("") is False


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


# ---------------------------------------------------------------------------
# OllamaChatClient — payload construction (httpx mocked)
# ---------------------------------------------------------------------------


def _mock_httpx_response(data: dict) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=data)
    return mock_resp


@pytest.mark.asyncio
async def test_client_sends_correct_payload() -> None:
    """Verify OllamaChatClient sends the expected JSON to /api/chat."""
    ollama_data = _make_ollama_tool_response()
    mock_resp = _mock_httpx_response(ollama_data)

    captured: list[dict] = []

    async def fake_post(url, *, json, headers):
        captured.append({"url": url, "json": json})
        return mock_resp

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = fake_post

    client = OllamaChatClient("http://192.168.1.50:11434/v1")

    with patch("ownevo_kernel.eval_runner.ollama_native.httpx.AsyncClient", return_value=mock_http):
        resp = await client.chat.completions.create(
            model="qwen3:14b",
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"type": "function", "function": {"name": "predict_label"}}],
            max_tokens=1000,
        )

    assert len(captured) == 1
    sent = captured[0]
    assert sent["url"] == "http://192.168.1.50:11434/api/chat"
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

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = fake_post

    client = OllamaChatClient("http://192.168.1.50:11434/v1")

    with patch("ownevo_kernel.eval_runner.ollama_native.httpx.AsyncClient", return_value=mock_http):
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

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = fake_post

    client = OllamaChatClient("http://192.168.1.50:11434")

    with patch("ownevo_kernel.eval_runner.ollama_native.httpx.AsyncClient", return_value=mock_http):
        await client.chat.completions.create(
            model="qwen3:14b",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert "tools" not in captured[0]


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

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = fake_post

    client = OllamaChatClient("http://192.168.1.50:11434/v1")

    with patch("ownevo_kernel.eval_runner.ollama_native.httpx.AsyncClient", return_value=mock_http):
        await client.chat.completions.create(
            model="qwen3:14b",
            messages=[],
        )

    assert captured[0]["url"] == "http://192.168.1.50:11434/api/chat"
