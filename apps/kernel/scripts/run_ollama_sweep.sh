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
      --include-outcomes \
    2>&1 | tee "$log" || rc=$?

  [[ $rc -ne 0 ]] && OVERALL_RC=1

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
echo "[ollama-sweep] summary written to $SUMMARY"
cat "$SUMMARY"
exit $OVERALL_RC
