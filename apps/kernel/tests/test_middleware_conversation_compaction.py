"""Tests for in-call conversation compaction.

Pins the mechanical drop-old-tool-results behavior used by both runners
to keep BL.3 multi-turn loops within the model's context window.
"""

from __future__ import annotations

import pytest

from ownevo_kernel.middleware.claude_sdk.conversation_compaction import (
    DEFAULT_KEEP_LAST_K,
    DEFAULT_THRESHOLD_CHARS,
    compact_anthropic_messages,
    compact_openai_messages,
)


# ---------------------------------------------------------------------------
# Helpers — build messages that look like the real ones
# ---------------------------------------------------------------------------


def _anthropic_kickoff(text: str = "kickoff") -> dict:
    return {"role": "user", "content": text}


def _anthropic_assistant(blocks: list[dict] | None = None) -> dict:
    return {"role": "assistant", "content": blocks or [{"type": "text", "text": "ok"}]}


def _anthropic_tool_result(tool_use_id: str, content: str) -> dict:
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
            }
        ],
    }


def _openai_system(text: str = "sys") -> dict:
    return {"role": "system", "content": text}


def _openai_user(text: str = "kickoff") -> dict:
    return {"role": "user", "content": text}


def _openai_assistant(text: str = "ok", tool_calls: list[dict] | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": text}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return msg


def _openai_tool(tool_call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


class TestCompactAnthropicMessages:
    def test_under_threshold_returns_same_identity(self):
        msgs = [
            _anthropic_kickoff(),
            _anthropic_assistant(),
            _anthropic_tool_result("t1", "tiny"),
        ]
        out = compact_anthropic_messages(msgs, threshold_chars=10_000)
        assert out is msgs  # identity preserved as a no-op signal

    def test_short_with_low_threshold_compacts_old_tool_results(self):
        big = "x" * 1000
        msgs = [
            _anthropic_kickoff("kickoff"),
            _anthropic_assistant(),
            _anthropic_tool_result("t1", big),  # OLD
            _anthropic_assistant(),
            _anthropic_tool_result("t2", big),  # OLD
            _anthropic_assistant(),
            _anthropic_tool_result("t3", big),  # KEEP
            _anthropic_assistant(),
            _anthropic_tool_result("t4", big),  # KEEP
            _anthropic_assistant(),
            _anthropic_tool_result("t5", big),  # KEEP
            _anthropic_assistant(),
            _anthropic_tool_result("t6", big),  # KEEP
        ]
        # 6 tool_results, keep 4 → drop 2 oldest
        out = compact_anthropic_messages(msgs, threshold_chars=100, keep_last_k=4)
        # Original list untouched
        assert msgs[2]["content"][0]["content"] == big

        # Old tool_result 1 + 2 stubbed; 3-6 verbatim
        old_t1 = out[2]["content"][0]
        old_t2 = out[4]["content"][0]
        assert old_t1["content"].startswith("[archived: tool_use_id=t1")
        assert "1000 bytes" in old_t1["content"]
        assert old_t2["content"].startswith("[archived: tool_use_id=t2")

        for keep_idx in (6, 8, 10, 12):
            assert out[keep_idx]["content"][0]["content"] == big

    def test_kickoff_string_message_preserved(self):
        big = "x" * 200
        msgs = [
            _anthropic_kickoff("kickoff string with workflow_id and past_attempts"),
            _anthropic_assistant(),
            _anthropic_tool_result("t1", big),
            _anthropic_assistant(),
            _anthropic_tool_result("t2", big),
            _anthropic_assistant(),
            _anthropic_tool_result("t3", big),
            _anthropic_assistant(),
            _anthropic_tool_result("t4", big),
            _anthropic_assistant(),
            _anthropic_tool_result("t5", big),
        ]
        # 5 tool_results, keep 4 → drop 1 oldest
        out = compact_anthropic_messages(msgs, threshold_chars=100, keep_last_k=4)
        assert out[0] == msgs[0]  # kickoff string untouched
        assert out[2]["content"][0]["content"].startswith("[archived: tool_use_id=t1")

    def test_assistant_blocks_preserved_verbatim(self):
        big = "x" * 1000
        assistant_with_tool_use = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I'll read the skill"},
                {
                    "type": "tool_use",
                    "id": "tu1",
                    "name": "read_skill",
                    "input": {"skill_id": "s1"},
                },
            ],
        }
        msgs = [
            _anthropic_kickoff(),
            assistant_with_tool_use,
            _anthropic_tool_result("tu1", big),
            _anthropic_assistant(),
            _anthropic_tool_result("tu2", big),
            _anthropic_assistant(),
            _anthropic_tool_result("tu3", big),
            _anthropic_assistant(),
            _anthropic_tool_result("tu4", big),
            _anthropic_assistant(),
            _anthropic_tool_result("tu5", big),
        ]
        out = compact_anthropic_messages(msgs, threshold_chars=100, keep_last_k=4)
        # Assistant message with tool_use stays verbatim
        assert out[1] == assistant_with_tool_use

    def test_keep_last_k_zero_compacts_all(self):
        big = "x" * 500
        msgs = [
            _anthropic_kickoff(),
            _anthropic_assistant(),
            _anthropic_tool_result("t1", big),
            _anthropic_assistant(),
            _anthropic_tool_result("t2", big),
        ]
        out = compact_anthropic_messages(msgs, threshold_chars=100, keep_last_k=0)
        assert out[2]["content"][0]["content"].startswith("[archived: tool_use_id=t1")
        assert out[4]["content"][0]["content"].startswith("[archived: tool_use_id=t2")

    def test_fewer_results_than_keep_returns_unchanged(self):
        # 3 tool_results, keep 4 → nothing to drop
        msgs = [
            _anthropic_kickoff(),
            _anthropic_assistant(),
            _anthropic_tool_result("t1", "x" * 5000),
            _anthropic_assistant(),
            _anthropic_tool_result("t2", "x" * 5000),
            _anthropic_assistant(),
            _anthropic_tool_result("t3", "x" * 5000),
        ]
        out = compact_anthropic_messages(msgs, threshold_chars=100, keep_last_k=4)
        # Even though we tripped the threshold, nothing was dropped
        for i in (2, 4, 6):
            assert out[i]["content"][0]["content"] == "x" * 5000

    def test_is_error_flag_preserved_on_compacted(self):
        big = "x" * 1000
        err_block = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "te1",
                    "content": big,
                    "is_error": True,
                }
            ],
        }
        msgs = [
            _anthropic_kickoff(),
            _anthropic_assistant(),
            err_block,
            _anthropic_assistant(),
            _anthropic_tool_result("t2", big),
            _anthropic_assistant(),
            _anthropic_tool_result("t3", big),
            _anthropic_assistant(),
            _anthropic_tool_result("t4", big),
            _anthropic_assistant(),
            _anthropic_tool_result("t5", big),
        ]
        out = compact_anthropic_messages(msgs, threshold_chars=100, keep_last_k=4)
        compacted_block = out[2]["content"][0]
        assert compacted_block["is_error"] is True

    def test_invalid_keep_last_k_raises(self):
        with pytest.raises(ValueError):
            compact_anthropic_messages([], keep_last_k=-1)

    def test_invalid_threshold_raises(self):
        with pytest.raises(ValueError):
            compact_anthropic_messages([], threshold_chars=-1)


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


