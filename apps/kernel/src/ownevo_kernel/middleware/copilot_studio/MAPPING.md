# Microsoft Copilot Studio — integration API surface

Pinned reference for the Copilot Studio adapter, the way `MAPPING.md`
pins the OTLP ingest mapping and `MAPPING_PUSH.md` pins the LangSmith
fix-delivery contract. Records exactly which Microsoft APIs we call, the
request/response shapes, and where the integration deliberately stops —
so a maintainer chasing an API break doesn't have to reverse-engineer it
from the adapter code.

Unlike LangSmith (a maintained Python SDK), Copilot Studio is reached
over **raw REST** with an `httpx.AsyncClient`. There is no vendor SDK we
can lean on for auth or serialization, so the wire contract lives here.

## Auth — Entra service principal (client credentials)

Every Power Platform call carries an OAuth2 bearer token minted by
Microsoft Entra ID under the client-credentials grant. No user is in the
loop; the customer registers a service principal (app registration) and
consents to its Power Platform permissions.

```
POST https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_id={client_id}
&client_secret={client_secret}
&scope={environment_url}/.default
```

Response: `{"access_token": "...", "expires_in": 3599, "token_type": "Bearer"}`.

- `environment_url` is the Dataverse org URL (e.g.
  `https://org.crm.dynamics.com`); the `/.default` scope grants every
  application permission an admin has consented to for the principal.
- The token is cached (`TokenCache`) and re-minted ~60 s before its
  stated expiry, so a burst of calls mints one token, not one per call.
- Sovereign clouds (US Gov / China) use a different authority host;
  `authority_host` is a parameter, defaulting to the public cloud.

### Auth error mapping

| HTTP from Entra        | adapter error                | HTTP at route |
|------------------------|------------------------------|---------------|
| 400 / 401 / 403        | `CopilotStudioAuthError`     | 401           |
| connection failure     | `CopilotStudioNetworkError`  | 502           |
| other non-2xx          | `CopilotStudioError`         | 502           |

## Eval push — Power Platform Evaluation API

Copilot Studio is the only enterprise agent platform with a documented,
externally-callable eval-push API. We create a **test set** from a
failure cluster's eval cases:

```
POST {environment_url}/api/copilotstudio/testsets?api-version=2024-10-01
Authorization: Bearer {token}

{ "agentId": "...", "name": "...", "testCases": [ {input, expected_output}, … ] }
```

- `EVAL_API_VERSION` (`2024-10-01`) is pinned in `evaluation_api.py`.
- The caller owns the ownEvo-eval-case → `testCases` mapping.
- `verify_connection` is the cheapest authenticated round-trip (mint a
  token only); it backs the Settings "test connection" action.

### Power Platform error mapping

| HTTP                   | adapter error                  | HTTP at route |
|------------------------|--------------------------------|---------------|
| 401 / 403              | `CopilotStudioAuthError`       | 401           |
| 404                    | `CopilotStudioNotFoundError`   | 404           |
| 429                    | `CopilotStudioRateLimitError`  | 429           |
| connection failure     | `CopilotStudioNetworkError`    | 502           |
| other non-2xx          | `CopilotStudioError`           | 502           |

## Definition export — Solutions ALM

The agent's instructions are pulled from a Power Platform **solution**
export so the trace-import design agent can ground reverse-discovery in
the agent's stated intent (`agent_definition`), not only its traces:

```
POST {environment_url}/api/data/v9.2/ExportSolution
Authorization: Bearer {token}

{ "SolutionName": "...", "Managed": false }
```

Response: `{"ExportSolutionFile": "<base64 zip>"}`. The adapter decodes
the zip and scans its JSON bot-component files for the first
instruction-bearing field (`instructions` / `systemPrompt` / `content` /
`description`, searched at any nesting depth).

**Component-completeness caveat** (Microsoft's own ALM docs): solution
export does not guarantee every component round-trips. Extraction is
best-effort — a miss returns `None` and the design agent falls back to
the trace-only summary. Definition export is never a hard dependency.

## What this adapter deliberately does not do

- **No programmatic fix-feedback.** No Microsoft API applies an
  instruction change to a deployed agent. Approved fixes are delivered
  as a plain-language diff the customer applies in the Copilot Studio
  UI, recorded via the `fix-exported-copilot-studio` audit kind. This is
  the structural limit on "full automation" for every vendor except
  LangSmith.
- **No test-run lifecycle.** Creating a test set is the documented,
  stable surface. Triggering a run and polling results is preview and
  its response shapes aren't pinned, so it's deferred rather than coded
  against a guess.
- **No live conformance in the per-PR test suite.** Unit tests mock the
  `httpx` transport. A live path needs a Copilot Studio developer tenant
  + a registered service principal, gated behind credentials the same
  way the LangSmith conformance workflow is.
