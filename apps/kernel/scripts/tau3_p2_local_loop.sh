#!/usr/bin/env bash
# τ³-retail P2 loop driven by a local model (Ollama or LM Studio at
# $OWNEVO_LLM_HOST). Loop agent runs locally (free); task agent + user
# simulator stay on cloud Anthropic via tau2 inside the sandbox.
#
# Used to test "can a local model drive the τ³ improvement loop and produce
# lift" on a workflow_id that's independent of the Sonnet baseline anchor —
# this matters because gate compares against
# `MAX(best_ever_score_after) WHERE workflow_id=$1`, so per-model workflow_id
# is the only way to grade a local model on its own merits.
#
# Required args (positional):
#   $1  model id           e.g. "qwen/qwen3.6-35b-a3b" (LMS) or "gemma4:26b" (Ollama)
#   $2  base url           http://192.168.1.50:1234/v1 (LMS) or :11434/v1 (Ollama)
#   $3  workflow tag       e.g. "qwen36"  (becomes workflow_id tau3-retail-v1__qwen36)
#
# Env vars (override defaults):
#   OWNEVO_TAU3_LOGDIR  log directory (default /tmp/tau3_p2_logs)
#   OWNEVO_TAU3_CYCLES  number of cycles (default 10)
#
# Example — qwen3.6-35b-a3b on LMS desktop (the 2026-05-09 multi-cycle run
# that hit val=0.85 twice on cycles 2 and 5):
#   bash scripts/tau3_p2_local_loop.sh \
#     "qwen/qwen3.6-35b-a3b" "http://192.168.1.50:1234/v1" "qwen36"
#
# Example — gemma4:26b on Ollama desktop:
#   bash scripts/tau3_p2_local_loop.sh \
#     "gemma4:26b" "http://192.168.1.50:11434/v1" "gemma4_26b"
set -u

if [[ $# -lt 3 ]]; then
    echo "usage: $0 <model> <base_url> <workflow_tag>" >&2
    echo "example: $0 'qwen/qwen3.6-35b-a3b' 'http://192.168.1.50:1234/v1' 'qwen36'" >&2
    exit 2
fi

MODEL="$1"
BASE_URL="$2"
WORKFLOW_TAG="$3"

KERNEL_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$KERNEL_DIR"

PASS=$(docker inspect ownevo-postgres \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep POSTGRES_PASSWORD | cut -d= -f2)
export OWNEVO_DATABASE_URL="postgresql://ownevo:${PASS}@localhost:5432/ownevo"

# Loop agent runs locally so it doesn't need ANTHROPIC_API_KEY, but the
# task agent + user simulator (default Sonnet 4.6 + Haiku 4.5) inside the
# sandbox DO. Load the cloud key from .env.
DOTENV="$KERNEL_DIR/../../.env"
if [[ -f "$DOTENV" ]]; then
    AKEY=$(grep '^ANTHROPIC_API_KEY=' "$DOTENV" | head -1 | cut -d= -f2- | tr -d '"'"'")
    [[ -n "$AKEY" ]] && export ANTHROPIC_API_KEY="$AKEY"
fi

LOGDIR="${OWNEVO_TAU3_LOGDIR:-/tmp/tau3_p2_logs}"
mkdir -p "$LOGDIR"

N_CYCLES="${OWNEVO_TAU3_CYCLES:-10}"
WORKFLOW_ID="tau3-retail-v1__${WORKFLOW_TAG}"
MASTER="$LOGDIR/${WORKFLOW_TAG}_p2_master.log"

for i in $(seq 1 "$N_CYCLES"); do
    ts=$(date -u +%FT%TZ)
    log="$LOGDIR/${WORKFLOW_TAG}_p2_cycle${i}.log"
    echo "=== [$ts] $WORKFLOW_TAG P2 cycle $i/$N_CYCLES ===" | tee -a "$MASTER"

    uv run --extra agent python scripts/run_tau3_loop.py \
        --workflow-id "$WORKFLOW_ID" \
        --api-format openai \
        --llm-base-url "$BASE_URL" \
        --llm-model "$MODEL" \
        --task-concurrency 3 \
        --task-timeout-seconds 2400 \
        > "$log" 2>&1
    rc=$?

    ts_end=$(date -u +%FT%TZ)
    val=$(grep -o 'val_score=[0-9.]*' "$log" | tail -1 | cut -d= -f2)
    decision=$(grep -o 'decision=[A-Z_]*' "$log" | tail -1 | cut -d= -f2)
    echo "=== [$ts_end] cycle $i rc=$rc val_score=${val:-?} decision=${decision:-?}" \
        | tee -a "$MASTER"

    if [[ $rc -ne 0 ]]; then
        echo "=== loop driver exited rc=$rc — stopping series" | tee -a "$MASTER"
        break
    fi
done

echo "=== $WORKFLOW_TAG P2 series complete ===" | tee -a "$MASTER"