class TestCompactOpenAIMessages:
    def test_under_threshold_returns_same_identity(self):
        msgs = [
            _openai_system(),
            _openai_user(),
            _openai_assistant(),
            _openai_tool("t1", "tiny"),
        ]
        out = compact_openai_messages(msgs, threshold_chars=10_000)
        assert out is msgs

    def test_compacts_old_tool_messages(self):
        big = "x" * 1000
        msgs = [
            _openai_system(),
            _openai_user(),
            _openai_assistant(),
            _openai_tool("t1", big),  # OLD
            _openai_assistant(),
            _openai_tool("t2", big),  # OLD
            _openai_assistant(),
            _openai_tool("t3", big),  # KEEP
            _openai_assistant(),
            _openai_tool("t4", big),  # KEEP
            _openai_assistant(),
            _openai_tool("t5", big),  # KEEP
            _openai_assistant(),
            _openai_tool("t6", big),  # KEEP
        ]
        out = compact_openai_messages(msgs, threshold_chars=100, keep_last_k=4)
        # OLD tool messages compacted (OpenAI stubs use tool_call_id label)
        assert out[3]["content"].startswith("[archived: tool_call_id=t1")
        assert "1000 bytes" in out[3]["content"]
        assert out[5]["content"].startswith("[archived: tool_call_id=t2")
        # KEEP tool messages verbatim
        for keep_idx in (7, 9, 11, 13):
            assert out[keep_idx]["content"] == big

    def test_system_user_assistant_preserved(self):
        big = "x" * 1000
        msgs = [
            _openai_system("system prompt"),
            _openai_user("kickoff"),
            _openai_assistant(
                "I'll call the tool",
                tool_calls=[
                    {
                        "id": "tu1",
                        "type": "function",
                        "function": {"name": "read_skill", "arguments": "{}"},
                    }
                ],
            ),
            _openai_tool("tu1", big),
            _openai_assistant(),
            _openai_tool("tu2", big),
            _openai_assistant(),
            _openai_tool("tu3", big),
            _openai_assistant(),
            _openai_tool("tu4", big),
            _openai_assistant(),
            _openai_tool("tu5", big),
        ]
        out = compact_openai_messages(msgs, threshold_chars=100, keep_last_k=4)
        # System + user + assistant preserved
        assert out[0]["role"] == "system" and out[0]["content"] == "system prompt"
        assert out[1]["role"] == "user" and out[1]["content"] == "kickoff"
        assert out[2]["role"] == "assistant"
        assert "tool_calls" in out[2]

    def test_keep_last_k_zero_compacts_all(self):
        big = "x" * 500
        msgs = [
            _openai_system(),
            _openai_user(),
            _openai_assistant(),
            _openai_tool("t1", big),
            _openai_assistant(),
            _openai_tool("t2", big),
        ]
        out = compact_openai_messages(msgs, threshold_chars=100, keep_last_k=0)
        assert out[3]["content"].startswith("[archived: tool_call_id=t1")
        assert out[5]["content"].startswith("[archived: tool_call_id=t2")

    def test_fewer_tool_messages_than_keep_returns_unchanged(self):
        msgs = [
            _openai_system(),
            _openai_user(),
            _openai_assistant(),
            _openai_tool("t1", "x" * 5000),
            _openai_assistant(),
            _openai_tool("t2", "x" * 5000),
            _openai_assistant(),
            _openai_tool("t3", "x" * 5000),
        ]
        out = compact_openai_messages(msgs, threshold_chars=100, keep_last_k=4)
        for i in (3, 5, 7):
            assert out[i]["content"] == "x" * 5000

    def test_invalid_keep_last_k_raises(self):
        with pytest.raises(ValueError):
            compact_openai_messages([], keep_last_k=-1)

    def test_invalid_threshold_raises(self):
        with pytest.raises(ValueError):
            compact_openai_messages([], threshold_chars=-1)


