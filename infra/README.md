# infra

Local development infrastructure — Postgres + pgvector for MVP. Langfuse / ClickHouse / OTel collector arrive in W2-W3 as the trace pipeline expands.

## Quick start

```bash
cd infra
docker compose up -d
```

Postgres listens on `localhost:5432` (override with `OWNEVO_PG_PORT`). The substrate migration `apps/kernel/migrations/0001_substrate.sql` is mounted into `docker-entrypoint-initdb.d` and runs on first boot (when the data volume is empty).

To re-bootstrap with a clean DB:

```bash
docker compose down -v
docker compose up -d
```

## Connection string

The default credentials are dev-only:

```
postgresql://ownevo:ownevo@localhost:5432/ownevo
```

Set `OWNEVO_DATABASE_URL` in your shell so kernel code and tests pick it up.
