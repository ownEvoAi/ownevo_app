"""In-call conversation compaction for the BL.3 multi-turn agent loop.

Background
----------
A single ``run_improvement_loop.py`` invocation can drive 25+ tool-call
turns within one agent run. Each turn appends an assistant block
(tool_use + reasoning text) and a user block (tool_result content). The
runner doesn't trim or summarize between turns, so the conversation
grows monotonically until the model's context window can't hold it.
For local LMS Anthropic at 32k context with verbose tool results
(``read_skill`` returns full skill bodies, ``run_pipeline`` returns
trace + scores), overflow hits at turn 8-12 in practice — observed
during the 2026-05-08 free condition-D 30-day replay (28 context-size
errors across 30 iterations).

The biggest growth term is **tool_result content** — skill bodies,
pipeline traces, failure tables. Older results are usually stale by
the time the agent decides what to do; the agent acts on the most
recent state. So the cheapest compaction is mechanical: keep the
assistant blocks (the action history) verbatim and replace older
tool_result content with a short pointer.

Cross-iter ``past_attempts`` memory is bounded at 8 entries (~2 KB
total) — that's not the source of overflow. This module addresses the
**in-call** growth.

Design (MVP)
------------
* Pure mechanical replacement — no LLM call. Deterministic, fast,
  trivially testable.
* Identify the most recent ``keep_last_k`` ``tool_result`` user
  messages (Anthropic) or ``role=tool`` messages (OpenAI). Leave them
  untouched.
* For each older tool_result, replace its content with a short stub:
  ``[archived: tool_use_id={id}, original_size={n} bytes; full content
  omitted to fit context]``. Tool_use blocks in assistant messages
  stay intact so the model still sees the action history.
* Triggers only when total serialized size exceeds
  ``threshold_chars`` — small conversations pass through unchanged.
* The first user message (the kickoff) is always preserved — it
  carries the workflow_id + past_attempts block + agent instructions.
* System messages (OpenAI shape) are always preserved.

Future work (not in MVP)
------------------------
* FS-backed archive: write the original content to
  ``archive_dir/<uuid>.json`` and have the stub reference the path,
  so a future ``read_archived_turn(turn_index)`` tool could pull
  detail back into context if the agent decides it's needed.
* LLM-summary every N turns: ask the same model to write a paragraph
  summary of older turns and prepend it as a single ``assistant``
  message. Mastra does this with a buffer; we don't need a buffer
  because the loop is sequential and local — a synchronous summary
  call before the next turn is fine.
"""

from __future__ import annotations

import json
from typing import Any

DEFAULT_KEEP_LAST_K = 4
"""Number of most-recent tool_result user messages to keep verbatim.

4 covers the typical BL.3 happy path (read_skill → analyze → write_skill
→ run_pipeline) plus one turn of recovery headroom. Smaller values
free more context but risk dropping load-bearing recent state."""

DEFAULT_THRESHOLD_CHARS = 80_000
"""Compact only when serialized conversation exceeds this many chars.

Approximate token count = chars / 4. 80k chars ≈ 20k tokens — leaves
~12k tokens of headroom on a 32k context window for the model's
response + tool definitions + system prompt. Below this threshold,
short conversations pass through unchanged so caching stays warm."""


def _compact_stub(tool_use_id: str, original_size: int) -> str:
    """Compact replacement text for an archived tool_result."""
    return (
        f"[archived: tool_use_id={tool_use_id}, "
        f"original_size={original_size} bytes; "
        f"full content omitted to fit context]"
    )


def _content_size(content: Any) -> int:
    """Approximate serialized size of a message content field."""
    if isinstance(content, str):
        return len(content)
    try:
        return len(json.dumps(content, default=str))
    except (TypeError, ValueError):
        return len(str(content))


def _messages_size_chars(messages: list[dict[str, Any]]) -> int:
    """Total approximate size of a messages list in characters."""
    return sum(_content_size(m.get("content", "")) for m in messages)


# ---------------------------------------------------------------------------
# Anthropic message shape
# ---------------------------------------------------------------------------


