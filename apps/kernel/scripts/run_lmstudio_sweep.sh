#!/usr/bin/env bash
#
# Full --from-fixtures gate sweep against all models loaded in LM Studio,
# using the OpenAI-compat /v1/chat/completions API directly.
#
# Usage:
#   OWNEVO_LMSTUDIO_HOST=http://localhost:1234 bash apps/kernel/scripts/run_lmstudio_sweep.sh
#   OWNEVO_LMSTUDIO_HOST=http://localhost:1234 bash apps/kernel/scripts/run_lmstudio_sweep.sh <model-id>
#
# First positional arg restricts to one model id (exact LM Studio model id).
# Default: all models from the LM Studio /v1/models endpoint minus known skips.
#
# Exit 0 iff every requested model met target on every workflow.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OWNEVO_LMSTUDIO_HOST="${OWNEVO_LMSTUDIO_HOST:-http://localhost:1234}"
OPENAI_BASE_URL="${OWNEVO_LMSTUDIO_HOST}/v1"
OUT_DIR="${REPO_ROOT}/temp/lmstudio_sweep/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT_DIR"

# Pre-flight: LM Studio reachable?
if ! curl -fsS --max-time 5 "${OPENAI_BASE_URL}/models" >/dev/null 2>&1; then
  echo "[lmstudio-sweep] ABORT: LM Studio not reachable at ${OPENAI_BASE_URL}/models" >&2
  exit 2
fi

# Model id fragments to skip: embedding-only and vision-only models.
SKIP_PATTERNS=(
  "embed"
  "whisper"
  "vision"
  "vl-"
  "-vl"
  "-asr"
  "_asr"
  "vibevoice"
)

# Fetch all models from LM Studio.
ALL_MODELS=$(curl -fsS "${OPENAI_BASE_URL}/models" | python3 -c "
import json, sys
models = json.load(sys.stdin).get('data', [])
for m in sorted(models, key=lambda x: x['id']):
    print(m['id'])
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
  echo "[lmstudio-sweep] no models to sweep after filtering" >&2
  exit 2
fi

echo "[lmstudio-sweep] host=${OWNEVO_LMSTUDIO_HOST}  models=${#MODELS[@]}"
echo "[lmstudio-sweep] output → ${OUT_DIR}"

SUMMARY="${OUT_DIR}/summary.md"
{
  echo "# LM Studio OpenAI-compat sweep ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
  echo
  echo "Host: \`${OWNEVO_LMSTUDIO_HOST}\`  API: OpenAI /v1/chat/completions (direct)"
  echo
  echo "| model | demand-pred (recall ≥0.50) | credit-risk (balanced_acc ≥0.40) | contract-review (f1 ≥0.75) | wall | exit |"
  echo "|---|---:|---:|---:|---:|---:|"
} > "$SUMMARY"

LMS_CTX="${LMS_CONTEXT_LENGTH:-32768}"
LOAD_URL="${OWNEVO_LMSTUDIO_HOST}/api/v1/models/load"
UNLOAD_URL="${OWNEVO_LMSTUDIO_HOST}/api/v1/models/unload"

lms_load() {
  # Returns the instance_id on stdout; caller captures it for lms_unload.
  # Tries LMS_CTX first, then falls back to 16384 and 8192 if LMS rejects the load (OOM).
  local model="$1"
  local ctx instance_id body
  for ctx in "${LMS_CTX}" 16384 8192; do
    body=$(curl -fsS --max-time 60 -X POST "$LOAD_URL" \
      -H "Content-Type: application/json" \
      -d "{\"model\": \"${model}\", \"context_length\": ${ctx}, \"flash_attention\": true, \"echo_load_config\": true}" \
      2>/dev/null) || true
    if [[ -z "$body" ]]; then
      echo "[lmstudio-sweep] WARN: empty/failed response loading ${model} at ctx=${ctx} — trying smaller context" >&2
      continue
    fi
    instance_id=$(echo "$body" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('instance_id',''))" 2>/dev/null) || true
    if [[ -n "$instance_id" ]]; then
      echo "[lmstudio-sweep] loaded ${model} at ctx=${ctx} → instance_id=${instance_id}" >&2
      echo "$instance_id"
      return 0
    fi
    echo "[lmstudio-sweep] WARN: no instance_id for ${model} at ctx=${ctx} — trying smaller context" >&2
  done
  echo "[lmstudio-sweep] WARN: could not load ${model} at any context size — will use whatever is already in LMS" >&2
  echo ""
}

lms_unload() {
  local instance_id="$1"
  [[ -z "$instance_id" ]] && return 0
  curl -fsS -X POST "$UNLOAD_URL" \
    -H "Content-Type: application/json" \
    -d "{\"instance_id\": \"${instance_id}\"}" \
    >/dev/null 2>&1 || true
}

OVERALL_RC=0

for model in "${MODELS[@]}"; do
  log="${OUT_DIR}/$(echo "$model" | tr '/: ' '___').jsonl"
  echo
  echo "[lmstudio-sweep] === ${model} ==="

  echo "[lmstudio-sweep] loading ${model} ctx=${LMS_CTX}..."
  instance_id=$(lms_load "$model")
  if [[ -n "$instance_id" ]]; then
    echo "[lmstudio-sweep] loaded instance_id=${instance_id}"
  else
    echo "[lmstudio-sweep] load failed or instance_id empty — falling back to model name '${model}' (context may be wrong)" >&2
  fi

  # Use instance_id as model name so LM Studio routes to our 32k instance,
  # not the default-context one that may already be loaded.
  run_model="${instance_id:-$model}"

  rc=0
  uv run --directory "$REPO_ROOT/apps/kernel" --extra agent \
    python scripts/nl_gen_smoketest.py \
      --workflow all \
      --from-fixtures \
      --model "$run_model" \
      --openai-base-url "$OPENAI_BASE_URL" \
      --include-outcomes \
    2>&1 | tee "$log" || rc=$?

  lms_unload "$instance_id"

  [[ $rc -ne 0 ]] && OVERALL_RC=1

  MODEL="${model}" RC="${rc}" LOG="${log}" \
    python3 "$REPO_ROOT/apps/kernel/scripts/_sweep_parse_log.py" >> "$SUMMARY" \
    || echo "| (summary-gen failed for model) | — | — | — | — | — |" >> "$SUMMARY"
done

echo
echo "[lmstudio-sweep] summary written to $SUMMARY"
cat "$SUMMARY"
exit $OVERALL_RC
