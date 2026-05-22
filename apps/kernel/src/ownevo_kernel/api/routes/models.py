"""`/api/models` — runtime-enabled LLM provider + model catalog.

Reads the env-var allowlist from `kernel/llm/providers.py` and serves
it grouped by provider. The web `ModelPickerForm` calls this on every
render of the workflow settings page, then renders each entry as an
`<optgroup label={label}>` of `<option value="{provider}:{model}">`.

Read-only — new providers come from `.env` edits + kernel restart, not
from API writes.
"""

from __future__ import annotations

from fastapi import APIRouter

from ...llm import enabled_providers
from ..models import ModelCatalog, ProviderModels

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("", response_model=ModelCatalog)
async def list_models() -> ModelCatalog:
    """Grouped provider+model catalog the picker UI reads.

    Order follows the declaration in `PROVIDERS`; providers with an
    empty allowed-models list (or `_ENABLED` set to false) are omitted.
    """
    return ModelCatalog(
        providers=[
            ProviderModels(id=p.id, label=p.label, models=list(models))
            for p, models in enabled_providers()
        ]
    )