def compact_anthropic_messages(
    messages: list[dict[str, Any]],
    *,
    keep_last_k: int = DEFAULT_KEEP_LAST_K,
    threshold_chars: int = DEFAULT_THRESHOLD_CHARS,
) -> list[dict[str, Any]]:
    """Return a compacted copy of an Anthropic-format messages list.

    Anthropic shape:
      * ``{"role": "user", "content": <str or list-of-blocks>}``
      * ``{"role": "assistant", "content": [<text/tool_use blocks>]}``

    Tool results live in ``user`` messages whose ``content`` is a list
    containing one or more ``{"type": "tool_result", "tool_use_id":
    ..., "content": ...}`` blocks. The kickoff user message has a
    plain string content and is left untouched.

    Compaction policy:
      * If total size <= ``threshold_chars``, return ``messages``
        unchanged (same identity — caller can use ``is`` to detect
        no-op).
      * Otherwise, walk user messages in order. The *last*
        ``keep_last_k`` user messages whose content is a tool_result
        list stay verbatim. Older tool_result blocks have their
        ``content`` field replaced with a compact stub.
      * The first user message (the kickoff string) is always
        preserved.
    """
    if keep_last_k < 0:
        raise ValueError(f"keep_last_k must be >= 0; got {keep_last_k}")
    if threshold_chars < 0:
        raise ValueError(f"threshold_chars must be >= 0; got {threshold_chars}")

    if _messages_size_chars(messages) <= threshold_chars:
        return messages

    # Find indices of all tool_result user messages (skip the kickoff
    # string). The kickoff is identifiable by content being a string
    # rather than a list.
    tool_result_indices: list[int] = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            tool_result_indices.append(i)

    # Indices to compact: all but the most recent keep_last_k
    if len(tool_result_indices) <= keep_last_k:
        # Even after threshold trip, fewer-than-keep tool_results means
        # there's nothing to drop — caller probably needs a different fix
        # (bigger context, smaller per-iter cap).
        return messages

    compact_indices = set(tool_result_indices[:-keep_last_k]) if keep_last_k > 0 else set(tool_result_indices)

    # Build the compacted list. Each compacted user message gets its
    # tool_result blocks rewritten with stub content; the assistant
    # blocks above are preserved verbatim.
    out: list[dict[str, Any]] = []
    for i, msg in enumerate(messages):
        if i in compact_indices:
            new_content: list[dict[str, Any]] = []
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    original_size = _content_size(block.get("content", ""))
                    new_content.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.get("tool_use_id", ""),
                            "content": _compact_stub(
                                block.get("tool_use_id", ""), original_size
                            ),
                            **(
                                {"is_error": block["is_error"]}
                                if "is_error" in block
                                else {}
                            ),
                        }
                    )
                else:
                    new_content.append(block)
            out.append({**msg, "content": new_content})
        else:
            out.append(msg)
    return out


# ---------------------------------------------------------------------------
# OpenAI message shape
# ---------------------------------------------------------------------------


def compact_openai_messages(
    messages: list[dict[str, Any]],
    *,
    keep_last_k: int = DEFAULT_KEEP_LAST_K,
    threshold_chars: int = DEFAULT_THRESHOLD_CHARS,
) -> list[dict[str, Any]]:
    """Return a compacted copy of an OpenAI-format messages list.

    OpenAI shape (loop-relevant):
      * ``{"role": "system", "content": <str>}`` — preserved verbatim
      * ``{"role": "user", "content": <str>}`` — kickoff; preserved
      * ``{"role": "assistant", "content": <str>, "tool_calls": [...]}``
        — preserved (action history)
      * ``{"role": "tool", "tool_call_id": <id>, "content": <str>}``
        — compaction target

    Compaction policy mirrors the Anthropic path: keep the most recent
    ``keep_last_k`` tool messages verbatim; older tool messages have
    their ``content`` replaced with a compact stub.
    """
    if keep_last_k < 0:
        raise ValueError(f"keep_last_k must be >= 0; got {keep_last_k}")
    if threshold_chars < 0:
        raise ValueError(f"threshold_chars must be >= 0; got {threshold_chars}")

    if _messages_size_chars(messages) <= threshold_chars:
        return messages

    tool_indices: list[int] = [
        i for i, m in enumerate(messages) if m.get("role") == "tool"
    ]
    if len(tool_indices) <= keep_last_k:
        return messages

    compact_indices = set(tool_indices[:-keep_last_k]) if keep_last_k > 0 else set(tool_indices)

    out: list[dict[str, Any]] = []
    for i, msg in enumerate(messages):
        if i in compact_indices:
            tool_call_id = msg.get("tool_call_id", "")
            original_size = _content_size(msg.get("content", ""))
            out.append(
                {
                    **msg,
                    "content": _compact_stub(tool_call_id, original_size),
                }
            )
        else:
            out.append(msg)
    return out
