# Fly.io deploy runbook

Live demo at `demo.ownevo.ai`. Three services: Postgres (Fly managed),
kernel API (`ownevo-kernel`), web (`ownevo-web`).

Estimated first-run time: **~2 hours**.

---

## Prerequisites

```bash
brew install flyctl          # or: curl -L https://fly.io/install.sh | sh
flyctl auth login            # browser OAuth
flyctl orgs list             # confirm you're in the right org
```

---

## Step 1 — Provision Fly Postgres

```bash
flyctl postgres create \
  --name ownevo-pg \
  --region sjc \
  --vm-size shared-cpu-1x \
  --volume-size 1 \
  --initial-cluster-size 1

# Save the connection string printed at the end — you'll need it in Step 3.
# Looks like: postgres://ownevo_pg:PASSWORD@ownevo-pg.internal:5432/ownevo_pg
```

Enable pgvector (Fly Postgres ships the extension; just activate it):

```bash
flyctl postgres connect -a ownevo-pg
# inside psql:
CREATE EXTENSION IF NOT EXISTS vector;
\q
```

---

## Step 2 — Create the kernel app

```bash
# From repo root
flyctl apps create ownevo-kernel --org personal
```

---

## Step 3 — Set secrets on the kernel app

```bash
flyctl secrets set -a ownevo-kernel \
  OWNEVO_DATABASE_URL="postgres://ownevo_pg:PASSWORD@ownevo-pg.internal:5432/ownevo_pg" \
  ANTHROPIC_API_KEY="sk-ant-..." \
  OWNEVO_CORS_ORIGINS="https://ownevo-web.fly.dev,https://demo.ownevo.ai"
```

Replace `PASSWORD` with the value from Step 1.

---

## Step 4 — Attach Postgres to the kernel app (for private networking)

```bash
flyctl postgres attach ownevo-pg -a ownevo-kernel
# This also sets DATABASE_URL on the app — you can use that instead of
# the manual OWNEVO_DATABASE_URL above if you rename it in the kernel.
```

---

## Step 5 — Deploy the kernel (runs migrations automatically)

```bash
make fly-deploy-kernel
# Equivalent: flyctl deploy --config fly.toml --remote-only
#
# The release_command in fly.toml runs migrate.py before traffic cuts over.
# Watch for "Applied N migration(s)." in the release logs.
```

Verify:

```bash
curl https://ownevo-kernel.fly.dev/api/health
# {"status":"ok","db":"ok"}
```

---

## Step 6 — Create and deploy the web app

```bash
flyctl apps create ownevo-web --org personal

flyctl secrets set -a ownevo-web \
  OWNEVO_KERNEL_API_URL="http://ownevo-kernel.internal:8000"

make fly-deploy-web
# Equivalent: flyctl deploy --config apps/web/fly.toml --remote-only
```

Verify at `https://ownevo-web.fly.dev` — you should see the workspace UI
with a "Kernel API not reachable" banner (expected — no data yet).

---

## Step 7 — Seed demo data

```bash
make fly-seed
# Runs seed_demo.py --with-iterations inside the kernel container.
# Takes ~3-5 min (two LLM-driven iterations via ANTHROPIC_API_KEY).
```

Verify: open `https://ownevo-web.fly.dev/workspaces/acme` in an incognito
window — lift chart should show data, Failures tab should show clusters.

---

## Step 8 — Custom domain

```bash
flyctl certs add demo.ownevo.ai -a ownevo-web
# Follow the DNS instructions printed (CNAME demo.ownevo.ai → ownevo-web.fly.dev)
```

TLS is automatic via Let's Encrypt once the DNS propagates (~5 min).

---

## Day-2 operations

### Re-deploy after a code push

```bash
# Deploy both in sequence (kernel first — migrations may run):
make fly-deploy-kernel && make fly-deploy-web
```

### Run migrations manually (if release_command didn't fire)

```bash
make fly-migrate
```

### Tail live logs

```bash
make fly-logs
```

### Open a shell on the kernel machine

```bash
make fly-ssh
```

### Re-seed after a DB reset

```bash
# Wipe and re-create the DB (destructive):
flyctl postgres connect -a ownevo-pg -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
make fly-migrate   # re-apply all migrations
make fly-seed      # re-seed demo data
```

---

## Cost

All three services fit within Fly.io's free machine allowance
(3 shared-cpu-1x VMs + 3 GB volumes per billing account):

| Service | Size | Monthly |
|---|---|---|
| ownevo-pg | shared-cpu-1x, 256 MB, 1 GB vol | ~$0 |
| ownevo-kernel | shared-cpu-1x, 512 MB | ~$0 |
| ownevo-web | shared-cpu-1x, 512 MB | ~$0 |

Total: **$0–5/month** depending on outbound traffic volume.

---

## What doesn't work in DEMO_MODE

`DEMO_MODE=true` is set in `fly.toml`. It blocks write operations that
would consume API credits or mutate the seeded demo data:

- `POST /api/workflows/{id}/iterations/run` → 503
- `POST /api/workflows/{id}/eval-cases/generate` → 503
- `DELETE /api/workflows/{id}` → 503
- `DELETE /api/workflows/{id}/eval-cases/{case_id}` → 503
- `POST /api/proposals/{id}/deploy` → 503
- `POST /api/proposals/{id}/rollback` → 503

All 503 responses include a message pointing at the GitHub repo.

Everything else (browse workspace, view traces, view audit trail, approve
or reject proposals) works normally on the seeded data.

To run real iterations: clone the repo and `make dev-up` locally.
