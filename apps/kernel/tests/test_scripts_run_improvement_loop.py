"""Unit tests for `scripts/run_improvement_loop.py` trace-extraction logic (BL.3).

The full BL.3 round-trip needs Postgres + Docker + a live LLM endpoint;
those are exercised in CI / local dogfooding, not pytest. The pure
function this test pins is `_extract_latest_write_skill` — it walks the
trace events emitted by the agent and produces the proposal payload
that `persist_gate_run` consumes.

Why a unit test on this helper specifically: a silent regression here
would surface as "agent ran but nothing got gated" or "wrong skill
got gated" — both hard to diagnose from outside the script.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ownevo_format import ToolCallResult, ToolCallStart

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from scripts.run_improvement_loop import (  # noqa: E402
    _extract_latest_write_skill,
    parse_args,
)

_TRACE_ID = uuid4()
_NOW = datetime.now(UTC)


def _start(call_id: str, name: str, args: dict) -> ToolCallStart:
    return ToolCallStart(
        event_id=uuid4(),
        trace_id=_TRACE_ID,
        timestamp=_NOW,
        type="tool_call_start",
        call_id=call_id,
        name=name,
        args=args,
    )


def _result(
    call_id: str,
    name: str,
    *,
    status: str = "ok",
    output: object = None,
    error: str | None = None,
) -> ToolCallResult:
    return ToolCallResult(
        event_id=uuid4(),
        trace_id=_TRACE_ID,
        timestamp=_NOW,
        type="tool_call_result",
        call_id=call_id,
        name=name,
        status=status,
        output=output,
        duration_ms=0,
        error=error,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_returns_latest_successful_write_skill():
    version_id = uuid4()
    events = [
        _start("c1", "read_skill", {"skill_id": "m5.baseline.v1.predictor"}),
        _result(
            "c1",
            "read_skill",
            output={"found": True, "skill_id": "m5.baseline.v1.predictor"},
        ),
        _start(
            "c2",
            "write_skill",
            {
                "skill_id": "m5.baseline.v1.predictor",
                "content": "BODY-V2",
                "diff_summary": "tweak clip floor",
            },
        ),
        _result(
            "c2",
            "write_skill",
            output={
                "skill_id": "m5.baseline.v1.predictor",
                "version_id": str(version_id),
                "version_seq": 2,
            },
        ),
    ]

    proposal = _extract_latest_write_skill(events)
    assert proposal is not None
    assert proposal.skill_id == "m5.baseline.v1.predictor"
    assert proposal.content == "BODY-V2"
    assert proposal.diff_summary == "tweak clip floor"
    assert proposal.version_id == version_id
    assert proposal.version_seq == 2


# ---------------------------------------------------------------------------
# No write_skill at all → None
# ---------------------------------------------------------------------------


def test_returns_none_when_agent_only_read():
    events = [
        _start("c1", "read_skill", {"skill_id": "m5.baseline.v1.predictor"}),
        _result(
            "c1",
            "read_skill",
            output={"found": True, "skill_id": "m5.baseline.v1.predictor"},
        ),
    ]
    assert _extract_latest_write_skill(events) is None


# ---------------------------------------------------------------------------
# Errored write_skill is ignored
# ---------------------------------------------------------------------------


def test_ignores_errored_write_skill():
    events = [
        _start(
            "c1",
            "write_skill",
            {"skill_id": "m5.baseline.v1.predictor", "content": "BAD"},
        ),
        _result(
            "c1",
            "write_skill",
            status="error",
            output="SkillFormatError: kind mismatch",
            error="SkillFormatError: kind mismatch",
        ),
    ]
    assert _extract_latest_write_skill(events) is None


# ---------------------------------------------------------------------------
# Multiple writes — pick the latest
# ---------------------------------------------------------------------------


def test_picks_last_successful_write_when_multiple():
    v1 = uuid4()
    v2 = uuid4()
    events = [
        _start(
            "c1",
            "write_skill",
            {"skill_id": "m5.baseline.v1.predictor", "content": "FIRST"},
        ),
        _result(
            "c1",
            "write_skill",
            output={
                "skill_id": "m5.baseline.v1.predictor",
                "version_id": str(v1),
                "version_seq": 2,
            },
        ),
        _start(
            "c2",
            "write_skill",
            {"skill_id": "m5.baseline.v1.feature_engineer", "content": "SECOND"},
        ),
        _result(
            "c2",
            "write_skill",
            output={
                "skill_id": "m5.baseline.v1.feature_engineer",
                "version_id": str(v2),
                "version_seq": 2,
            },
        ),
    ]
    proposal = _extract_latest_write_skill(events)
    assert proposal is not None
    assert proposal.skill_id == "m5.baseline.v1.feature_engineer"
    assert proposal.content == "SECOND"
    assert proposal.version_id == v2


# ---------------------------------------------------------------------------
# Errored write between two successful — last *successful* wins
# ---------------------------------------------------------------------------


def test_skips_errored_write_between_successful_ones():
    v1 = uuid4()
    events = [
        _start(
            "c1",
            "write_skill",
            {"skill_id": "m5.baseline.v1.predictor", "content": "OK1"},
        ),
        _result(
            "c1",
            "write_skill",
            output={
                "skill_id": "m5.baseline.v1.predictor",
                "version_id": str(v1),
                "version_seq": 2,
            },
        ),
        _start(
            "c2",
            "write_skill",
            {"skill_id": "m5.baseline.v1.predictor", "content": "BAD"},
        ),
        _result(
            "c2",
            "write_skill",
            status="error",
            output="SkillFormatError",
            error="SkillFormatError",
        ),
    ]
    proposal = _extract_latest_write_skill(events)
    assert proposal is not None
    assert proposal.content == "OK1"
    assert proposal.version_id == v1


# ---------------------------------------------------------------------------
# Malformed output (e.g., missing version_id) → None, no crash
# ---------------------------------------------------------------------------


def test_returns_none_on_malformed_result_output():
    events = [
        _start(
            "c1",
            "write_skill",
            {"skill_id": "m5.baseline.v1.predictor", "content": "X"},
        ),
        _result(
            "c1",
            "write_skill",
            output={"skill_id": "m5.baseline.v1.predictor"},
        ),
    ]
    assert _extract_latest_write_skill(events) is None


# ---------------------------------------------------------------------------
# parse_args — new --api-format and --no-stream flags
# ---------------------------------------------------------------------------


def test_parse_args_defaults():
    args = parse_args([])
    assert args.api_format == "anthropic"
    assert args.no_stream is False
    # Default base URL for anthropic format
    assert "192.168.1.50:1234" in args.llm_base_url


def test_parse_args_openai_format_uses_ollama_default():
    args = parse_args(["--api-format", "openai"])
    assert args.api_format == "openai"
    assert "11434" in args.llm_base_url  # Ollama default port


def test_parse_args_openai_format_explicit_url():
    args = parse_args(["--api-format", "openai", "--llm-base-url", "http://myhost:8080/v1"])
    assert args.api_format == "openai"
    assert args.llm_base_url == "http://myhost:8080/v1"


def test_parse_args_no_stream_flag():
    args = parse_args(["--no-stream"])
    assert args.no_stream is True
    assert args.api_format == "anthropic"  # default still anthropic


def test_parse_args_anthropic_explicit_url_not_overridden_by_format():
    args = parse_args(["--llm-base-url", "http://myproxy:4000"])
    assert args.llm_base_url == "http://myproxy:4000"
    assert args.api_format == "anthropic"


def test_parse_args_env_var_api_format(monkeypatch):
    """ENV_LLM_API_FORMAT env var should set the default api_format."""
    monkeypatch.setenv("OWNEVO_LLM_API_FORMAT", "openai")
    args = parse_args([])
    assert args.api_format == "openai"
    assert "11434" in args.llm_base_url  # Ollama default when format=openai
