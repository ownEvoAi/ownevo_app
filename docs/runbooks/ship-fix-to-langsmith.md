# Ship a fix back to LangSmith

End-to-end walkthrough for delivering an ownEvo-approved fix to a
customer's LangSmith workspace as a new prompt version. This is the
fix-delivery half of the LangSmith integration; the ingest half (getting
the customer's traces *into* ownEvo) is covered by the OTLP receiver.

## Prerequisites

- The kernel is deployed with a **credentials master key** set:
  `OWNEVO_CREDENTIALS_MASTER_KEY`. Generate one with
  `make gen-credentials-key` and store it in the deployment's secret
  manager. Without it the credentials API returns 500 on write.
- A LangSmith account + an API key with permission to push prompts.

## 1. Store the LangSmith API key

Settings → Integrations → LangSmith → paste the API key → **Save**, then
**Test connection**. The key is encrypted at rest (Fernet) and never
returned by the API. A green "Connection OK" means the key authenticates.

Behind the UI:

```
POST /api/integrations/langsmith        {"api_key": "lsv2_pt_..."}
POST /api/integrations/langsmith/test   -> {"status": "ok"}
```

## 2. Get the customer's traces flowing in (so the workflow is bound)

Mint a receiver token for the workflow and point the customer's
collector at the OTLP endpoint:

```
make mint-receiver-token LABEL="acme-langsmith-prod" WORKFLOW=<workflow_id>
```

Configure `langsmith-collector-proxy` (or any OTLP-HTTP exporter, JSON
or protobuf) with:

- endpoint: `https://<your-kernel-host>/api/otel/v1/traces`
- header: `Authorization: Bearer ownevo_rt_...`

As traces arrive, the workflow is auto-tagged `origin='langsmith'` and
its skill's `langsmith_prompt_id` is back-filled from the spans. Verify
on the workflow's **Failures** tab that ingested traces are showing up.

## 3. Bind the skill to a LangSmith prompt (if not auto-bound)

If auto-binding didn't fire (the spans didn't carry a prompt id), set it
manually: workflow **Settings → LangSmith binding** → pick the skill →
enter the LangSmith prompt identifier → **Save**.

```
PATCH /api/skills/{skill_id}/langsmith-binding  {"langsmith_prompt_id": "demand-forecast"}
```

## 4. Run the loop and approve a fix

Let the improvement loop propose a fix, review it, **Approve**, then
**Deploy** (Deploy flips the skill's deployed version — the fix is live
in ownEvo).

## 5. Ship the fix to LangSmith

On the deployed proposal, click **Ship fix to LangSmith**. ownEvo pushes
the deployed instruction as a new commit on the bound prompt and records
a `fix-shipped-langsmith` audit entry with the LangSmith commit hash +
URL. The audit-trail row links straight to the new commit in LangSmith.

```
POST /api/proposals/{proposal_id}/ship-langsmith
  -> {"commit_hash": "...", "commit_url": "https://smith.langchain.com/prompts/.../...", "already_shipped": false}
```

Shipping is idempotent: clicking again returns the existing commit
(`already_shipped: true`) without pushing twice.

## Troubleshooting

| Symptom (HTTP) | Cause | Fix |
|----------------|-------|-----|
| 422 "Workflow is not LangSmith-originated" | `workflows.origin` isn't `langsmith` | Confirm traces ingested via a LangSmith collector; or the import flow didn't tag it. |
| 422 "Skill has no langsmith_prompt_id" | No prompt binding | Set it in Settings → LangSmith binding (step 3). |
| 422 "Proposal must be deployed before shipping" | Fix approved but not deployed | Click Deploy first. |
| 424 "No LangSmith credential configured" | No API key stored | Add it in Settings → Integrations (step 1). |
| 401 | LangSmith rejected the key | Key invalid/revoked — re-enter and Test connection. |
| 404 | Prompt identifier not found in the workspace | Check the bound `langsmith_prompt_id` matches a prompt the key can write. |
| 429 | LangSmith rate limit | Wait and retry — shipping is idempotent, so a retry is safe. |
| 502 | Network / LangSmith API error | Transient; retry. The deploy is unaffected. |

## Notes

- Shipping never rolls back the ownEvo deploy. A failed push leaves the
  fix live in ownEvo; retrying the ship is just another click.
- The weekly `langsmith-push-conformance` CI job validates the real API
  against drift — but it is dormant until `OWNEVO_LANGSMITH_TEST_API_KEY`
  is set on the repo.
