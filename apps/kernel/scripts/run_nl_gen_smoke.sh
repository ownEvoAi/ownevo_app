#!/usr/bin/env bash
#
# A4.4 NL-gen smoketest dogfood — run the gate against local Ollama
# models via a LiteLLM Anthropic-compat proxy. Captures per-model
# JSONL into .temp/a4_4_local_smoke/ + a markdown summary, mirroring
# the Phase-1 sweep pattern in docs/local-model-testing.md.
#
# Setup (one-time):
#   pip install 'litellm[proxy]'
#
# Usage:
#   OWNEVO_OLLAMA_HOST=http://localhost:11434 \
#     bash apps/kernel/scripts/run_nl_gen_smoke.sh
#
#   OWNEVO_OLLAMA_HOST=http://localhost:11434 \
#     bash apps/kernel/scripts/run_nl_gen_smoke.sh devstral-small-2
#
# First positional arg restricts to one model id (matches model_name
# in infra/litellm/ollama.yaml). Default runs all configured models.
#
# Exit 0 iff every requested model met target on every workflow.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
LITELLM_CONFIG="${REPO_ROOT}/infra/litellm/ollama.yaml"
OUT_DIR="${REPO_ROOT}/temp/a4_4_local_smoke/$(date +%Y%m%d-%H%M%S)"
PROXY_PORT="${LITELLM_PORT:-4001}"
PROXY_LOG="${OUT_DIR}/litellm_proxy.log"
PROXY_PID=""

OWNEVO_OLLAMA_HOST="${OWNEVO_OLLAMA_HOST:-http://localhost:11434}"
export OWNEVO_OLLAMA_HOST

# Default model list (mirrors infra/litellm/ollama.yaml model_name values).
# Excludes embedding-only and vision-only models. gpt-oss-20b excluded —
# known max_tokens error before tool-call commit.
DEFAULT_MODELS=(
  devstral-small-2
  devstral-64k-cline
  qwen2.5-coder-32b
  qwen2.5-coder-7b
  qwen2.5-14b
  qwen2.5-7b
  qwen3-coder-30b
  qwen3-30b-instruct
  qwen3-30b-a3b
  qwen3-32b
  qwen3-14b
  qwen3-8b
  qwen3-4b-instruct
  qwen3.5-27b
  qwen3.5-35b-a3b
  qwen3.5-9b
  qwen3.5-distilled-27b
  qwen3.6-27b
  qwen3.6-35b-a3b
  gemma4-26b
  gemma4-31b
  gemma4-e4b
  gemma4-e2b
  gemma3-27b
  gemma3-12b
  gemma3n
  granite4.1-30b
  granite4.1-8b
  granite4.1-3b
  granite3.3-8b
  phi4-reasoning
  phi4-mini-reasoning
  qwq-32b
  glm-4.7-flash
  llama3.1-8b
  ministral-3-8b
  olmo-3-7b
  lfm2
  nemotron-cascade-2
  qwen3-cline-14b
  deepseek-r1-roo-14b
  rnj-1
)

