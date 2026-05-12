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
from urllib.parse import urlparse, urlunparse

import httpx


# ---------------------------------------------------------------------------
# URL detection
# ---------------------------------------------------------------------------

OLLAMA_DEFAULT_PORT: int = 11434


def is_ollama_url(url: str) -> bool:
    """Return True when url points to an Ollama daemon (default port 11434)."""
    try:
        return urlparse(url).port == OLLAMA_DEFAULT_PORT
    except Exception:
        return False


def _ollama_api_base(url: str) -> str:
    """Strip /v1 suffix from an Ollama OpenAI-compat URL to get the daemon base.

    e.g. http://localhost:11434/v1 → http://localhost:11434

    Only strips when /v1 is the complete path component. Paths like /openai/v1
    or /api/v1 are left unchanged to avoid mangling reverse-proxy configurations.
    """
    parsed = urlparse(url.rstrip("/"))
    if parsed.path == "/v1":
        return urlunparse(parsed._replace(path=""))
    return url.rstrip("/")


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
    # Ollama returns HTTP 200 with {"error": "..."} for model-not-found and OOM.
    # raise_for_status() misses these; catch them explicitly before parsing.
    if "error" in data:
        raise RuntimeError(f"Ollama /api/chat error: {data['error']}")
    # Streaming chunks have done=false. If stream=False is not honoured by the
    # daemon, we'd silently parse an incomplete chunk as a full response.
    if data.get("done") is False:
        raise RuntimeError(
            "Ollama returned an incomplete response (done=false); "
            "the daemon may be ignoring stream=false or returned a streaming chunk."
        )

    msg = data.get("message") or {}
    raw_tool_calls: list[dict[str, Any]] = msg.get("tool_calls") or []
    finish_reason: str = data.get("done_reason") or "stop"

    tool_calls: list[_OllamaToolCall] = []
    for tc in raw_tool_calls:
        fn = tc.get("function") or {}
        name: str = fn.get("name") or ""
        args: Any = fn.get("arguments") or {}
        # Ollama normally returns arguments as a dict; OpenAI consumers expect a
        # JSON string. Some builds already serialize to a string — pass through.
        if isinstance(args, str):
            arguments_str = args
        else:
            arguments_str = json.dumps(args)
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


DEFAULT_TIMEOUT_SECONDS = 1800.0
"""Per-request httpx timeout for Ollama /api/chat calls.

Two distinct call sites with different timeout needs:

(a) A4.4 gate (12 concurrent cases on a single GPU): the last queued
case waits for ~11 earlier completions before being served. At ~10s
per case on qwen3:14b, that's ~120s of queue wait + per-case generation
time on top.

(b) τ³ loop agent (1 sequential request, large model): a single chat
turn on a 26-35B model can emit several thousand tokens. At ~30 tok/s
on a 35B MoE model, a single turn runs 3-5 minutes. Dense models
(e.g. qwen3.6:27b, 27B active params) are ~5-10× slower per token
AND may need ~5 min for Ollama to load the model from disk on first
request. The earlier 600s default tripped ReadTimeout on qwen3.6:27b
(model load + first turn exceeded 600s; wall time ~900s).

1800s (30 min) covers the worst case: 5 min load + 25 min generation
for a dense 27B model running at 3-5 tok/s with multi-thousand token
thinking chain. If a call legitimately exceeds 30 min the model is
wedged and nothing productive can be done anyway.
"""


class _Completions:
    def __init__(self, api_base: str, timeout: float) -> None:
        self._api_base = api_base
        # Shared client across all create() calls — avoids a TCP handshake per
        # request when 12 concurrent cases hit the same Ollama host.
        self._http = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,  # accepted for interface compat; Ollama /api/chat has no equivalent — not forwarded
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

        resp = await self._http.post(
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

    async def aclose(self) -> None:
        await self.completions.aclose()


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
        base_url: Ollama URL (e.g. http://localhost:11434/v1 or
            http://localhost:11434).  The /v1 suffix is stripped
            automatically before appending /api/chat.
        timeout: Per-request httpx timeout in seconds. Default 300s —
            generous enough for 12 concurrent cases queued on a single
            GPU (each must wait for all preceding requests to complete
            before the GPU starts on it).
    """

    def __init__(self, base_url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        api_base = _ollama_api_base(base_url)
        self.chat = _Chat(api_base, timeout)

    async def aclose(self) -> None:
        """Close the underlying httpx connection pool."""
        await self.chat.aclose()

    async def __aenter__(self) -> "OllamaChatClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()



__all__ = [
    "is_ollama_url",
    "OllamaChatClient",
    "OllamaResponse",
    "_parse_ollama_response",  # exposed for tests
]
