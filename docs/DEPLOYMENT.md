# Deployment guide

Three deployment paths are supported:

| Path | Use case | Setup time |
|---|---|---|
| [Local dev (bare metal)](#local-dev-bare-metal) | Daily kernel + web iteration | ~5 min |
| [Local Docker (compose)](#local-docker-compose) | Full-stack testing, demo prep | ~10 min |
| [Fly.io (production demo)](#flyio-production-demo) | Live public demo URL | ~45 min first-run |

---

## Prerequisites

| Dependency | Required for | Install |
|---|---|---|
| Python 3.12+ | All kernel work | `brew install python` |
| Node 20+ | Web app | `brew install node` |
| `uv` | Python package management | `brew install uv` |
| Docker Desktop | Local compose + sandbox | [docker.com](https://www.docker.com/products/docker-desktop/) |
| `flyctl` | Fly.io deploy only | `brew install flyctl` |

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
| `OWNEVO_M5_DIR` | `./data/m5` | Path to M5 forecasting CSVs (only needed for the M5 improvement loop). |
| `OWNEVO_LLM_BASE_URL` | Anthropic cloud | Override base URL for local LLM backends (LM Studio, Ollama via LiteLLM). |
| `OWNEVO_LLM_MODEL` | `qwen/qwen3-coder-30b` | Model name passed to the local backend. |
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

make api          # uvicorn on :8000, auto-migrates on first start
```

### Start the web app

```bash
make web-dev      # Next.js dev server on :3000
```

Verify: `curl localhost:8000/api/health` → `{"status":"ok","db":"ok"}`, then open `http://localhost:3000/workspaces/acme`.

---

## Local Docker (compose)

One command brings up Postgres, kernel API, and web. All three containers share a private Docker network; the kernel's `release_command` runs migrations before the API process starts.

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
make dev-down
docker volume rm ownevo_app_postgres_data   # wipe DB
make dev-up                                 # re-migrates on start
make seed-demo-with-iter
```

---

## Fly.io (production demo)

The live demo at `demo.ownevo.ai` runs three Fly.io services: a managed Postgres instance, `ownevo-kernel` (kernel API), and `ownevo-web` (Next.js). The full step-by-step first-run guide is in [`runbooks/fly-deploy.md`](runbooks/fly-deploy.md).

### TL;DR for subsequent deploys

```bash
# Deploy both services in sequence (kernel first — migrations may run):
make fly-deploy-kernel && make fly-deploy-web
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
| `0008_iteration_case_outputs.sql` | `iteration_case_outputs` table for operator shell primitives |
| `0009_audit_hash_chain.sql` | `parent_hash` + `entry_hash` on `audit_entries` (SHA-256 chain) |
| `0010_grants_and_constraints.sql` | `workflows.id <> '_unscoped'` constraint; REVOKE template for role-level WORM (edit before running) |

**Local:** `make api` and `make dev-up` both run migrations automatically on start.

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
