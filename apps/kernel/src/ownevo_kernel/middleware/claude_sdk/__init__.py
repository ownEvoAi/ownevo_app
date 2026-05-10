"""Claude Agent SDK middleware — manual loop over Anthropic Messages API.

Public surface (everything an outside caller needs):

  run_agent_turn(client, *, system, user_message, kernel_context, collector, ...)
      Drive one agent run with the 5 kernel tools; emits AgentEvents
      into the collector. Returns AgentTurnResult.

  run_agent_turn_openai(client, *, system, user_message, kernel_context, ...)
      Same contract, but for OpenAI-compatible backends (Ollama, LMS, etc.)
      that speak /v1/chat/completions instead of /v1/messages.

  run_agent_turn_ollama(client, *, system, user_message, kernel_context, ...)
      Same contract, but routes through OllamaChatClient (native /api/chat,
      non-streaming). Lets options.think=false and other Ollama-native options
      pass through reliably. Use with OllamaChatClient from eval_runner.ollama_native.

  KernelContext(conn, sandbox, actor, default_workflow_id)
      Bundle of dependencies the tools execute against.

  AgentTurnResult(stop_reason, iterations, final_text, ...)
      Lifecycle summary from one run. The trace itself lives on the
      collector.

The internal split — `tool_definitions`, `event_router`, `runner` —
exists so each piece is independently testable. Don't import from
those submodules in caller code; use this package's namespace.
"""

from .event_router import (
    FinalizedBlock,
    FinalizedToolCall,
    StreamEventRouter,
)
from .runner import (
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MAX_TOKENS_OPENAI,
    DEFAULT_MODEL,
    AgentTurnResult,
    AnthropicClientProtocol,
    run_agent_turn,
    run_agent_turn_ollama,
    run_agent_turn_openai,
)
from .tool_definitions import (
    KernelContext,
    ToolDispatchResult,
    dispatch_tool,
    kernel_tool_definitions,
    kernel_tool_definitions_openai,
)

__all__ = [
    "DEFAULT_MAX_ITERATIONS",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MAX_TOKENS_OPENAI",
    "DEFAULT_MODEL",
    "AgentTurnResult",
    "AnthropicClientProtocol",
    "FinalizedBlock",
    "FinalizedToolCall",
    "KernelContext",
    "StreamEventRouter",
    "ToolDispatchResult",
    "dispatch_tool",
    "kernel_tool_definitions",
    "kernel_tool_definitions_openai",
    "run_agent_turn",
    "run_agent_turn_ollama",
    "run_agent_turn_openai",
]
