"""Unit tests for the LangSmith push adapter.

The langsmith SDK `Client.push_prompt` is mocked — no network, no
account. We assert: the happy path returns a parsed PushResult; each
langsmith exception maps to the right adapter error; and the prompt
object is built from the instruction text.

Skipped unless the `langsmith` extra is installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("langsmith", reason="langsmith extra not installed")
pytest.importorskip("langchain_core", reason="langchain-core not installed")

from ownevo_kernel.middleware.langsmith_push import (  # noqa: E402
    LangSmithAuthError,
    LangSmithConflictError,
    LangSmithNetworkError,
    LangSmithNotFoundError,
    LangSmithPushError,
    LangSmithRateLimitError,
    push_fix,
)
from ownevo_kernel.middleware.langsmith_push.client import (  # noqa: E402
    _parse_commit_hash,
)


def _patch_push(monkeypatch, *, returns=None, raises=None, capture=None):
    """Patch langsmith.Client.push_prompt with a fake."""
    from langsmith import Client

    def fake_push(self, prompt_identifier, **kwargs):  # noqa: ANN001
        if capture is not None:
            capture["prompt_identifier"] = prompt_identifier
            capture.update(kwargs)
        if raises is not None:
            raise raises
        return returns

    monkeypatch.setattr(Client, "push_prompt", fake_push)


# --- commit-hash parsing ---------------------------------------------------


def test_parse_commit_hash_trailing_segment() -> None:
    url = "https://smith.langchain.com/prompts/demand-forecast/abc123def"
    assert _parse_commit_hash(url) == "abc123def"


def test_parse_commit_hash_trailing_slash() -> None:
    url = "https://smith.langchain.com/prompts/demand-forecast/abc123/"
    assert _parse_commit_hash(url) == "abc123"


def test_parse_commit_hash_fallback_to_url() -> None:
    weird = "no-slashes-here"
    assert _parse_commit_hash(weird) == "no-slashes-here"


# --- happy path ------------------------------------------------------------


def test_push_fix_returns_parsed_result(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://smith.langchain.com/prompts/demand-forecast/commit789"
    capture: dict = {}
    _patch_push(monkeypatch, returns=url, capture=capture)

    result = push_fix(
        api_key="lsv2_pt_x",
        prompt_id="demand-forecast",
        instruction_text="Always cross-check the holiday calendar.",
        commit_description="Approved fix: holiday markdown false-negatives",
    )
    assert result.prompt_id == "demand-forecast"
    assert result.commit_url == url
    assert result.commit_hash == "commit789"
    # The instruction text was wrapped into the pushed object.
    assert capture["prompt_identifier"] == "demand-forecast"
    assert capture["object"] is not None


def test_commit_description_truncated_to_100(monkeypatch: pytest.MonkeyPatch) -> None:
    capture: dict = {}
    _patch_push(
        monkeypatch,
        returns="https://smith.langchain.com/prompts/p/c",
        capture=capture,
    )
    push_fix(
        api_key="k",
        prompt_id="p",
        instruction_text="fix",
        commit_description="x" * 250,
    )
    assert len(capture["commit_description"]) == 100


# --- error mapping ---------------------------------------------------------


@pytest.mark.parametrize(
    ("ls_exc_name", "expected"),
    [
        ("LangSmithAuthError", LangSmithAuthError),
        ("LangSmithNotFoundError", LangSmithNotFoundError),
        ("LangSmithConflictError", LangSmithConflictError),
        ("LangSmithRateLimitError", LangSmithRateLimitError),
        ("LangSmithConnectionError", LangSmithNetworkError),
        ("LangSmithError", LangSmithPushError),
    ],
)
def test_error_mapping(
    monkeypatch: pytest.MonkeyPatch, ls_exc_name: str, expected: type
) -> None:
    from langsmith import utils as ls_utils

    ls_exc_cls = getattr(ls_utils, ls_exc_name)
    _patch_push(monkeypatch, raises=ls_exc_cls("boom"))

    with pytest.raises(expected):
        push_fix(
            api_key="k",
            prompt_id="p",
            instruction_text="fix",
            commit_description="desc",
        )


def test_generic_langsmith_error_maps_to_push_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An APIError (subclass of LangSmithError) falls through to the base.
    from langsmith import utils as ls_utils

    _patch_push(monkeypatch, raises=ls_utils.LangSmithAPIError("500"))
    with pytest.raises(LangSmithPushError):
        push_fix(api_key="k", prompt_id="p", instruction_text="f", commit_description="d")


# --- pushed object + plumbing ----------------------------------------------


def test_pushed_object_carries_instruction_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture: dict = {}
    _patch_push(
        monkeypatch,
        returns="https://smith.langchain.com/prompts/p/c",
        capture=capture,
    )
    push_fix(
        api_key="k",
        prompt_id="p",
        instruction_text="Cross-check the holiday calendar.",
        commit_description="d",
    )
    template = capture["object"]
    # The system message text is recoverable from the ChatPromptTemplate.
    assert "holiday calendar" in str(template)


def test_api_url_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}
    from langsmith import Client

    def spy_init(self, *args, **kwargs):  # noqa: ANN001
        # No-op init (no network probe). push_prompt is mocked below and
        # doesn't read instance state, so the client needs no real setup.
        seen["api_url"] = kwargs.get("api_url")

    monkeypatch.setattr(Client, "__init__", spy_init)
    monkeypatch.setattr(
        Client, "push_prompt", lambda self, pid, **kw: "https://self.hosted/prompts/p/c"
    )
    push_fix(
        api_key="k",
        prompt_id="p",
        instruction_text="f",
        commit_description="d",
        api_url="https://self.hosted/api",
    )
    assert seen["api_url"] == "https://self.hosted/api"


def test_result_echoes_prompt_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_push(monkeypatch, returns="https://smith.langchain.com/prompts/my-prompt/c1")
    result = push_fix(
        api_key="k",
        prompt_id="my-prompt",
        instruction_text="f",
        commit_description="d",
    )
    assert result.prompt_id == "my-prompt"
