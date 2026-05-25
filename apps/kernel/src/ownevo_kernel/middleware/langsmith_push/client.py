"""Thin wrapper over `langsmith.Client.push_prompt`.

Owns three concerns the rest of the kernel shouldn't: building the
LangChain prompt object from a plain instruction string, calling
`push_prompt`, and translating the langsmith exception hierarchy into
the adapter's own typed errors (see MAPPING_PUSH.md). Returns a
`PushResult` carrying the commit URL + parsed commit hash.

The langsmith SDK is synchronous; callers offload to a thread (the
route uses `asyncio.to_thread`) so the event loop stays free.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import (
    LangSmithAuthError,
    LangSmithConflictError,
    LangSmithNetworkError,
    LangSmithNotFoundError,
    LangSmithPushError,
    LangSmithRateLimitError,
)


@dataclass(frozen=True)
class PushResult:
    """Outcome of a successful push.

    `commit_url` is the full LangSmith URL `push_prompt` returned;
    `commit_hash` is the trailing path segment (the commit id), or the
    full URL if the shape was unexpected. `prompt_id` echoes the
    identifier pushed to, for the audit payload.
    """

    prompt_id: str
    commit_url: str
    commit_hash: str


def _parse_commit_hash(url: str) -> str:
    """Pull the commit hash (trailing path segment) out of the URL.

    Defensive: LangSmith returns
    `https://.../prompts/<name>/<commit_hash>`, but if the shape ever
    changes we fall back to the full URL rather than failing the ship —
    the audit entry still has a usable reference either way.
    """
    trimmed = url.rstrip("/")
    tail = trimmed.rsplit("/", 1)[-1] if "/" in trimmed else trimmed
    return tail or url


def push_fix(
    *,
    api_key: str,
    prompt_id: str,
    instruction_text: str,
    commit_description: str,
    api_url: str | None = None,
) -> PushResult:
    """Push an approved instruction fix as a new LangSmith prompt commit.

    Wraps `instruction_text` as a single-system-message
    `ChatPromptTemplate` (the minimal shape LangSmith accepts) and
    pushes it under `prompt_id`. Raises an adapter error subclass on any
    failure; never leaks a `langsmith` exception.
    """
    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langsmith import Client
        from langsmith import utils as ls_utils
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise LangSmithPushError(
            "LangSmith fix delivery requires the `langsmith` extra "
            "(langsmith + langchain-core).",
        ) from exc

    client = Client(api_key=api_key, api_url=api_url)
    template = ChatPromptTemplate.from_messages([("system", instruction_text)])

    try:
        url = client.push_prompt(
            prompt_id,
            object=template,
            commit_description=commit_description[:100],
        )
    except ls_utils.LangSmithAuthError as exc:
        raise LangSmithAuthError(str(exc)) from exc
    except ls_utils.LangSmithNotFoundError as exc:
        raise LangSmithNotFoundError(str(exc)) from exc
    except ls_utils.LangSmithConflictError as exc:
        raise LangSmithConflictError(str(exc)) from exc
    except ls_utils.LangSmithRateLimitError as exc:
        raise LangSmithRateLimitError(str(exc)) from exc
    except ls_utils.LangSmithConnectionError as exc:
        raise LangSmithNetworkError(str(exc)) from exc
    except ls_utils.LangSmithError as exc:
        raise LangSmithPushError(str(exc)) from exc

    return PushResult(
        prompt_id=prompt_id,
        commit_url=url,
        commit_hash=_parse_commit_hash(url),
    )


def verify_api_key(*, api_key: str, api_url: str | None = None) -> None:
    """Check that the key authenticates against LangSmith.

    Performs one cheap authenticated read. Returns None on success;
    raises `LangSmithAuthError` when the key is rejected, or another
    adapter error on network / API failure. Used by the Settings
    "test connection" action so a stored key can be validated without
    pushing anything.
    """
    try:
        from langsmith import Client
        from langsmith import utils as ls_utils
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise LangSmithPushError(
            "LangSmith integration requires the `langsmith` extra.",
        ) from exc

    client = Client(api_key=api_key, api_url=api_url)
    try:
        # Force a single authenticated request. list_prompts is paginated
        # and lazy, so we pull one item to actually hit the API.
        next(iter(client.list_prompts(limit=1)), None)
    except ls_utils.LangSmithAuthError as exc:
        raise LangSmithAuthError(str(exc)) from exc
    except ls_utils.LangSmithConnectionError as exc:
        raise LangSmithNetworkError(str(exc)) from exc
    except ls_utils.LangSmithError as exc:
        raise LangSmithPushError(str(exc)) from exc
