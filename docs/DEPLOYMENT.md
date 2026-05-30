# Deployment guide

Three deployment paths are supported:

| Path | Use case | Setup time |
|---|---|---|
| [Local dev (bare metal)](#local-dev-bare-metal) | Daily kernel + web iteration | ~5 min |
| [Local Docker (compose)](#local-docker-compose) | Full-stack testing, demo prep | ~10 min |
| [Run from published images (GHCR)](#run-from-published-images-ghcr) | Self-host without building from source | ~5 min |
| [Fly.io (production demo)](#flyio-production-demo) | Live public demo URL | ~45 min first-run |

---

## Prerequisites

The fastest path: `./scripts/setup.sh` from the repo root. It detects what's installed, installs missing pieces (uv, node via brew on macOS), runs `uv sync` + `npm install`, and bootstraps `.env`. Idempotent — safe to re-run.

Manual install:

| Dependency | Required for | Install |
|---|---|---|
| Python 3.12+ | All kernel work | `brew install python` |
| Node 20+ | Web app | `brew install node` |
| `uv` | Python package management | `brew install uv` |
| Docker Desktop | Local compose + sandbox | [docker.com](https://www.docker.com/products/docker-desktop/) |
| `flyctl` | Fly.io deploy only | `brew install flyctl` |

Before deploying, run `make doctor` — it verifies tool versions, `.env` contents, `fly auth whoami`, and the sandbox image build, and exits non-zero if anything is missing.

---

## Environment variables

The short list below covers the deployment path. **For the full inventory** of every variable read anywhere in the repo (web, scripts, sandbox images, dogfooding probes), see [`ENV_VARS.md`](ENV_VARS.md).

### Required

| Variable | Used by | Description |
|---|---|---|
| `OWNEVO_DATABASE_URL` | kernel | Postgres connection string. Compose sets this automatically. |
| `ANTHROPIC_API_KEY` | kernel | Drives NL-gen, agent solver, meta-eval judge, and seeding with iterations. |

### Optional

| Variable | Default | Description |
|---|---|---|
| `OWNEVO_CORS_ORIGINS` | `""` (allow all) | Comma-separated list of allowed CORS origins for the kernel API. |
| `OWNEVO_DB_POOL_MIN_SIZE` | `1` | Lower bound on the kernel's asyncpg pool. Must be ≥ 1. |
| `OWNEVO_DB_POOL_MAX_SIZE` | `10` | Upper bound on the pool. Raise for higher-concurrency deploys; `min > max` is rejected at boot. |
| `OWNEVO_DB_STATEMENT_TIMEOUT_MS` | `30000` | Per-connection `statement_timeout` (ms). A runaway query is cancelled and surfaces as `asyncpg.QueryCanceledError` instead of pinning a connection. Set to `0` to disable (long-running maintenance scripts only — not recommended for the API process). |
| `SENTRY_DSN` | unset (= off) | Sentry DSN. When set, the kernel ships uncaught exceptions to Sentry and tags every event with the same `request_id` echoed in the `X-Request-Id` response header. Unset → init is a no-op (dev/CI default). |
| `OWNEVO_SENTRY_TRACES_SAMPLE_RATE` | `0.0` | Performance-trace sample rate in `[0.0, 1.0]`. Default ships error events only. |
| `OWNEVO_SENTRY_RELEASE` | (Sentry auto-detect) | Release tag passed to `sentry_sdk.init(release=...)`. |
| `OWNEVO_M5_DIR` | `./data/m5` | Path to M5 forecasting CSVs (only needed for the M5 improvement loop). |
| `OWNEVO_LLM_BASE_URL` | Anthropic cloud | Override base URL for local LLM backends (LM Studio, Ollama via LiteLLM). |
| `OWNEVO_LLM_MODEL` | `claude-sonnet-4-6` | Model name for the improvement loop agent. For a local $0 alternative, use `qwen/qwen3.6-35b-a3b` (LM Studio, lms-anthropic path). |
| `OWNEVO_LLM_HOST` | `localhost` | Hostname for the Ollama OpenAI path (`http://$OWNEVO_LLM_HOST:11434/v1`). |
| `DEMO_MODE` | `false` | Set `true` to block write operations — used on the Fly.io demo instance. Kernel returns HTTP 503 on writes when true; web app surfaces a demo banner. Set independently on the two apps. |

The kernel reads `OWNEVO_DATABASE_URL` at startup. If it's unset, the API starts but every DB call returns a startup error.

---

## Local dev (bare metal)

Run kernel and web separately; requires a running Postgres instance.

### Start Postgres

```bash
# Option A — use the infra/ compose (Postgres only, no kernel/web):
docker compose -f infra/docker-compose.yml up -d postgres

# Option B — any Postgres 15+ instance; create the database manually:
createdb ownevo
```

### Start the kernel

```bash
export OWNEVO_DATABASE_URL=postgresql://ownevo:ownevo@localhost:5432/ownevo
export ANTHROPIC_API_KEY=sk-ant-...

make db-migrate   # apply all pending migrations (idempotent)
make api          # uvicorn on :8000 (does NOT migrate — run db-migrate first)
```

`make api` starts the server only; it does not apply migrations. Run `make
db-migrate` after pulling new schema, or `make db-reset` to drop and rebuild
the dev database from scratch.

### Start the web app

```bash
make web-dev      # Next.js dev server on :3000
```

Verify: `curl localhost:8000/api/health` → `{"status":"ok","db":"ok"}`, then open `http://localhost:3000/workspaces/acme`.

---

## Local Docker (compose)

One command brings up Postgres, kernel API, and web. All three containers share a private Docker network. A one-shot `migrate` service runs `scripts/migrate.py` once Postgres is healthy; the kernel waits on its successful completion (`service_completed_successfully`) so the API never starts against an unmigrated schema. Because the runner tracks applied files in `schema_migrations`, `make dev-up` re-applies only new migrations on every start — not just the first boot.

### First run

```bash
ANTHROPIC_API_KEY=sk-ant-... make dev-up
```

This builds both images and starts three services. Kernel on `:8000`, web on `:3000`. First build takes ~3 min (Python deps + Next.js build).

### Seed demo data

```bash
# Workflows + eval cases only (fast, ~30 s):
make seed-demo

# Workflows + eval cases + one iteration per workflow (~3–5 min, costs ~$0.30):
make seed-demo-with-iter
```

With `seed-demo-with-iter`, the operator pages (`/operator/credit-risk?ws=acme`) populate with real `iteration_case_outputs` rows on first load. Without it, MetricCards and TimeSeriesChart are empty until you click **Run iteration** in the UI.

### Useful commands

```bash
make dev-logs     # tail all service logs
make dev-ps       # show container status
make dev-down     # stop and remove containers
```

### Re-seeding after schema changes

```bash
make db-reset                               # drop + recreate + re-migrate the DB
make seed-demo-with-iter
```

`make db-reset` is destructive — it drops and recreates the dev database, then
runs every migration from scratch. (For the full-stack compose flow you can
instead `make dev-down && docker compose down -v && make dev-up`, which wipes
the Postgres volume and lets the `migrate` service rebuild the schema.)

---

## Run from published images (GHCR)

Every `v*` tag publishes pre-built kernel and web images to the GitHub
Container Registry, so a self-hoster can run the full stack without a source
build. The images are public — `docker pull` needs no login. (One-time setup: after the
first tagged publish, flip both packages to public in the GitHub package settings for the
`ownEvoAi` org. Until that toggle is flipped, pulls will 403 for unauthenticated clients.)

```
ghcr.io/ownevoai/ownevo-kernel:<version>   # also tagged latest, <major>.<minor>, <major>
ghcr.io/ownevoai/ownevo-web:<version>
```

The same `docker-compose.yml` runs either built-from-source or pulled images:
the `image:` fields read `OWNEVO_KERNEL_IMAGE` / `OWNEVO_WEB_IMAGE`, defaulting
to the local build tags when unset. Point them at the registry and skip the
build:

```bash
# Pin a released version (recommended over :latest for reproducibility).
# Image tags carry no leading "v" (e.g. 0.14.0, not v0.14.0).
export OWNEVO_KERNEL_IMAGE=ghcr.io/ownevoai/ownevo-kernel:0.14.0
export OWNEVO_WEB_IMAGE=ghcr.io/ownevoai/ownevo-web:0.14.0

docker compose pull                                   # fetch the images
ANTHROPIC_API_KEY=sk-ant-... docker compose up -d --no-build
```

`--no-build` makes the absence of a build toolchain explicit — the run uses
only the pulled images. The one-shot `migrate` service shares the kernel
image, so `OWNEVO_KERNEL_IMAGE` covers migrations too; it runs
`scripts/migrate.py` before the kernel starts, exactly as in the
build-from-source flow. Seed, logs, and teardown are identical to
[Local Docker (compose)](#local-docker-compose) above (`make seed-demo`,
`make dev-logs`, `make dev-down`).

The images are `linux/amd64`. On Apple Silicon / arm64 they run under Docker
Desktop's built-in emulation.

Self-hosting is permitted for internal and production use under the
[Business Source License](../LICENSE)'s Additional Use Grant — the only
carve-out is offering ownEvo as a hosted competing service. See
[README § License](../README.md#license).

---

## Fly.io (production demo)

The live demo at `demo.ownevo.ai` runs three Fly.io services: a managed Postgres instance, `ownevo-kernel` (kernel API), and `ownevo-web` (Next.js).

### First-time deploy

```bash
make doctor          # preflight: tools, .env, fly auth
make fly-bootstrap   # interactive — walks the 8 runbook steps
```

`fly-bootstrap` is idempotent. It checks each Fly resource before creating, prompts only where the runbook needs input, and finishes with a `make fly-smoke` call so you know the URL is alive. Pass `BOOTSTRAP_ARGS=--no-seed` to skip the (paid, ~$0.30) seed step, or `BOOTSTRAP_ARGS=--dry-run` to preview without executing.

If you'd rather walk the 8 steps by hand, the runbook is at [`runbooks/fly-deploy.md`](runbooks/fly-deploy.md).

### TL;DR for subsequent deploys

```bash
# Deploy both services in sequence (kernel first — migrations may run):
make fly-deploy-kernel && make fly-deploy-web
make fly-smoke   # verify
```

The kernel's `release_command` in `fly.toml` runs `migrate.py` before traffic cuts over. New migrations are picked up automatically.

### Re-seed the demo instance

```bash
make fly-seed
# Runs seed_demo.py --with-iterations inside the kernel container via flyctl ssh.
# Takes ~3–5 min; requires ANTHROPIC_API_KEY set as a Fly secret.
```

### Tail logs

```bash
make fly-logs
```

### Open a shell on the kernel machine

```bash
make fly-ssh
```

---

## Migrations

Migrations live in `apps/kernel/migrations/` and are applied in filename order (`0001_substrate.sql` → `0002_...` → ...). The migration runner is `apps/kernel/scripts/migrate.py`.

| Migration | What it adds |
|---|---|
| `0001_substrate.sql` | Core schema: workflows, skills, iterations, audit_entries, WORM triggers |
| `0002_failure_cluster_fingerprint.sql` | `fingerprint` column on `failure_clusters` |
| `0003_skills_latest_proposed.sql` | `latest_proposed_version_id` on `skills` |
| `0004_skills_deployed.sql` | `deployed_version_id` on `skills` |
| `0005_workflow_sim_metric.sql` | `sim_plan` + `metric` columns on `workflows` |
| `0006_workflow_kind.sql` | `kind` enum on `workflows` |
| `0007_workflow_mode_eval_modes.sql` | `mode` enum + `eval_modes` on `workflows` |
| `0008_iteration_case_outputs.sql` | `iteration_case_outputs` table for operator-shell views |
| `0009_audit_hash_chain.sql` | `parent_hash` + `entry_hash` on `audit_entries` (SHA-256 chain) |
| `0010_grants_and_constraints.sql` | `workflows.id <> '_unscoped'` constraint; REVOKE template for role-level WORM (edit before running) |
| `0011_workflow_template_id.sql` | `workflows.created_from_template` (vertical-template provenance) |
| `0012_design_agent_log.sql` | `workflows.design_agent_log` + design-agent audit kinds |
| `0013_case_output_payload.sql` | `iteration_case_outputs.output_payload` (domain-shaped output) |
| `0014_workflow_agent_model.sql` | `workflows.agent_model_id` (`provider:model` slug) |
| `0015_changes_requested_enums.sql` | `proposal_state`/`audit_kind` values for "request changes" (ADD VALUE) |
| `0015b_changes_requested_constraint.sql` | widen `approvals.decision` CHECK for `request-changes` |
| `0016_demo_phase1.sql` | demo tables: `demo_usage`, `demo_invite_revocations`, `demo_budget_state` |
| `0017_proposal_kind.sql` | `proposal_kind` enum + `proposals.proposed_payload` (non-skill artifacts) |
| `0018_traces_ingest_source.sql` | `traces.ingest_source` + CHECK NOT VALID |
| `0018b_traces_ingest_source_online.sql` | VALIDATE CONSTRAINT + `CREATE INDEX CONCURRENTLY` (no-txn) |
| `0019_receiver_tokens.sql` | `receiver_tokens` (OTLP ingest bearer-token auth) |
| `0020_mock_sim_tier.sql` | `workflows.sim_tier` + `mock_sim_config` |
| `0021_replay_sim_tier.sql` | replay config + `captured_sandbox_runs` table |
| `0022_langsmith_integration.sql` | `workflows.origin` + `skills.langsmith_prompt_id` |
| `0023_audit_fix_shipped_langsmith.sql` | `audit_kind` `fix-shipped-langsmith` (ADD VALUE) |
| `0024_audit_ship_langsmith_unique.sql` | single-push guard index (CONCURRENTLY, no-txn) |
| `0025_design_agent_import_log.sql` | `workflows.design_agent_import_log` + import audit kind |
| `0026_audit_fix_exported_copilot_studio.sql` | `audit_kind` `fix-exported-copilot-studio` (ADD VALUE) |
| `0027_audit_ship_copilot_studio_unique.sql` | single-delivery guard index (CONCURRENTLY, no-txn) |
| `0028_agent_registry.sql` | `agent_registry` table (one agent per workflow) |
| `0028_audit_eval_cases_pushed_copilot_studio.sql` | `audit_kind` `eval-cases-pushed-copilot-studio` (ADD VALUE) |
| `0029_mcp_servers.sql` | `mcp_servers` (external MCP data sources, sealed secrets) |
| `0030_mcp_oauth.sql` | `mcp_oauth_clients` + `mcp_oauth_states` (authorization-code grant) |
| `0031_data_uploads.sql` | `data_uploads` (parsed file data sources) |
| `0032_triggers.sql` | `trigger_kind` enum + `triggers` + `trigger_fires` |
| `0033_workspace_substrate.sql` | `workspaces` table + `workspace_id` on all 17 scoped tables (non-enforcing) |
| `0034_workspace_rls_enforcement.sql` | FORCE RLS + isolation policies + GUC default; PK/index/soft-delete fixes |
| `0035_auth_users.sql` | `users` + `user_identities` + `workspace_members` (global, non-RLS) + seeded dev user |
| `0036_workspace_invites.sql` | `workspace_invites` (HMAC-signed link invites, outside RLS) |
| `0037_audit_iteration_reaped.sql` | `audit_kind` `iteration-reaped` for the startup reaper (ADD VALUE) |
| `0038_receiver_tokens_global.sql` | Moves `receiver_tokens` out of the workspace-RLS set (auth-gateway table) |
| `0039_trace_keyset_indexes.sql` | Trace-list keyset pagination indexes (CONCURRENTLY, no-txn) |
| `0040_jobs_queue.sql` | `jobs` durable background-job queue + `job_kind` enum |
| `0041_jobs_workspace_indexes.sql` | `workspace_id` lead column on jobs claim + heartbeat indexes |
| `0042_jobs_list_indexes.sql` | Indexes for the `GET /api/jobs` list + status-count queries |
| `0043_jobs_run_clustering_kind.sql` | `job_kind` `run_clustering` (ADD VALUE) |
| `0044_rename_primitives_to_views.sql` | Rename render `primitive`→`view`: `ui-view` enum, `workflows.spec`/`proposals` JSONB rewrite, schema_version 1.5 |

**Local:** `make dev-up` runs migrations automatically (the one-shot `migrate` service runs before the kernel starts). `make api` does **not** migrate — run `make db-migrate` first (or `make db-reset` to rebuild the dev DB from scratch).

**Fly.io:** `make fly-deploy-kernel` triggers migrations via the `release_command` before the new process accepts traffic.

**Manual (Fly.io):**

```bash
make fly-migrate
```

---

## Health checks

```bash
# Kernel
curl https://ownevo-kernel.fly.dev/api/health
# {"status":"ok","db":"ok"}

# Audit chain integrity (operator-only; returns 503 in DEMO_MODE)
curl -X POST https://ownevo-kernel.fly.dev/api/audit/verify
# {"valid":true,"hash_chain_valid":true,"hash_chain_entries":N,...}

# Locally
curl localhost:8000/api/health
curl -X POST localhost:8000/api/audit/verify
```

---

## DEMO_MODE

`DEMO_MODE=true` is set in `fly.toml` to prevent demo visitors from consuming API credits or mutating seeded data. It blocks:

- `POST /api/workflows/{id}/iterations/run`
- `POST /api/workflows/{id}/eval-cases/generate`
- `POST /api/proposals/{id}/deploy`
- `POST /api/proposals/{id}/rollback`
- `DELETE` on workflows and eval cases
- `POST /api/audit/verify` (operator diagnostic — too memory-intensive for demo traffic)

All blocked routes return `503` with a message pointing to the GitHub repo. Read-only operations (browse workspace, view audit trail, approve or reject proposals) are unaffected.

To run real iterations against the demo DB, open a shell and run the seed script directly — it bypasses DEMO_MODE by talking to the DB, not the API:

```bash
make fly-ssh
# inside the container:
uv run python apps/kernel/scripts/seed_demo.py --with-iterations
```

---

## Cost

Running on Fly.io free tier:

| Service | Size | Estimated monthly |
|---|---|---|
| `ownevo-pg` | shared-cpu-1x, 256 MB RAM, 1 GB volume | ~$0 |
| `ownevo-kernel` | shared-cpu-1x, 512 MB RAM | ~$0 |
| `ownevo-web` | shared-cpu-1x, 512 MB RAM | ~$0 |

**Total: $0–5/month** depending on outbound traffic. The free allowance covers three `shared-cpu-1x` machines and 3 GB of Fly volumes per billing account.

Local Docker compose runs entirely on your machine. No cloud costs.

---

## Post-deploy hardening checklist

The pieces below are **not optional for production deploys.** Local-dev and demo deploys can skip them; regulated-industry or paid deploys must complete every step before going live.

### 1. Audit WORM grants (layer 2)

Migration `0010_grants_and_constraints.sql` ships the `REVOKE UPDATE, DELETE ON audit_entries FROM <app_role>;` statement **commented out** because the actual role name depends on the deployment environment. The migration tool runs the file as-is; the operator must edit-and-rerun (or hand-execute the REVOKE after the migration applies).

```bash
# 1. Find the role used by the kernel.
# On Fly.io managed Postgres:
fly pg connect -a ownevo-pg -c "\du"

# 2. Apply the REVOKE.
fly pg connect -a ownevo-pg <<'SQL'
REVOKE UPDATE, DELETE ON audit_entries FROM ownevo_app;
SQL

# 3. Verify — the privileges column should show no a/r/w/d for the app role,
#    only insert + select.
fly pg connect -a ownevo-pg -c "\dp audit_entries"
```

See [`AUDIT_HARDENING.md`](AUDIT_HARDENING.md) for the full three-layer story (DB trigger, role-level grants, SHA-256 hash chain).

### 2. Audit hash chain (layer 3)

Migration `0009_audit_hash_chain.sql` adds the chain columns; **no backfill** is run. Entries written before 0009 have NULL hashes and are skipped by the verifier (the "pre-hash epoch"). Verify the chain post-deploy:

```bash
# Use your production kernel URL (not the demo instance — DEMO_MODE blocks this endpoint there).
curl -X POST https://ownevo-kernel.fly.dev/api/audit/verify
# {"hash_chain_entries": N, "hash_chain_valid": true}
```

A freshly-migrated DB returns `hash_chain_entries = 0` and `hash_chain_valid = true` — empty chain is valid by definition. The chain grows from the first audit entry written after 0009 applies.

### 3. Sandbox preflight

The kernel uses local Docker (or whichever sandbox provider is wired behind `SandboxRuntime`). Before serving real traffic, build the sandbox image and confirm it's runnable:

```bash
# Build the M5 sandbox image (one-time per host).
make sandbox-image-m5

# Verify the image is runnable (no standalone smoke target yet — run the
# sandbox unit tests as a build-time sanity check):
make test
```

If you're using a managed sandbox provider (Modal, e2b, …) the swap is one file behind the `SandboxRuntime` Protocol — verify the provider's credentials are set as Fly secrets, not in `fly.toml`.

### 4. Demo-mode banner

For public demo URLs only: set `DEMO_MODE=true` on **both** the kernel and the web app (independently — see `fly.toml` and `apps/web/fly.toml`). The kernel returns HTTP 503 on write endpoints; the web renders a top-of-page banner. The two are decoupled on purpose so a kernel that's mid-deploy can already be in read-only mode while the web takes longer to rebuild.

### 5. CORS origins

Set the production allowlist via Fly secret (not `fly.toml`, because the value is environment-specific):

```bash
flyctl secrets set OWNEVO_CORS_ORIGINS=https://ownevo-web.fly.dev,https://demo.ownevo.ai -a ownevo-kernel
```

The default value is dev-friendly (permissive); production must override.

### 6. Workspace RLS — app role must not bypass it

Migrations `0033`/`0034` put every workspace-scoped table under `FORCE ROW LEVEL SECURITY`. `FORCE` makes the policy apply even to the table owner, but it does **not** override Postgres's unconditional bypass for superusers and roles with the `BYPASSRLS` attribute. If the kernel connects as a superuser or a `BYPASSRLS` role, RLS is silently skipped and tenant isolation is not enforced.

The production app role must therefore be **neither** a superuser **nor** `BYPASSRLS`:

```bash
# The kernel's role should show neither "Superuser" nor "Bypass RLS"
# in the attributes column.
fly pg connect -a ownevo-pg -c "\du"
```

If the role has either attribute, isolation is not enforced regardless of the policies. (The isolation test suite proves the policies under a dedicated non-superuser role via the `rls_db` fixture — production must match that property.) No GUC needs setting at deploy time: `tenant_session.py` binds `app.workspace_id` per connection from the request's resolved workspace, and an unset GUC fails closed (zero rows visible, inserts rejected).

Production must also set `OWNEVO_INTERNAL_AUTH_KEY` (the secret shared with the web app that the kernel uses to verify identity assertions; see [`AUTH.md`](AUTH.md)) and must **not** set `OWNEVO_DEV_AUTH=true` — with the dev fallback off, unauthenticated requests are rejected rather than served the default workspace.
