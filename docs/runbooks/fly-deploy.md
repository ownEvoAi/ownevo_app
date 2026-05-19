# Fly.io deploy runbook

Live demo at `demo.ownevo.ai`. Three services: Postgres (Fly managed),
kernel API (`ownevo-kernel`), web (`ownevo-web`).

Estimated first-run time: **~30 min** with `make fly-bootstrap`, **~2 hours** following these steps by hand.

---

## TL;DR — one-shot

```bash
make doctor          # preflight: tools, .env, fly auth
make fly-bootstrap   # walks every step below interactively
make fly-smoke       # verify
```

The bootstrap is idempotent — re-run after a partial failure and it skips
what's already done. The manual steps below are the source of truth for
anything bootstrap doesn't cover (custom region, multiple environments,
debugging a failed step).

---

## Prerequisites

```bash
brew install flyctl          # or: curl -L https://fly.io/install.sh | sh
flyctl auth login            # browser OAuth
flyctl orgs list             # confirm you're in the right org
```

---

## Step 1 — Provision Postgres (custom pgvector image)

We **do not** use `flyctl postgres create`. That command provisions
`flyio/postgres-flex:17.2`, which does not ship pgvector. Migration
`0001_substrate.sql` does `CREATE EXTENSION IF NOT EXISTS vector;`
and the deploy fails with `extension "vector" is not available`.

Instead we deploy the upstream `pgvector/pgvector:pg17` image directly
— same image as `docker-compose.yml`. See `infra/postgres-pgvector/`
(`fly.toml` + `README.md`) for the per-app config and trade-offs.

```bash
# Create the app, volume, and password secret.
flyctl apps create ownevo-pg
flyctl volumes create ownevo_pg_data --app ownevo-pg --size 1 --region sjc --yes
PG_PASSWORD=$(openssl rand -hex 16)
flyctl secrets set POSTGRES_PASSWORD="$PG_PASSWORD" --app ownevo-pg

# Save the password locally — needed to construct OWNEVO_DATABASE_URL.
# (make fly-bootstrap caches it at .fly-pg-password, gitignored.)
echo "$PG_PASSWORD" > .fly-pg-password
chmod 600 .fly-pg-password

# Deploy the image (~2 min).
(cd infra/postgres-pgvector && flyctl deploy --remote-only -a ownevo-pg)
```

The connection string the kernel will use:

```
OWNEVO_DATABASE_URL=postgres://ownevo:$PG_PASSWORD@ownevo-pg.flycast:5432/ownevo
```

(`ownevo-pg.flycast` is Fly's internal DNS for the app, reachable from
any other Fly app in the same org. No public exposure of Postgres.)

Trade-offs vs the (broken) `flyctl postgres create` path:

- No Fly-managed backups. Snapshots happen at the volume level only.
  `make fly-seed` reconstructs the demo data in ~3 min if needed.
- Single machine, no replication. Demo doesn't need HA.
- Image bumps are manual (edit the tag in `infra/postgres-pgvector/fly.toml`).

`pgvector` is already loaded by the image — no `CREATE EXTENSION` step.

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
  OWNEVO_DATABASE_URL="postgres://ownevo:$(cat .fly-pg-password)@ownevo-pg.flycast:5432/ownevo" \
  ANTHROPIC_API_KEY="sk-ant-..." \
  OWNEVO_CORS_ORIGINS="https://ownevo-web.fly.dev,https://demo.ownevo.ai"
```

`.fly-pg-password` was written in Step 1. The kernel reads the secret as
`OWNEVO_DATABASE_URL` (`db.py:26`).

---

## Step 4 — Deploy the kernel (runs migrations automatically)

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

## Step 5 — Create and deploy the web app

```bash
flyctl apps create ownevo-web --org personal

flyctl secrets set -a ownevo-web \
  OWNEVO_KERNEL_API_URL="http://ownevo-kernel.internal:8000"

make fly-deploy-web
# Equivalent: cd apps/web && flyctl deploy --remote-only
#
# IMPORTANT — must `cd apps/web` before flyctl deploy. With
# `flyctl deploy --config apps/web/fly.toml` from the repo root,
# flyctl resolves the build context against cwd (repo root), so the
# remote builder uploads the wrong tree and `COPY package*.json ./`
# can't find `apps/web/package-lock.json`. The `cd` makes the build
# context match the Dockerfile's expectations.
# Tracked at https://github.com/superfly/flyctl/issues/752.
```

Verify at `https://ownevo-web.fly.dev` — you should see the workspace UI
with a "Kernel API not reachable" banner (expected — no data yet).

---

## Step 6 — Seed demo data

```bash
make fly-seed
# Runs seed_demo.py --with-iterations inside the kernel container.
# Takes ~3-5 min (two LLM-driven iterations via ANTHROPIC_API_KEY).
```

Verify: open `https://ownevo-web.fly.dev/workspaces/acme` in an incognito
window — lift chart should show data, Failures tab should show clusters.

---

## Step 7 — Custom domain

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
