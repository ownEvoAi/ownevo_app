# infra

Local development infrastructure — Postgres + pgvector for MVP. Langfuse / ClickHouse / OTel collector arrive in W2-W3 as the trace pipeline expands.

## Quick start

```bash
cd infra
docker compose up -d
make -C .. db-migrate     # apply all migrations (idempotent)
```

Postgres listens on `localhost:5432` (override with `OWNEVO_PG_PORT`). This
compose brings up Postgres only — it does **not** apply migrations. Run `make
db-migrate` from the repo root: the `scripts/migrate.py` runner tracks applied
files in `schema_migrations`, is idempotent, and handles the `CREATE INDEX
CONCURRENTLY` migrations the old `docker-entrypoint-initdb.d` mount could not.

To re-bootstrap with a clean DB:

```bash
docker compose down -v    # drop the data volume
docker compose up -d
make -C .. db-migrate      # rebuild the schema from scratch
```

## Connection string

The default credentials are dev-only:

```
postgresql://ownevo:ownevo@localhost:5432/ownevo
```

Set `OWNEVO_DATABASE_URL` in your shell so kernel code and tests pick it up.

## LiteLLM proxy

`infra/litellm/ollama.yaml` — proxy config for hybrid NL-gen runs: Anthropic `/v1/messages` → Ollama `/api/chat` translation, with passthrough entries for cloud models. Used by `apps/kernel/scripts/run_nl_gen_smoke.sh`. Set `OWNEVO_OLLAMA_HOST` to the Ollama daemon host (default `localhost`).

`infra/litellm/ollama_cloud.yaml` — exposes Ollama Cloud free-tier models via Anthropic `/v1/messages` on port 4001. Routes through local Ollama at `:11434` (which transparently forwards `:cloud` tags to ollama.com). Used by the cloud NL-gen probe.

Start the proxy: `litellm --config infra/litellm/ollama.yaml` (requires `pip install litellm`).
