# LangSmith `push_prompt` — fix-delivery API surface

Pinned reference for the fix-delivery adapter, the way `MAPPING.md`
pins the OTLP ingest mapping. Records exactly which LangSmith API we
call, the request/response shape, and how an approved ownEvo fix maps
onto a LangSmith prompt commit — so a reader (or a future maintainer
chasing an API break) doesn't have to reverse-engineer it from the
adapter code.

Targeted SDK: **`langsmith` Python SDK 0.8.x** (`langsmith.Client`).
We build against the SDK rather than the raw REST API because the SDK
absorbs auth, retries, and the prompt-manifest serialization, and is
the contract LangSmith actually maintains for third parties.

## The call

```python
from langsmith import Client
from langchain_core.prompts import ChatPromptTemplate

client = Client(api_key=<customer key>)
url = client.push_prompt(
 prompt_identifier, # e.g. "demand-forecast-system"
 object=ChatPromptTemplate.from_messages([("system", <fix text>)]),
 commit_description=<plain-language summary of the approved fix>,
)
```

### `prompt_identifier`
The LangSmith Prompt Hub identifier the ownEvo skill maps to — stored
in `skills.langsmith_prompt_id` (migration 0020). Populated
automatically when a workflow is imported from LangSmith (read off the
ingested span attributes) or set manually via the binding picker.

### `object`
The LangChain object to push. An approved ownEvo fix is an updated
**instruction** (the skill's head-version content), so we wrap it as a
single-system-message `ChatPromptTemplate`. This is the minimal shape
LangSmith accepts; richer multi-message templates are out of scope for
this slice.

### Behaviour
- If the prompt identifier doesn't exist, LangSmith **creates** it.
- If it exists, a **new commit** (version) is appended.

So shipping a fix is idempotent at the API level only by content
pushing the same text twice creates two commits. ownEvo's own
idempotency (don't ship the same approved proposal twice) is enforced
in the route via the audit trail, not here.

## Return value

`push_prompt` returns a **URL string** pointing at the new commit, of
the form:

```
https://smith.langchain.com/prompts/<name>/<commit_hash>
```

(or the customer's self-hosted host). The adapter:
- stores the full URL (what the audit entry links to), and
- parses the trailing path segment as the **commit hash** for the
 `fix-shipped-langsmith` audit payload.

The commit-hash parse is defensive: if the URL shape changes, the hash
field falls back to the full URL rather than failing the ship.

## Error mapping

`langsmith.utils` raises a typed hierarchy; the adapter maps each to an
ownEvo-side error so the kernel never leaks a `langsmith` type past the
adapter seam:

| langsmith.utils | adapter error | HTTP at route |
|--------------------------------|----------------------------|---------------|
| `LangSmithAuthError` | `LangSmithAuthError` | 401 |
| `LangSmithNotFoundError` | `LangSmithNotFoundError` | 404 |
| `LangSmithConflictError` | `LangSmithConflictError` | 409 |
| `LangSmithRateLimitError` | `LangSmithRateLimitError` | 429 |
| `LangSmithConnectionError` | `LangSmithNetworkError` | 502 |
| `LangSmithAPIError` / other | `LangSmithPushError` | 502 |

## What this adapter deliberately does not do

- No prompt **pull** / read-back — we push fixes, we don't sync prompts
 in. Trace ingest (the other direction) is the OTLP receiver's job.
- No multi-message or templated-variable prompts — single system
 message only.
- No live conformance in the per-PR test suite. The weekly
 `langsmith-push-conformance` workflow exercises the real API, but it
 is gated on a `OWNEVO_LANGSMITH_TEST_API_KEY` secret and no-ops until
 a LangSmith account is provisioned. Unit tests mock the SDK client.
