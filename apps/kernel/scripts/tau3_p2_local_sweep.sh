#!/usr/bin/env bash
# Sequential local-model diagnostic sweep through P2 loop. Each model runs
# under its own --workflow-id (`tau3-retail-v1__<tag>`) so its gate history
# is independent of the Sonnet 0.95 anchor; the gate compares val_score
# against `MAX(best_ever_score_after) WHERE workflow_id=$1`, starting at 0
# for a fresh per-model workflow.
#
# All listed models share the same desktop GPU on $OWNEVO_LLM_HOST, so
# concurrency must be 1 (sequential). Each model gets one gate cycle —
# enough to answer "drives the loop or not"; multi-cycle runs use
# `tau3_p2_local_loop.sh`.
#
# 2026-05-09 results (the canonical run) recorded in
# docs/TAU3_LOCAL_TESTPLAN.md § Phase 2 § Local model sweep.
#
# Env vars:
#   OWNEVO_TAU3_LOGDIR  log directory (default /tmp/tau3_p2_logs)
#   OWNEVO_LLM_HOST     desktop ip (default localhost)
set -u

KERNEL_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$KERNEL_DIR"

PASS=$(docker inspect ownevo-postgres \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep POSTGRES_PASSWORD | cut -d= -f2)
export OWNEVO_DATABASE_URL="postgresql://ownevo:${PASS}@localhost:5432/ownevo"

# Cloud key for the task agent + user simulator inside the sandbox.
DOTENV="$KERNEL_DIR/../../.env"
if [[ -f "$DOTENV" ]]; then
    AKEY=$(grep '^ANTHROPIC_API_KEY=' "$DOTENV" | head -1 | cut -d= -f2- | tr -d '"'"'")
    [[ -n "$AKEY" ]] && export ANTHROPIC_API_KEY="$AKEY"
fi

LOGDIR="${OWNEVO_TAU3_LOGDIR:-/tmp/tau3_p2_logs}"
LLM_HOST="${OWNEVO_LLM_HOST:-localhost}"
mkdir -p "$LOGDIR"

RESULTS="$LOGDIR/sweep_results.tsv"
MASTER="$LOGDIR/sweep_master.log"
echo -e "model\tprovider\tapi_format\tworkflow_id\tlog_file\texit_code\tts_start\tts_end" > "$RESULTS"

run_one() {
    local model="$1" provider="$2" api_format="$3" base_url="$4"
    local tag="${model//\//_}"; tag="${tag//:/_}"
    local workflow_id="tau3-retail-v1__${tag}"
    local log="$LOGDIR/sweep_${tag}.log"
    local extra=()
    [[ "$api_format" == "anthropic" ]] && extra+=(--no-stream)

    local ts_start
    ts_start=$(date -u +%FT%TZ)
    echo "=== [$ts_start] starting $model ($provider, $api_format) workflow=$workflow_id" \
        | tee -a "$MASTER"

    uv run --extra agent python scripts/run_tau3_loop.py \
        --workflow-id "$workflow_id" \
        --api-format "$api_format" \
        --llm-base-url "$base_url" \
        --llm-model "$model" \
        --task-concurrency 3 \
        --task-timeout-seconds 2400 \
        ${extra[@]+"${extra[@]}"} \
        > "$log" 2>&1
    local rc=$?
    local ts_end
    ts_end=$(date -u +%FT%TZ)
    echo -e "${model}\t${provider}\t${api_format}\t${workflow_id}\t${log}\t${rc}\t${ts_start}\t${ts_end}" \
        >> "$RESULTS"
    echo "=== [$ts_end] finished $model rc=$rc" | tee -a "$MASTER"
}

# Ollama models — OpenAI format
run_one "qwen3:32b"          "ollama" "openai" "http://${LLM_HOST}:11434/v1"
run_one "granite4.1:30b"     "ollama" "openai" "http://${LLM_HOST}:11434/v1"
run_one "gemma4:26b"         "ollama" "openai" "http://${LLM_HOST}:11434/v1"

# LM Studio models — OpenAI format. Anthropic format on LMS hits the SDK
# streaming guard (max_tokens + non-streaming is rejected when the request
# could exceed 10 min); stick with OpenAI.
run_one "mistralai/devstral-small-2-2512"      "lms" "openai" "http://${LLM_HOST}:1234/v1"
run_one "mistralai/ministral-3-14b-reasoning"  "lms" "openai" "http://${LLM_HOST}:1234/v1"
run_one "zai-org/glm-4.7-flash"                "lms" "openai" "http://${LLM_HOST}:1234/v1"

echo "=== sweep complete ==="
column -t -s $'\t' "$RESULTS" | tee -a "$MASTER"
