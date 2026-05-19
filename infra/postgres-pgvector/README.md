# Custom Postgres-with-pgvector for Fly

Single-machine Postgres 17 with the pgvector extension, deployed to Fly
as `ownevo-pg`. Same image (`pgvector/pgvector:pg17`) as the local
docker-compose stack — no extension-install dance, no MPG dashboard
side-quest.

## Why not Fly's `flyctl postgres create`?

The unmanaged Fly Postgres image (`flyio/postgres-flex:17.2`) ships
without pgvector. Migration `0001_substrate.sql` does
`CREATE EXTENSION IF NOT EXISTS vector;` and the deploy fails:

```
asyncpg.exceptions.FeatureNotSupportedError: extension "vector" is not available
DETAIL: Could not open extension control file ".../17/extension/vector.control": No such file or directory.
```

Fly's Managed Postgres (MPG) does carry pgvector but the per-cluster
`fly-user` role is `schema_admin`, not superuser. `CREATE EXTENSION`
must be invoked from the MPG dashboard before the migration runs —
inconvenient to script around.

Using the upstream `pgvector/pgvector` image side-steps both problems.

## Trade-offs

- **No Fly-managed backups.** This image is a plain Postgres container
  + volume. Snapshots happen at the volume level only. Acceptable for
  the demo because `make fly-seed` reconstructs the data in ~3 min.
- **Single machine.** No replication, no failover. Demo doesn't need
  HA.
- **Self-managed Postgres upgrades.** Image bumps are manual: change
  the tag in `fly.toml` + redeploy. The base image is upstream Postgres
  + pgvector both maintained by the same team, so bumps are usually
  uneventful.

## Provisioning

Done end-to-end by `scripts/fly_bootstrap.sh`. Manual version:

```bash
flyctl apps create ownevo-pg
flyctl volumes create ownevo_pg_data --app ownevo-pg --size 1 --region sjc
flyctl secrets set POSTGRES_PASSWORD=$(openssl rand -hex 16) --app ownevo-pg
(cd infra/postgres-pgvector && flyctl deploy --remote-only -a ownevo-pg)
```

## Connection string

Internal-network DNS, no public exposure:

```
OWNEVO_DATABASE_URL=postgres://ownevo:$POSTGRES_PASSWORD@ownevo-pg.internal:5432/ownevo
```

Set on the kernel app via `flyctl secrets set -a ownevo-kernel`. The
bootstrap script does this automatically.

## Tearing down

```bash
flyctl apps destroy ownevo-pg --yes
# Volume goes with the app. Data is lost.
```
