"""Microsoft Copilot Studio integration adapter.

Three documented Power Platform surfaces, one Entra auth seam:

* `auth.acquire_token` — Entra service-principal (client-credentials) token.
* `evaluation_api` — push ownEvo eval cases as a Copilot Studio test set
  via the Power Platform Evaluation API; `verify_connection` for the
  Settings "test connection" action.
* `solutions_alm` — export the agent's definition (instructions) via the
  Dataverse `ExportSolution` action so the trace-import design agent can
  ground reverse-discovery in the agent's stated intent.

Callers catch the typed `CopilotStudio*Error` hierarchy and never see a
raw `httpx` exception or a Microsoft error body. There is no programmatic
fix-feedback API on the Microsoft side: approved fixes are delivered as a
plain-language diff the customer applies in Copilot Studio (recorded via
the `fix-exported-copilot-studio` audit kind), not pushed back. See
MAPPING.md for the pinned API contract.
"""

from .auth import AccessToken, acquire_token
from .errors import (
    CopilotStudioAuthError,
    CopilotStudioConfigError,
    CopilotStudioError,
    CopilotStudioNetworkError,
    CopilotStudioNotFoundError,
    CopilotStudioRateLimitError,
)
from .evaluation_api import (
    EVAL_API_VERSION,
    CopilotStudioCredentials,
    TestSetResult,
    TokenCache,
    create_test_set,
    verify_connection,
)
from .solutions_alm import export_solution, extract_agent_definition

__all__ = [
    "EVAL_API_VERSION",
    "AccessToken",
    "CopilotStudioAuthError",
    "CopilotStudioConfigError",
    "CopilotStudioCredentials",
    "CopilotStudioError",
    "CopilotStudioNetworkError",
    "CopilotStudioNotFoundError",
    "CopilotStudioRateLimitError",
    "TestSetResult",
    "TokenCache",
    "acquire_token",
    "create_test_set",
    "export_solution",
    "extract_agent_definition",
    "verify_connection",
]
