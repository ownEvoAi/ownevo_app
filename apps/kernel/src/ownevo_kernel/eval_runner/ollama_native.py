"""Ollama native /api/chat client for the A4.4 agent-solver path.

When --openai-base-url points to an Ollama daemon (:11434), route through
/api/chat instead of /v1/chat/completions. This lets us pass `think: false`
in options, which /v1/chat/completions silently strips on some Ollama builds
(see F14h-hang in docs/local-model-testing.md, TODO-25).

Usage: nl_gen_smoketest._make_openai_client returns OllamaChatClient instead
of AsyncOpenAI when is_ollama_url() detects port 11434. OllamaChatClient
duck-types the `client.chat.completions.create()` interface so predict_one()
works without modification.

Only non-streaming single calls are supported — the A4.4 gate is single-turn
and does not need streaming. For the multi-turn loop (run_agent_turn_openai),
stay on AsyncOpenAI / LMS OpenAI-compat.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


# ---------------------------------------------------------------------------
# URL detection
# ---------------------------------------------------------------------------


def is_ollama_url(url: str) -> bool:
    """Return True when url points to an Ollama daemon (default port 11434)."""
    try:
        return urlparse(url).port == 11434
    except Exception:
        return False


def _ollama_api_base(url: str) -> str:
    """Strip /v1 suffix from an Ollama OpenAI-compat URL to get the daemon base.

    e.g. http://192.168.1.50:11434/v1 → http://192.168.1.50:11434
    """
    stripped = url.rstrip("/")
    if stripped.endswith("/v1"):
        stripped = stripped[:-3]
    return stripped


# ---------------------------------------------------------------------------
# Response shape — compatible with _extract_prediction_openai
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _OllamaToolFunction:
    name: str
    arguments: str  # JSON string; Ollama returns dict, we serialize on ingestion


@dataclass(frozen=True)
class _OllamaToolCall:
    function: _OllamaToolFunction


@dataclass(frozen=True)
class _OllamaMessage:
    tool_calls: list[_OllamaToolCall]
    content: str


@dataclass(frozen=True)
class _OllamaChoice:
    message: _OllamaMessage
    finish_reason: str


@dataclass(frozen=True)
class _OllamaUsage:
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class OllamaResponse:
    """OpenAI-shaped wrapper around an Ollama /api/chat response.

    Implements the subset of the OpenAI response shape that
    _extract_prediction_openai and predict_one's budget recorder access:
      .choices[0].message.tool_calls[0].function.name
      .choices[0].message.tool_calls[0].function.arguments  (JSON str)
      .choices[0].finish_reason
      .usage.prompt_tokens
      .usage.completion_tokens
    """

    choices: list[_OllamaChoice]
    usage: _OllamaUsage


def _parse_ollama_response(data: dict[str, Any]) -> OllamaResponse:
    """Translate raw Ollama /api/chat JSON → OllamaResponse."""
    msg = data.get("message") or {}
    raw_tool_calls: list[dict[str, Any]] = msg.get("tool_calls") or []
    finish_reason: str = data.get("done_reason") or "stop"

    tool_calls: list[_OllamaToolCall] = []
    for tc in raw_tool_calls:
        fn = tc.get("function") or {}
        name: str = fn.get("name") or ""
        args: Any = fn.get("arguments") or {}
        # Ollama returns arguments as a dict; OpenAI consumers expect JSON string
        arguments_str = json.dumps(args) if isinstance(args, dict) else str(args)
        tool_calls.append(
            _OllamaToolCall(
                function=_OllamaToolFunction(name=name, arguments=arguments_str)
            )
        )

    choice = _OllamaChoice(
        message=_OllamaMessage(
            tool_calls=tool_calls,
            content=msg.get("content") or "",
        ),
        finish_reason=finish_reason,
    )

    usage = _OllamaUsage(
        prompt_tokens=int(data.get("prompt_eval_count") or 0),
        completion_tokens=int(data.get("eval_count") or 0),
    )

    return OllamaResponse(choices=[choice], usage=usage)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


DEFAULT_TIMEOUT_SECONDS = 300.0
"""Per-request httpx timeout for Ollama /api/chat calls.

12 concurrent cases fire simultaneously against a single GPU; the last case
queued must wait for ~11 earlier completions before it is served. At ~10s
per case on qwen3:14b, the last queued request can take up to ~120s just
waiting in the Ollama queue — then its own generation time on top. 300s
gives comfortable headroom for long-prompt cases on slower models without
being absurd.
"""


class _Completions:
    def __init__(self, api_base: str, timeout: float) -> None:
        self._api_base = api_base
        self._timeout = timeout

    async def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        **_kwargs: Any,
    ) -> OllamaResponse:
        """POST to /api/chat; return OllamaResponse compatible with predict_one."""
        if stream:
            raise NotImplementedError(
                "OllamaChatClient does not support stream=True — "
                "use AsyncOpenAI for streaming paths (run_agent_turn_openai)."
            )

        options: dict[str, Any] = {}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        # Pass think:false in options for qwen3-family models — this is the
        # reliable suppression path. /v1/chat/completions strips it on some builds.
        if "qwen3" in model.lower():
            options["think"] = False

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(
                f"{self._api_base}/api/chat",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

        return _parse_ollama_response(data)


class _Chat:
    def __init__(self, api_base: str, timeout: float) -> None:
        self.completions = _Completions(api_base, timeout)


class OllamaChatClient:
    """Drop-in AsyncOpenAI replacement that routes to Ollama /api/chat.

    Duck-types the `openai_client.chat.completions.create(...)` interface
    consumed by predict_one() in agent_solver.py. Supports non-streaming
    single calls only (sufficient for the A4.4 gate path).

    Key difference from AsyncOpenAI on Ollama /v1:
    - Passes `options.think = false` for qwen3-family models, which is
      reliably honoured by /api/chat regardless of the Ollama build's
      Modelfile template (see F14h-hang, docs/local-model-testing.md).

    Args:
        base_url: Ollama URL (e.g. http://192.168.1.50:11434/v1 or
            http://192.168.1.50:11434).  The /v1 suffix is stripped
            automatically before appending /api/chat.
        timeout: Per-request httpx timeout in seconds. Default 300s —
            generous enough for 12 concurrent cases queued on a single
            GPU (each must wait for all preceding requests to complete
            before the GPU starts on it).
    """

    def __init__(self, base_url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._base_url = base_url
        api_base = _ollama_api_base(base_url)
        self.chat = _Chat(api_base, timeout)


__all__ = [
    "is_ollama_url",
    "OllamaChatClient",
    "OllamaResponse",
    "_parse_ollama_response",  # exposed for tests
]
