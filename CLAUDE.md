# Notes for Claude Code (and humans) working in this repo

## What this repo is

The reference implementation of ownEvo — an improvement loop for core agents. Production agents emit typed traces; failures get clustered into eval cases; the loop proposes instruction/skill edits; a regression gate validates them; a domain expert approves through the web UI.

## Stack split (locked)

- **Python** — `apps/kernel/`. Agent runtime, eval harness (Inspect AI), failure clustering (sentence-transformers + UMAP + HDBSCAN), regression gate, background jobs.
- **TS / Next.js** — `apps/web/`. Approval UX, side-by-side diff, lift chart, audit trail.
- **Seam** — REST + SSE from kernel to web. Don't blur the boundary.

Why not pure TS: clustering ecosystem is Python-first at the quality bar required.
Why not pure Python: web UI is unavoidably TS/Next.

## Single-tenant for now

The current schema runs on a single workspace — no `workspace_id` columns, no row-level security. Multi-tenant retrofit is intentionally deferred until a second deployment demands it; the schema is designed to stay retrofit-friendly (nothing that fights a future `workspace_id` column).

## Append-only audit log

`audit_entries` is append-only at the DB level: `REVOKE UPDATE, DELETE` from the app role, only `INSERT` permitted. Exportable in canonical JSON (sorted keys, no whitespace). A SHA-256 hash chain over entry content + parent hash is recorded per entry; verification is exposed via `GET /api/audit/verify`.

## Sandbox: local Docker

Agent-generated code runs in **local Docker** with hardening: `--network=none`, `--read-only` rootfs + tmpfs `/tmp`, `--cap-drop=ALL`, mem/cpu/pids limits, hard timeout, structured stdout/stderr capture, explicit failure semantics (`tool_call_result {status: "error", error_class: "Timeout"|"OOM"|"Crash"}`). The `SandboxRuntime` Protocol is preserved so swapping to a hosted sandbox (e2b, Modal) stays bounded.

## Trace format is the contract

`packages/trace-format/` defines the typed `AgentEvent` schema — the seam between any customer agent and the improvement loop. Same role as OTel for distributed tracing: standardize once, everything downstream works. Canonical spec at `packages/trace-format/SPEC.md`; Pydantic + Zod implementations conform.

## Local LLM backend (dev / dogfooding)

Two distinct tracks; pick the one matching your task before reaching for a model name.

### Multi-turn improvement loop (`scripts/run_improvement_loop.py`)

Code-generating loop on real M5. Supports two API formats via `--api-format`:

- `anthropic` (default) — `AsyncAnthropic` + `/v1/messages`. Works with LM Studio and any LiteLLM proxy. Add `--no-stream` when proxying Ollama through LiteLLM to bypass the streaming tool-call translation bug.
- `openai` — `AsyncOpenAI` + `/v1/chat/completions`. Talks directly to Ollama (or vLLM). Default base URL: `http://$OWNEVO_LLM_HOST:11434/v1`.

Confirmed lift drivers on the multi-turn loop:

- **Sonnet 4.6 (Anthropic cloud)** — reliable, ~$0.30/iter on the 7-iter M5 replay.
- **`qwen3-coder:30b` (Ollama OpenAI)** — produced a real lift in one experiment series; later runs surfaced a deterministic codegen bug, so treat single-driver lift as uncertain pending root-cause. Requires `/no_think` auto-injection (handled by `run_agent_turn_openai` when the model id contains `qwen3`).

```bash
# Sonnet 4.6 — confirmed lift driver
uv run --directory apps/kernel --extra agent python scripts/run_improvement_loop.py \
  --api-format anthropic \
  --llm-model claude-sonnet-4-6 \
  --no-seed

# qwen3-coder:30b via Ollama — local lift driver (mileage varies)
uv run --directory apps/kernel --extra agent python scripts/run_improvement_loop.py \
  --api-format openai \
  --llm-model qwen3-coder:30b \
  --no-seed
```

Other local-model attempts on the multi-turn loop and where they fail:

- `qwen3-coder-30b` (LMS Anthropic) — drives the loop but hits a deterministic codegen bug.
- `devstral-small-2:latest` (Ollama) — drives the loop, but `run_pipeline` validation rejects every diff.
- `granite4.1:8b` — calls tools but generates em-dashes (U+2013) in Python → SyntaxError.
- `qwen2.5-coder:32b` — doesn't trigger tool calls with `tool_choice=auto`.

### Single-turn classification gate (`scripts/nl_gen_smoketest.py --from-fixtures`)

Forced-tool-use `predict_label(value: bool)` per case; orthogonal to the multi-turn loop. **19+ models pass 3/3** across desktop LMS / laptop LMS / desktop Ollama. Source of truth: `docs/local-model-testing.md` (and `apps/kernel/README.md` for the top-pick table). Highlights:

- Fastest desktop 3/3: `granite-4.1-8b` (~33 s, LMS). On laptop Apple Metal it sits on the credit-risk gate boundary; for stable laptop iteration prefer `qwen/qwen3-4b-2507`.
- Fastest desktop Ollama 3/3: `qwen3-coder:30b` (~82 s) — **only with `/no_think` auto-injection**.
- API-format-load-bearing: `qwen/qwen3.5-9b` is 0/3 via OpenAI but 3/3 via Anthropic `/v1/messages`.
- The qwen3.5 / qwen3.6 lineage embeds thinking deeper than the directive can override. qwen3-base + qwen3-coder ARE unlocked.

## OSS-friendly diffs — no external repo references

This repo is the public reference implementation. Anything that ships in a
diff here — code, comments, tests, docstrings, fixture text, PR bodies,
CHANGELOG entries — must be self-contained for an external reader.

Never reference, link to, or quote from:

- `../ownevo_docs/` or any sibling/private repo (`mvp-execution/`, `pitch/`,
  `yc/`, `competitors/`, `vision/`)
- Internal planning artifacts (`PLAN.md`, `TODOS.md`, `DEMO_VIDEO_SCRIPT.md`)
- Internal IDs (`TODO-NN`, `PLAN 8.4.11`, `W6.4`, internal PR numbers from
  other repos)
- Personal paths (`~/code/jobs/`, `~/code/startup2026/`)
- Recording/demo scripts or pitch artifacts not in this repo
- Founder/investor framing (don't write code comments that assume the
  reader is part of the pitch context)

Replace external-artifact references with self-contained descriptions of
what the code does and why. Example:

- **Wrong:** `# labels match the recording-script voiceover`
- **Right:** `# labels are concrete named patterns so the failures page renders realistic content`

If you genuinely need to point at private context for a teammate, put it
in `CLAUDE.local.md` (gitignored) or in a `mvp-execution/` doc that lives
in the private docs repo — not in tracked files here.

This rule applies to every PR. Audit the branch diff before committing:

```bash
git diff main..HEAD | grep -nE "ownevo_docs|/mvp-execution/|/yc/|/pitch/|TODO-[0-9]+|PLAN\.md|~/code/|recording.script|founder|investor.pitch"
```

A clean grep means the branch is OSS-publishable.

## Out of scope

Multiple framework integrations beyond Claude Agent SDK, self-evolving harness, custom Rust gateway, knowledge ingestion connectors, mobile UI, skills marketplace.

## Personal / machine-local notes

Anything specific to a particular developer's machine, billing account, or experimental branch belongs in `CLAUDE.local.md` (gitignored), not here.
