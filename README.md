<p align="center">
  <a href="https://ownevo.ai">
    <img src="https://ownevo.ai/logo-shield.svg" alt="ownEvo" width="96">
  </a>
</p>

<h1 align="center">ownEvo</h1>

<p align="center">
  <strong>The improvement loop for core agents.</strong>
</p>

<p align="center">
  Every production failure becomes an eval case.<br>
  Every proposed fix is regression-tested against every prior fix.<br>
  A domain expert approves the change in plain language.
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="docs/ARCHITECTURE.md">Architecture</a> ·
  <a href="CHANGELOG.md">Changelog</a> ·
  <a href="https://ownevo.ai">ownevo.ai</a>
</p>

---

## What it does

```
   Agent runs a workflow
            ↓
   AgentEvent traces collected
            ↓
   Failures clustered into eval cases
            ↓
   Loop proposes a skill edit
            ↓
   Regression gate (every prior fix runs)
            ↓
   Domain expert approves in plain language
            ↓
   Deploy · audit chain extends
```

Two processes joined by REST + SSE:

- **Python kernel** — agent runtime, eval harness (Inspect AI), failure clustering (sentence-transformers + UMAP + HDBSCAN), regression gate, sandboxed code execution.
- **Next.js web** — workspace UI, side-by-side diff, lift chart, audit trail, approval queue.

Detailed system tour: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Quick start

```bash
ANTHROPIC_API_KEY=sk-... make dev-up        # build + start postgres + kernel + web
make seed-demo-with-iter                    # seed two workflows + run one iteration
```

- Kernel API: <http://localhost:8000/api/health>
- Web app: <http://localhost:3000/workspaces/acme>

`make dev-down` to stop. Local-dev without Docker: `make api` + `make web-dev` (Postgres separate).

Full deployment options — local, Docker Compose, Fly.io: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## Repo layout

```
apps/kernel/      Python — runtime, eval, clustering, gate, baselines, sandbox
apps/web/         Next.js — workspace UI, approval queue, diff viewer
packages/         Shared schemas (trace-format)
docs/             Architecture, schema, skill format, runbooks
infra/            Docker compose + LiteLLM proxy config
```

## Docs

| Topic | Read |
|---|---|
| System tour | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Database schema | [`docs/SCHEMA.md`](docs/SCHEMA.md) |
| Skill format + retention contract | [`docs/SKILL_FORMAT.md`](docs/SKILL_FORMAT.md) |
| State machines (proposal lifecycle) | [`docs/STATE_MACHINES.md`](docs/STATE_MACHINES.md) |
| Improvement-loop design rules | [`docs/HARNESS.md`](docs/HARNESS.md) |
| Multi-benchmark substrate | [`docs/BENCHMARK_ARCHITECTURE.md`](docs/BENCHMARK_ARCHITECTURE.md) |
| Trace-format spec | [`packages/trace-format/SPEC.md`](packages/trace-format/SPEC.md) |
| Local LLM backends (Ollama / LM Studio) | [`docs/local-model-testing.md`](docs/local-model-testing.md) |
| Deployment | [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) |
| REST + SSE API | [`docs/api/openapi.yaml`](docs/api/openapi.yaml) |

## Status

See [`CHANGELOG.md`](CHANGELOG.md) for releases and roadmap.

## License

- **`apps/`, `docs/`, `infra/`, root** — [Business Source License 1.1](LICENSE), converting to Apache 2.0 four years after each release. An Additional Use Grant permits production use except as a hosted competing service.
- **`packages/trace-format/`** — [Apache 2.0](packages/trace-format/LICENSE). The trace schema is meant to be a standard; use it everywhere.

## Contributing

Issues and discussion are welcome. For pull requests: keep them focused, run `make test` and `make lint`, and avoid mixing refactors with feature changes.