# Caller can pin one model.
if [[ $# -ge 1 ]]; then
  MODELS=("$1")
else
  MODELS=("${DEFAULT_MODELS[@]}")
fi

mkdir -p "$OUT_DIR"

cleanup() {
  if [[ -n "$PROXY_PID" ]] && kill -0 "$PROXY_PID" 2>/dev/null; then
    echo "[a4.4-smoke] stopping LiteLLM proxy (PID $PROXY_PID)"
    kill "$PROXY_PID" 2>/dev/null || true
    wait "$PROXY_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# Pre-flight: Ollama reachable?
if ! curl -fsS --max-time 5 "${OWNEVO_OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
  echo "[a4.4-smoke] ABORT: Ollama not reachable at ${OWNEVO_OLLAMA_HOST}/api/tags" >&2
  echo "[a4.4-smoke] set OWNEVO_OLLAMA_HOST to your Ollama daemon's URL" >&2
  exit 2
fi
echo "[a4.4-smoke] Ollama reachable at ${OWNEVO_OLLAMA_HOST}"

# Skip starting our own proxy if 4001 is already serving (e.g. a prior
# `bash run_nl_gen_smoke.sh` left one running). Health-check first.
if curl -fsS --max-time 2 "http://localhost:${PROXY_PORT}/health/readiness" >/dev/null 2>&1; then
  echo "[a4.4-smoke] reusing LiteLLM proxy already on :${PROXY_PORT}"
else
  echo "[a4.4-smoke] starting LiteLLM proxy on :${PROXY_PORT} (logs: $PROXY_LOG)"
  litellm --config "$LITELLM_CONFIG" --port "$PROXY_PORT" >"$PROXY_LOG" 2>&1 &
  PROXY_PID=$!

  # Wait up to 30s for readiness.
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 "http://localhost:${PROXY_PORT}/health/readiness" >/dev/null 2>&1; then
      echo "[a4.4-smoke] proxy ready (PID $PROXY_PID)"
      break
    fi
    sleep 1
  done

  if ! curl -fsS --max-time 2 "http://localhost:${PROXY_PORT}/health/readiness" >/dev/null 2>&1; then
    echo "[a4.4-smoke] ABORT: proxy did not become ready" >&2
    tail -40 "$PROXY_LOG" >&2 || true
    exit 3
  fi
fi

OVERALL_RC=0
SUMMARY="${OUT_DIR}/summary.md"
{
  echo "# A4.4 local-model smoke ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
  echo
  echo "Ollama: \`${OWNEVO_OLLAMA_HOST}\`"
  echo "Proxy: LiteLLM on :${PROXY_PORT}"
  echo
  echo "| model | demand-pred (recall ≥0.50) | credit-risk (balanced_acc ≥0.40) | contract-review (f1 ≥0.75) | wall | exit |"
  echo "|---|---:|---:|---:|---:|---:|"
} > "$SUMMARY"

for model in "${MODELS[@]}"; do
  log="${OUT_DIR}/${model}.jsonl"
  echo
  echo "[a4.4-smoke] === ${model} ==="
  rc=0
  ANTHROPIC_API_KEY=dummy \
  ANTHROPIC_AUTH_TOKEN=dummy \
    make -C "$REPO_ROOT" nl-gen-smoketest \
      WORKFLOW=all \
      SMOKE_ARGS="--from-fixtures --include-outcomes --anthropic-base-url http://localhost:${PROXY_PORT} --model ${model}" \
      2>&1 | tee "$log" || rc=$?

  if [[ $rc -ne 0 ]]; then
    OVERALL_RC=1
  fi

  # Extract per-workflow values + meets_target flags. Lines starting
  # with `{` are the JSON outputs.
  python3 - <<PY >> "$SUMMARY"
import json, pathlib
log = pathlib.Path("${log}").read_text().splitlines()
rows = {}
for line in log:
    if not line.startswith("{"):
        continue
    try:
        d = json.loads(line)
    except Exception:
        continue
    if "workflow_id" in d:
        rows[d["workflow_id"]] = d

def cell(wf):
    d = rows.get(wf)
    if not d:
        return "—"
    val = d.get("value")
    met = "✅" if d.get("meets_target") else "❌"
    return f"{val:.2f} {met}"

wall = sum(d.get("wall_seconds", 0) for d in rows.values())
print(
    "| ${model} | "
    + cell("demand-prediction") + " | "
    + cell("credit-risk") + " | "
    + cell("contract-review") + " | "
    + f"{wall:.1f}s | ${rc} |"
)
PY
done

echo
echo "[a4.4-smoke] summary written to $SUMMARY"
echo "[a4.4-smoke] per-model logs in $OUT_DIR"
cat "$SUMMARY"
exit $OVERALL_RC
