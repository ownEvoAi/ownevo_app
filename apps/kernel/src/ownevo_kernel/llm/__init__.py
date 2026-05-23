"""LLM provider routing and config.

`providers` reads the runtime-enabled provider list + their allowed
models from environment variables. The web `/api/models` endpoint
serves the grouped list; `PATCH /api/workflows/{id}/agent-model`
validates incoming slugs against it. `router` dispatches the stored
slug to a live chat client at iteration time.
"""

from .providers import (
    PROVIDERS,
    ProviderConfig,
    ProviderId,
    enabled_providers,
    is_model_allowed,
    parse_slug,
)

__all__ = [
    "PROVIDERS",
    "ProviderConfig",
    "ProviderId",
    "enabled_providers",
    "is_model_allowed",
    "parse_slug",
]
