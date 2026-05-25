"""Typed errors for the Microsoft Copilot Studio adapter.

The adapter is the seam between the kernel and Microsoft's Entra +
Power Platform REST surfaces. Callers (the integrations + ship routes)
catch these ownEvo-side types and never see a raw `httpx` exception or
a Microsoft error body — so the kernel doesn't couple its call sites to
the vendor's wire format. See MAPPING.md for the HTTP → adapter mapping.
"""

from __future__ import annotations


class CopilotStudioError(Exception):
    """Base for every Copilot Studio adapter failure. Generic / unclassified."""


class CopilotStudioConfigError(CopilotStudioError):
    """Stored credential is incomplete or malformed (missing tenant/client/etc.)."""


class CopilotStudioAuthError(CopilotStudioError):
    """Entra rejected the service principal (invalid client id/secret, 401/403)."""


class CopilotStudioNotFoundError(CopilotStudioError):
    """The environment, solution, or test set doesn't exist (404)."""


class CopilotStudioRateLimitError(CopilotStudioError):
    """Power Platform throttled the request (429)."""


class CopilotStudioNetworkError(CopilotStudioError):
    """Connection-level failure reaching Entra / Power Platform (timeout, DNS, reset)."""
