"""Typed errors for the LangSmith fix-delivery adapter.

The adapter is the seam between the kernel and the `langsmith` SDK.
Callers (the ship route) catch these ownEvo-side types and never see a
`langsmith` exception — so the kernel doesn't take a hard dependency on
the SDK's exception hierarchy at its call sites. See MAPPING_PUSH.md for
the langsmith.utils → adapter mapping.
"""

from __future__ import annotations


class LangSmithPushError(Exception):
    """Base for every fix-delivery failure. Generic / unclassified API error."""


class LangSmithAuthError(LangSmithPushError):
    """API key rejected (401/403). The stored credential is invalid or revoked."""


class LangSmithNotFoundError(LangSmithPushError):
    """The prompt identifier or workspace doesn't exist (404)."""


class LangSmithConflictError(LangSmithPushError):
    """The push conflicted with existing state (409)."""


class LangSmithRateLimitError(LangSmithPushError):
    """LangSmith throttled the request (429)."""


class LangSmithNetworkError(LangSmithPushError):
    """Connection-level failure reaching LangSmith (timeout, DNS, reset)."""
