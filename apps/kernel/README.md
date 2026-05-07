# kernel

Python kernel — agent runtime, eval harness, failure clustering, regression gate, background jobs.

This is where the IP lives. See `../../CLAUDE.md` and `../../../ownevo_docs/ownEvo_MVP.md`.

Planned modules (Week 1-2 lift):
- `types.py` — typed `AgentEvent` and core domain types
- `evolution/` — tracker → reflector → curator → proposer (greenfield; 4-stage pattern shaped by `startup2026/core/agentos_harness/evolution/`; see `docs/SPIKE-RESULT.md`)
- `trace/` — OTel intake + Langfuse client
- `skills/` — skill registry + retention contracts
- `eval/` — Inspect AI integration
- `gate/` — 3-step regression gate runner

## A4.4 local model recommendations (from-fixtures gate, OpenAI-compat)

Top 3/3-pass picks from the 2026-05-06/07 sweep across desktop LMS,
laptop LMS, and desktop Ollama (full results + 19 passing models +
infrastructure notes in `../../docs/local-model-testing.md` § F14).

**Laptop (≤10B, no dedicated GPU) — recommended:**

| model | host | wall | demand / credit / contract |
|---|---|---:|---:|
| **`qwen/qwen3-4b-2507`** | LMS | 152s | 1.00 / 0.42 / 0.91 |
| `qwen/qwen3-1.7b` | LMS | 826s | 0.80 / 0.50 / 0.89 (smallest 3/3) |

**Desktop (24GB+ VRAM, 8B–32B class) — recommended:**

| model | host | API | wall | demand / credit / contract |
|---|---|---|---:|---:|
| **`granite-4.1-8b`** | LMS | OpenAI | 33s | 1.00 / 0.50 / 0.91 (fastest 3/3) |
| **`google/gemma-4-e4b`** | LMS | OpenAI | 34s | 0.60 / 0.42 / 0.89 (smallest 3/3 at this speed — Gemma's "edge" 4B) |
| `mistralai/ministral-3-14b-reasoning` | LMS | OpenAI | 47s | 1.00 / 0.50 / 0.91 |
| `qwen/qwen3.5-9b` | LMS | **Anthropic** | 52s | 0.60 / 0.42 / 0.89 (only passes via `/v1/messages`) |
| `qwen2.5-coder-32b-instruct` | LMS | OpenAI | 98s | 1.00 / 0.50 / 0.89 |
| `qwen3:8b` | Ollama | OpenAI | 373s | 0.80 / 0.42 / 0.91 |

`qwen/qwen3.5-9b` is the first model where API format is load-bearing:
0/3 on OpenAI path (`stop_reason='stop'`, no tool emitted), 3/3 on
Anthropic path. See `../../docs/local-model-testing.md` § F14g for the
broader Anthropic-retry findings (also lifts `qwen3.6-27b` and
`gemma-4-26b-a4b` from 0/3 to 2/3).

Run a sweep: `scripts/run_lmstudio_sweep.sh` (LMS, OpenAI path) /
`scripts/run_ollama_sweep.sh` (Ollama). LiteLLM proxy config (hybrid
NL-gen frontier + local agent) at `infra/litellm/ollama.yaml`.
Anthropic-API retry helper for tool-shy LMS models:
`temp/retry_lms_anthropic.sh {laptop|desktop}`.
