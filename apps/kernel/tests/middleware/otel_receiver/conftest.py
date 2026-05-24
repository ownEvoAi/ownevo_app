"""Per-directory test config for the OTLP receiver tests.

Sets `OWNEVO_OTLP_AUTH_OPTIONAL=true` so tests that don't care about
auth (mapper round-trip, route shape, persistence) can hit the route
without minting a token. Tests in `test_route_auth.py` explicitly
override this with `monkeypatch.delenv` to exercise the required-auth
path.

The setting is scoped to this directory only — the global default
remains "auth required" so any future test in another file that
forgets to mint a token will fail loudly rather than silently
ingesting unauthenticated traffic in CI.
"""

from __future__ import annotations

import os

os.environ.setdefault("OWNEVO_OTLP_AUTH_OPTIONAL", "true")
