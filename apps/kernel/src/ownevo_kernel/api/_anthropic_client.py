"""Shared `AsyncAnthropic` builder honoring `ANTHROPIC_BASE_URL`.

Every API route that needs an Anthropic client should call
`build_async_anthropic(api_key)` rather than constructing `AsyncAnthropic`
directly — that keeps the empty-base-URL workaround (see below) in one
place.

Empty-base-URL trap: docker-compose's `${VAR:-}` interpolation passes an
unset env var through as an empty string, not as missing. The Anthropic
SDK respects that empty `ANTHROPIC_BASE_URL` and fails with
`UnsupportedProtocol("Request URL is missing an 'http://' or 'https://'")`.
We wipe the empty value before reading so the SDK falls back to its
default cloud endpoint.

Pointing the kernel at a self-hosted endpoint:

```bash
export ANTHROPIC_BASE_URL=http://192.168.1.50:1234   # LMS Anthropic-compat
docker compose up -d --force-recreate kernel
```

See `docs/local-model-testing.md` for the protocol map and which kernel
surfaces can talk to which backends.
"""

from __future__ import annotations

import os

from anthropic import AsyncAnthropic


def build_async_anthropic(api_key: str) -> AsyncAnthropic:
    """Return an `AsyncAnthropic` client, honoring `ANTHROPIC_BASE_URL`.

    Args:
        api_key: The API key for the Anthropic endpoint. Local backends
            (LMS, LiteLLM proxies) usually accept any non-empty string.
    """
    if os.environ.get("ANTHROPIC_BASE_URL") == "":
        del os.environ["ANTHROPIC_BASE_URL"]
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or None
    if base_url:
        return AsyncAnthropic(api_key=api_key, base_url=base_url)
    return AsyncAnthropic(api_key=api_key)