def test_default_constants_in_reasonable_range():
    # Sanity — defaults shouldn't drift to absurd values silently
    assert 1 <= DEFAULT_KEEP_LAST_K <= 16
    assert 10_000 <= DEFAULT_THRESHOLD_CHARS <= 1_000_000


# ---------------------------------------------------------------------------
# Identity contract — "fewer results than keep" branch
# ---------------------------------------------------------------------------


def test_anthropic_fewer_results_than_keep_returns_same_identity():
    """Even over threshold, if n <= keep_last_k the same list object is returned."""
    msgs = [
        _anthropic_kickoff(),
        _anthropic_assistant(),
        _anthropic_tool_result("t1", "x" * 5000),
        _anthropic_assistant(),
        _anthropic_tool_result("t2", "x" * 5000),
        _anthropic_assistant(),
        _anthropic_tool_result("t3", "x" * 5000),
    ]
    out = compact_anthropic_messages(msgs, threshold_chars=100, keep_last_k=4)
    assert out is msgs


def test_openai_fewer_tool_messages_than_keep_returns_same_identity():
    """Even over threshold, if n <= keep_last_k the same list object is returned."""
    msgs = [
        _openai_system(),
        _openai_user(),
        _openai_assistant(),
        _openai_tool("t1", "x" * 5000),
        _openai_assistant(),
        _openai_tool("t2", "x" * 5000),
    ]
    out = compact_openai_messages(msgs, threshold_chars=100, keep_last_k=4)
    assert out is msgs


# ---------------------------------------------------------------------------
# Idempotency — compacting an already-compacted conversation
# ---------------------------------------------------------------------------


def test_anthropic_compaction_idempotent():
    """Re-compacting preserves the original stub unchanged (no size corruption)."""
    big = "x" * 1000
    msgs = [
        _anthropic_kickoff(),
        _anthropic_assistant(),
        _anthropic_tool_result("t1", big),
        _anthropic_assistant(),
        _anthropic_tool_result("t2", big),
        _anthropic_assistant(),
        _anthropic_tool_result("t3", big),
        _anthropic_assistant(),
        _anthropic_tool_result("t4", big),
        _anthropic_assistant(),
        _anthropic_tool_result("t5", big),
    ]
    first = compact_anthropic_messages(msgs, threshold_chars=100, keep_last_k=4)
    second = compact_anthropic_messages(first, threshold_chars=100, keep_last_k=4)
    first_stub = first[2]["content"][0]["content"]
    second_stub = second[2]["content"][0]["content"]
    assert first_stub == second_stub, f"Re-compaction changed stub: {first_stub!r} -> {second_stub!r}"
    assert "1000 bytes" in first_stub


# ---------------------------------------------------------------------------
# List-typed tool_result content (Anthropic allows list content in tool_result)
# ---------------------------------------------------------------------------


