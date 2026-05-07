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

| model | host | wall | demand / credit / contract |
|---|---|---:|---:|
| **`granite-4.1-8b`** | LMS | 33s | 1.00 / 0.50 / 0.91 (fastest 3/3) |
| `mistralai/ministral-3-14b-reasoning` | LMS | 47s | 1.00 / 0.50 / 0.91 |
| `qwen2.5-coder-32b-instruct` | LMS | 98s | 1.00 / 0.50 / 0.89 |
| `qwen3:8b` | Ollama | 373s | 0.80 / 0.42 / 0.91 |

**Best credit-risk score (hybrid frontier-NL-gen + local-agent):**
`mychen76/qwen3_cline_roocode:14b` (Ollama, 629s, 0.60 / 0.67 / 1.00).

Run a sweep: `scripts/run_lmstudio_sweep.sh` (LMS) /
`scripts/run_ollama_sweep.sh` (Ollama). LiteLLM proxy config (hybrid
NL-gen frontier + local agent) at `infra/litellm/ollama.yaml`.
