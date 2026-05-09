#!/usr/bin/env bash
# Sonnet 4.6 P2 improvement loop for τ³-retail — N gate cycles unattended.
#
# Each invocation of `run_tau3_loop.py` = one gate cycle: agent proposes ->
# 40-task retail test split eval -> persist. Subsequent cycles see prior
# attempts via `fetch_past_attempts` (compact metadata, not full history).
#
# Routing:
#   --api-format anthropic + --llm-base-url https://api.anthropic.com forces
#   the Anthropic cloud path (the loop's default base-url is LMS desktop, see
#   DEFAULT_LLM_BASE_URL_ANTHROPIC in run_tau3_loop.py).
#   --llm-api-key reads from .env's ANTHROPIC_API_KEY (the loop's default is
#   "lm-studio").
#
# Env vars (override defaults):
#   OWNEVO_TAU3_LOGDIR  log directory (default /tmp/tau3_p2_logs)
#   OWNEVO_TAU3_CYCLES  number of cycles (default 10)
#   OWNEVO_TAU3_WORKFLOW_ID  workflow id (default tau3-retail-v1 — the
#                            Sonnet baseline anchor at val=0.85+)
#
# Used to produce the val_score 0.85 -> 0.95 result on 2026-05-09 (skill v38,
# iter 11). See docs/TAU3_LOCAL_TESTPLAN.md § Phase 2.
set -u

KERNEL_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$KERNEL_DIR"

PASS=$(docker inspect ownevo-postgres \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep POSTGRES_PASSWORD | cut -d= -f2)
export OWNEVO_DATABASE_URL="postgresql://ownevo:${PASS}@localhost:5432/ownevo"

# Cloud Anthropic key for both the loop agent (this script's --llm-model)
# AND the task agent / user simulator (defaulted inside run_tau3_loop.py).
DOTENV="$KERNEL_DIR/../../.env"
if [[ -f "$DOTENV" ]]; then
    AKEY=$(grep '^ANTHROPIC_API_KEY=' "$DOTENV" | head -1 | cut -d= -f2- | tr -d '"'"'")
    [[ -n "$AKEY" ]] && export ANTHROPIC_API_KEY="$AKEY"
fi

LOGDIR="${OWNEVO_TAU3_LOGDIR:-/tmp/tau3_p2_logs}"
mkdir -p "$LOGDIR"

N_CYCLES="${OWNEVO_TAU3_CYCLES:-10}"
WORKFLOW_ID="${OWNEVO_TAU3_WORKFLOW_ID:-tau3-retail-v1}"
MASTER="$LOGDIR/sonnet_p2_master.log"

for i in $(seq 1 "$N_CYCLES"); do
    ts=$(date -u +%FT%TZ)
    log="$LOGDIR/sonnet_p2_cycle${i}.log"
    echo "=== [$ts] Sonnet P2 cycle $i/$N_CYCLES ===" | tee -a "$MASTER"

    uv run --extra agent python scripts/run_tau3_loop.py \
        --workflow-id "$WORKFLOW_ID" \
        --api-format anthropic \
        --llm-base-url https://api.anthropic.com \
        --llm-api-key "${ANTHROPIC_API_KEY:-}" \
        --llm-model claude-sonnet-4-6 \
        --task-concurrency 3 \
        --task-timeout-seconds 2400 \
        > "$log" 2>&1
    rc=$?

    ts_end=$(date -u +%FT%TZ)
    val=$(grep -o 'val_score=[0-9.]*' "$log" | tail -1 | cut -d= -f2)
    decision=$(grep -o 'decision=[A-Z_]*' "$log" | tail -1 | cut -d= -f2)
    echo "=== [$ts_end] cycle $i rc=$rc val_score=${val:-?} decision=${decision:-?}" \
        | tee -a "$MASTER"

    # Stop only on driver-side failure (e.g., 401 / context-exceeded). A
    # SANDBOX_ERROR on the proposed skill is rc=0 and the loop continues.
    if [[ $rc -ne 0 ]]; then
        echo "=== loop driver exited rc=$rc — stopping series" | tee -a "$MASTER"
        break
    fi
done

echo "=== P2 Sonnet series complete ===" | tee -a "$MASTER"
