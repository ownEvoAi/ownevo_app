# kernel

Python kernel — agent runtime, eval harness, failure clustering, regression gate, background jobs.

This is where the IP lives. See `../../CLAUDE.md` and `../../../ownevo_docs/ownEvo_MVP.md`. The canonical module layout is `src/ownevo_kernel/` (also enumerated in the top-level README's Layout block).

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
| **`qwen3-coder:30b`** | Ollama | OpenAI (`/no_think`) | 82s | 0.60 / 0.42 / 0.89 (fastest desktop Ollama 3/3, F14i) |
| `qwen3:8b` | Ollama | OpenAI | 373s | 0.80 / 0.42 / 0.91 |

Two model-specific caveats:

- **`qwen/qwen3.5-9b` is API-format-load-bearing** — 0/3 on OpenAI path
  (`stop_reason='stop'`, no tool emitted), 3/3 on Anthropic path. See
  `../../docs/local-model-testing.md` § F14g (also lifts `qwen3.6-27b` and
  `gemma-4-26b-a4b` from 0/3 to 2/3).
- **`granite-4.1-8b` on laptop Apple Metal sits on the credit-risk gate
  boundary** — same Q4_K_S blob clears 3/3 reliably on desktop CUDA but on
  laptop varies across trials (4 trials: credit-risk 0.33 / 0.25 / 0.50 /
  0.50 vs the 0.40 gate). For laptop iteration prefer `qwen/qwen3-4b-2507`
  (stable 3/3, 152s). See § F14j (initial gap finding) + § F14k (re-test
  weakens the systematic-drift hypothesis, treats it as boundary noise).

The agent solver auto-injects `/no_think` for any model id containing `qwen3`
(suppresses Qwen3-base thinking traces that exhaust `max_tokens` before tool
call; F14i unlocked 5 desktop Ollama 3/3 passers). qwen3.5/3.6 lineages embed
thinking deeper and don't fully respect the directive — see § F14h-hang.

Run a sweep: `scripts/run_lmstudio_sweep.sh` (LMS, OpenAI path) /
`scripts/run_ollama_sweep.sh` (Ollama). LiteLLM proxy config (hybrid
NL-gen frontier + local agent) at `infra/litellm/ollama.yaml`.
Anthropic-API retry helper for tool-shy LMS models:
`temp/retry_lms_anthropic.sh {laptop|desktop}`.
