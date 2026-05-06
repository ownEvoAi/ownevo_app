#!/usr/bin/env bash
#
# Full --from-fixtures gate sweep against all models loaded in LM Studio,
# using the OpenAI-compat /v1/chat/completions API directly.
#
# Usage:
#   OWNEVO_LMSTUDIO_HOST=http://192.168.1.50:1234 bash apps/kernel/scripts/run_lmstudio_sweep.sh
#   OWNEVO_LMSTUDIO_HOST=http://localhost:1234 bash apps/kernel/scripts/run_lmstudio_sweep.sh <model-id>
#
# First positional arg restricts to one model id (exact LM Studio model id).
# Default: all models from the LM Studio /v1/models endpoint minus known skips.
#
# Exit 0 iff every requested model met target on every workflow.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OWNEVO_LMSTUDIO_HOST="${OWNEVO_LMSTUDIO_HOST:-http://192.168.1.50:1234}"
OPENAI_BASE_URL="${OWNEVO_LMSTUDIO_HOST}/v1"
OUT_DIR="${REPO_ROOT}/temp/lmstudio_sweep/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT_DIR"

# Model id fragments to skip: embedding-only and vision-only models.
SKIP_PATTERNS=(
  "embed"
  "whisper"
  "vision"
  "vl-"
  "-vl"
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

echo "[lmstudio-sweep] host=${OWNEVO_LMSTUDIO_HOST}  models=${#MODELS[@]}"
echo "[lmstudio-sweep] output → ${OUT_DIR}"

# Pre-flight: LM Studio reachable?
if ! curl -fsS --max-time 5 "${OPENAI_BASE_URL}/models" >/dev/null 2>&1; then
  echo "[lmstudio-sweep] ABORT: LM Studio not reachable at ${OPENAI_BASE_URL}/models" >&2
  exit 2
fi

SUMMARY="${OUT_DIR}/summary.md"
{
  echo "# LM Studio OpenAI-compat sweep ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
  echo
  echo "Host: \`${OWNEVO_LMSTUDIO_HOST}\`  API: OpenAI /v1/chat/completions (direct)"
  echo
  echo "| model | demand-pred (recall ≥0.50) | credit-risk (balanced_acc ≥0.40) | contract-review (f1 ≥0.75) | wall | exit |"
  echo "|---|---:|---:|---:|---:|---:|"
} > "$SUMMARY"

OVERALL_RC=0

for model in "${MODELS[@]}"; do
  log="${OUT_DIR}/$(echo "$model" | tr '/: ' '___').jsonl"
  echo
  echo "[lmstudio-sweep] === ${model} ==="
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
echo "[lmstudio-sweep] summary written to $SUMMARY"
cat "$SUMMARY"
exit $OVERALL_RC
