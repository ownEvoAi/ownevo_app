#!/usr/bin/env bash
#
# Full --from-fixtures gate sweep against all text-capable models on an
# Ollama daemon, using the OpenAI-compat /v1/chat/completions API directly
# (no LiteLLM proxy).
#
# Usage:
#   OWNEVO_OLLAMA_HOST=http://192.168.1.50:11434 bash apps/kernel/scripts/run_ollama_sweep.sh
#   OWNEVO_OLLAMA_HOST=http://localhost:11434 bash apps/kernel/scripts/run_ollama_sweep.sh <model-name>
#
# First positional arg restricts to one model name (exact Ollama model tag).
# Default: all models from the Ollama /api/tags endpoint minus known skips.
#
# Exit 0 iff every requested model met target on every workflow.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OWNEVO_OLLAMA_HOST="${OWNEVO_OLLAMA_HOST:-http://192.168.1.50:11434}"
OPENAI_BASE_URL="${OWNEVO_OLLAMA_HOST}/v1"
OUT_DIR="${REPO_ROOT}/temp/ollama_sweep/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT_DIR"

# Pre-flight: Ollama reachable? Must be before the ALL_MODELS fetch so the
# error message fires instead of a raw curl failure under set -euo pipefail.
if ! curl -fsS --max-time 5 "${OWNEVO_OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
  echo "[ollama-sweep] ABORT: Ollama not reachable at ${OWNEVO_OLLAMA_HOST}/api/tags" >&2
  exit 2
fi

# Model tags to skip: embedding-only and vision-only models.
SKIP_PATTERNS=(
  "all-minilm"
  "mxbai-embed"
  "nomic-embed"
  "granite-embedding"
  "whisper"
  "minicpm-v"
  "llama3.2-vision"
  "qwen2.5vl"
  "qwen3-vl"
  "openbmb"
  "ZimaBlueAI"
)

# Fetch all models from Ollama.
ALL_MODELS=$(curl -fsS "${OWNEVO_OLLAMA_HOST}/api/tags" | python3 -c "
import json, sys
models = json.load(sys.stdin)['models']
for m in sorted(models, key=lambda x: x['name']):
    print(m['name'])
")

# Apply skip filter.
MODELS=()
if [[ $# -ge 1 ]]; then
  MODELS=("$1")
else
  while IFS= read -r model; do
    skip=0
    for pat in "${SKIP_PATTERNS[@]}"; do
      if [[ "$model" == *"$pat"* ]]; then
        skip=1; break
      fi
    done
    [[ $skip -eq 0 ]] && MODELS+=("$model")
  done <<< "$ALL_MODELS"
fi

if [[ ${#MODELS[@]} -eq 0 ]]; then
  echo "[ollama-sweep] no models to sweep after filtering" >&2
  exit 2
fi

echo "[ollama-sweep] host=${OWNEVO_OLLAMA_HOST}  models=${#MODELS[@]}"
echo "[ollama-sweep] output → ${OUT_DIR}"

SUMMARY="${OUT_DIR}/summary.md"
{
  echo "# Ollama OpenAI-compat sweep ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
  echo
  echo "Host: \`${OWNEVO_OLLAMA_HOST}\`  API: OpenAI /v1/chat/completions (direct)"
  echo
  echo "| model | demand-pred (recall ≥0.50) | credit-risk (balanced_acc ≥0.40) | contract-review (f1 ≥0.75) | wall | exit |"
  echo "|---|---:|---:|---:|---:|---:|"
} > "$SUMMARY"

OVERALL_RC=0

for model in "${MODELS[@]}"; do
  log="${OUT_DIR}/$(echo "$model" | tr '/:' '__').jsonl"
  echo
  echo "[ollama-sweep] === ${model} ==="
  rc=0
  uv run --directory "$REPO_ROOT/apps/kernel" --extra agent \
    python scripts/nl_gen_smoketest.py \
      --workflow all \
      --from-fixtures \
      --model "$model" \
      --openai-base-url "$OPENAI_BASE_URL" \
      --max-tokens "${OLLAMA_SWEEP_MAX_TOKENS:-10000}" \
      --include-outcomes \
    2>&1 | tee "$log" || rc=$?

  [[ $rc -ne 0 ]] && OVERALL_RC=1

  MODEL="${model}" RC="${rc}" LOG="${log}" \
    python3 "$REPO_ROOT/apps/kernel/scripts/_sweep_parse_log.py" >> "$SUMMARY" \
    || echo "| (summary-gen failed for model) | — | — | — | — | — |" >> "$SUMMARY"

  # Evict the just-tested model so the next one doesn't co-tenant on VRAM
  # while the prior model is still in its 5-min keep_alive window.
  curl -fsS --max-time 10 -X POST "${OWNEVO_OLLAMA_HOST}/api/generate" \
    -H "Content-Type: application/json" \
    -d "{\"model\": \"${model}\", \"keep_alive\": 0}" \
    >/dev/null 2>&1 || true
done

echo
echo "[ollama-sweep] summary written to $SUMMARY"
cat "$SUMMARY"
exit $OVERALL_RC