def test_anthropic_tool_result_with_list_content_gets_stubbed():
    """tool_result whose content is a list of blocks (not a string) is correctly sized and stubbed."""
    import json as _json
    list_content = [{"type": "text", "text": "x" * 500}]
    expected_size = len(_json.dumps(list_content))
    msgs = [
        _anthropic_kickoff(),
        _anthropic_assistant(),
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tl1", "content": list_content}]},
        _anthropic_assistant(),
        _anthropic_tool_result("t2", "x" * 500),
        _anthropic_assistant(),
        _anthropic_tool_result("t3", "x" * 500),
        _anthropic_assistant(),
        _anthropic_tool_result("t4", "x" * 500),
        _anthropic_assistant(),
        _anthropic_tool_result("t5", "x" * 500),
    ]
    out = compact_anthropic_messages(msgs, threshold_chars=100, keep_last_k=4)
    compacted = out[2]["content"][0]
    assert isinstance(compacted["content"], str)
    assert str(expected_size) in compacted["content"]
    assert compacted["content"].startswith("[archived: tool_use_id=tl1")


# ---------------------------------------------------------------------------
# Mixed-block user message (text block alongside tool_result block)
# ---------------------------------------------------------------------------


def test_anthropic_non_tool_result_blocks_in_user_message_preserved():
    """Non-tool_result blocks in a compacted user message are passed through verbatim."""
    big = "x" * 1000
    mixed_msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "preceding user text"},
            {"type": "tool_result", "tool_use_id": "tm1", "content": big},
        ],
    }
    msgs = [
        _anthropic_kickoff(),
        _anthropic_assistant(),
        mixed_msg,
        _anthropic_assistant(),
        _anthropic_tool_result("t2", big),
        _anthropic_assistant(),
        _anthropic_tool_result("t3", big),
        _anthropic_assistant(),
        _anthropic_tool_result("t4", big),
        _anthropic_assistant(),
        _anthropic_tool_result("t5", big),
    ]
    out = compact_anthropic_messages(msgs, threshold_chars=100, keep_last_k=4)
    compacted_content = out[2]["content"]
    assert compacted_content[0] == {"type": "text", "text": "preceding user text"}
    assert compacted_content[1]["content"].startswith("[archived: tool_use_id=tm1")


# ---------------------------------------------------------------------------
# threshold_chars=0 — "always compact" edge case
# ---------------------------------------------------------------------------


def test_anthropic_threshold_zero_always_compacts():
    """threshold_chars=0 triggers compaction on any non-empty conversation."""
    big = "x" * 500
    msgs = [
        _anthropic_kickoff(),
        _anthropic_assistant(),
        _anthropic_tool_result("t1", big),
        _anthropic_assistant(),
        _anthropic_tool_result("t2", big),
        _anthropic_assistant(),
        _anthropic_tool_result("t3", big),
        _anthropic_assistant(),
        _anthropic_tool_result("t4", big),
        _anthropic_assistant(),
        _anthropic_tool_result("t5", big),
    ]
    out = compact_anthropic_messages(msgs, threshold_chars=0, keep_last_k=4)
    assert out[2]["content"][0]["content"].startswith("[archived: tool_use_id=t1")
    for keep_idx in (4, 6, 8, 10):
        assert out[keep_idx]["content"][0]["content"] == big


# ---------------------------------------------------------------------------
# Exact n == keep_last_k boundary — must return same identity
# ---------------------------------------------------------------------------


def test_anthropic_exactly_keep_last_k_results_returns_same_identity():
    """When n == keep_last_k, nothing is dropped and the same list object is returned."""
    big = "x" * 5000
    msgs = [
        _anthropic_kickoff(),
        _anthropic_assistant(),
        _anthropic_tool_result("t1", big),
        _anthropic_assistant(),
        _anthropic_tool_result("t2", big),
        _anthropic_assistant(),
        _anthropic_tool_result("t3", big),
        _anthropic_assistant(),
        _anthropic_tool_result("t4", big),  # exactly 4 == keep_last_k
    ]
    out = compact_anthropic_messages(msgs, threshold_chars=100, keep_last_k=4)
    assert out is msgs


def test_openai_exactly_keep_last_k_tool_messages_returns_same_identity():
    """When n == keep_last_k, nothing is dropped and the same list object is returned."""
    big = "x" * 5000
    msgs = [
        _openai_system(),
        _openai_user(),
        _openai_assistant(),
        _openai_tool("t1", big),
        _openai_assistant(),
        _openai_tool("t2", big),
        _openai_assistant(),
        _openai_tool("t3", big),
        _openai_assistant(),
        _openai_tool("t4", big),  # exactly 4 == keep_last_k
    ]
    out = compact_openai_messages(msgs, threshold_chars=100, keep_last_k=4)
    assert out is msgs
