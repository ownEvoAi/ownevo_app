"""REST API for the W2.5 approval queue UI.

Thin FastAPI layer over the existing kernel modules. The web app
(`apps/web/`) talks to this; nothing else should — kernel-internal
callers use the Python functions directly.

The seam is locked at REST + SSE per `ownEvo_MVP.md` § Implementation
Stack. SSE for real-time gate updates is W4 work; W2.5 ships the
approval-queue REST surface only.
"""

from .app import app, create_app

__all__ = ["app", "create_app"]
