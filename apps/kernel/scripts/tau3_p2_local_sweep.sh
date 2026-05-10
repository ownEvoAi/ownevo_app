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
# Preset shorthands (see tau3_p2_local_loop.sh for matching defs):
#   ollama          → http://$LLM_HOST:11434       api_format=ollama
#   ollama-openai   → http://$LLM_HOST:11434/v1    api_format=openai
#   lms-openai      → http://$LLM_HOST:1234/v1     api_format=openai
#   lms-anthropic   → http://$LLM_HOST:1234        api_format=anthropic
#
# Env vars:
#   OWNEVO_TAU3_LOGDIR  log directory (default <repo>/log/tau3_p2 — survives reboot)
#   OWNEVO_LLM_HOST     desktop ip (default 192.168.1.50)
set -u

KERNEL_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$KERNEL_DIR"

PASS=$(docker inspect ownevo-postgres \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep POSTGRES_PASSWORD | cut -d= -f2)
export OWNEVO_DATABASE_URL="postgresql://ownevo:${PASS}@localhost:5432/ownevo"

# Cloud key for the task agent + user simulator inside the sandbox (only
# used when task models default to anthropic/claude-*).
DOTENV="$KERNEL_DIR/../../.env"
if [[ -f "$DOTENV" ]]; then
    AKEY=$(grep -E '^(export )?ANTHROPIC_API_KEY=' "$DOTENV" | head -1 | sed 's/^export //' | cut -d= -f2- | tr -d '"'"'")
    [[ -z "$AKEY" ]] && AKEY=$(grep -E '^(export )?OWNEVO_LLM_API_KEY=' "$DOTENV" | head -1 | sed 's/^export //' | cut -d= -f2- | tr -d '"'"'")
    [[ -n "$AKEY" ]] && export ANTHROPIC_API_KEY="$AKEY"
fi

# Persist logs at project root so they survive reboot. /tmp gets wiped.
REPO_ROOT=$(cd "$KERNEL_DIR/../.." && pwd)
LOGDIR="${OWNEVO_TAU3_LOGDIR:-$REPO_ROOT/log/tau3_p2}"
LLM_HOST="${OWNEVO_LLM_HOST:-192.168.1.50}"
mkdir -p "$LOGDIR"

RESULTS="$LOGDIR/sweep_results.tsv"
MASTER="$LOGDIR/sweep_master.log"
echo -e "model\tpreset\tapi_format\tworkflow_id\tlog_file\texit_code\tts_start\tts_end" > "$RESULTS"

# Resolve a preset name to (BASE_URL, API_FORMAT, PROVIDER_LABEL).
# Stdout format: "<base_url>|<api_format>|<provider_label>".
_resolve_preset() {
    case "$1" in
        ollama)         echo "http://${LLM_HOST}:11434|ollama|ollama"        ;;
        ollama-openai)  echo "http://${LLM_HOST}:11434/v1|openai|ollama"     ;;
        lms-openai)     echo "http://${LLM_HOST}:1234/v1|openai|lms"         ;;
        lms-anthropic)  echo "http://${LLM_HOST}:1234|anthropic|lms"         ;;
        *) echo "error: unknown preset '$1' (want ollama|ollama-openai|lms-openai|lms-anthropic)" >&2; return 2 ;;
    esac
}

run_one() {
    # run_one <model> <preset> [task_agent_model] [task_user_model]
    local model="$1" preset="$2"
    local task_agent="${3:-anthropic/claude-sonnet-4-6}"
    local task_user="${4:-anthropic/claude-haiku-4-5-20251001}"

    local resolved
    resolved=$(_resolve_preset "$preset") || return 2
    local base_url="${resolved%%|*}"; local rest="${resolved#*|}"
    local api_format="${rest%%|*}"
    local provider="${rest#*|}"

    # Auto-export the right *_API_BASE for the task agent's LiteLLM
    # provider, scoped to this run_one invocation only — so subsequent
    # calls in the sweep don't inherit stale values from a prior preset.
    local prev_OAI_B="${OPENAI_API_BASE:-}"   prev_OAI_K="${OPENAI_API_KEY:-}"
    local prev_OLL_B="${OLLAMA_API_BASE:-}"   prev_ANT_B="${ANTHROPIC_API_BASE:-}"
    unset OPENAI_API_BASE OPENAI_API_KEY OLLAMA_API_BASE ANTHROPIC_API_BASE

    if [[ "$task_agent" == openai/* || "$task_user" == openai/* ]]; then
        export OPENAI_API_BASE="$base_url"
        export OPENAI_API_KEY="lm-studio"
    fi
    if [[ "$task_agent" == ollama_chat/* || "$task_agent" == ollama/* \
       || "$task_user" == ollama_chat/* || "$task_user" == ollama/* ]]; then
        export OLLAMA_API_BASE="${base_url%/v1}"
    fi
    if [[ "$task_agent" == anthropic/* || "$task_user" == anthropic/* ]]; then
        case "$base_url" in
            *api.anthropic.com*) : ;;
            *) export ANTHROPIC_API_BASE="$base_url" ;;
        esac
    fi

    local tag="${model//\//_}"; tag="${tag//:/_}"
    local workflow_id="tau3-retail-v1__${tag}"
    local log="$LOGDIR/sweep_${tag}.log"
    local extra=()
    [[ "$api_format" == "anthropic" ]] && extra+=(--no-stream)

    local ts_start
    ts_start=$(date -u +%FT%TZ)
    echo "=== [$ts_start] starting $model ($preset, $api_format) task=$task_agent workflow=$workflow_id" \
        | tee -a "$MASTER"

    uv run --extra agent python scripts/run_tau3_loop.py \
        --workflow-id "$workflow_id" \
        --api-format "$api_format" \
        --llm-base-url "$base_url" \
        --llm-model "$model" \
        --task-agent-model "$task_agent" \
        --task-user-model "$task_user" \
        --task-concurrency 2 \
        --task-timeout-seconds 2400 \
        ${extra[@]+"${extra[@]}"} \
        > "$log" 2>&1
    local rc=$?
    local ts_end
    ts_end=$(date -u +%FT%TZ)
    echo -e "${model}\t${preset}\t${api_format}\t${workflow_id}\t${log}\t${rc}\t${ts_start}\t${ts_end}" \
        >> "$RESULTS"
    echo "=== [$ts_end] finished $model rc=$rc" | tee -a "$MASTER"

    # Restore prior env (or leave unset if it was unset before).
    [[ -n "$prev_OAI_B" ]] && export OPENAI_API_BASE="$prev_OAI_B"   || unset OPENAI_API_BASE
    [[ -n "$prev_OAI_K" ]] && export OPENAI_API_KEY="$prev_OAI_K"   || unset OPENAI_API_KEY
    [[ -n "$prev_OLL_B" ]] && export OLLAMA_API_BASE="$prev_OLL_B"   || unset OLLAMA_API_BASE
    [[ -n "$prev_ANT_B" ]] && export ANTHROPIC_API_BASE="$prev_ANT_B" || unset ANTHROPIC_API_BASE
}

# Usage:
#   run_one <model> <preset> [task_agent_model] [task_user_model]
# If task_agent/task_user are omitted, they default to cloud Sonnet/Haiku.

# LM Studio sweep — loop=local, task+user=local granite-4.1-8b.
# Some entries are retests against models that failed on the 2026-05-09 sweep
# pre-bug-fixes (anthropic_api_base plumbing, env routing, /no_think for qwen3).
# Compat matrix at docs/TAU3_LOCAL_TESTPLAN.md § Local LLM compat matrix —
# update after each sweep.
run_one "qwen/qwen3.6-35b-a3b"            lms-openai "openai/granite-4.1-8b" "openai/granite-4.1-8b"
run_one "qwen/qwen3-30b-a3b-2507"         lms-openai "openai/granite-4.1-8b" "openai/granite-4.1-8b"
run_one "google/gemma-4-26b-a4b"          lms-openai "openai/granite-4.1-8b" "openai/granite-4.1-8b"
run_one "google/gemma-4-31b"              lms-openai "openai/granite-4.1-8b" "openai/granite-4.1-8b"
run_one "unsloth/gemma-4-26b-a4b-it"      lms-openai "openai/granite-4.1-8b" "openai/granite-4.1-8b"
run_one "qwen/qwen3-32b"                  lms-openai "openai/granite-4.1-8b" "openai/granite-4.1-8b"
run_one "qwen/qwen3-coder-30b"            lms-openai "openai/granite-4.1-8b" "openai/granite-4.1-8b"
run_one "qwen2.5-coder-32b-instruct"      lms-openai "openai/granite-4.1-8b" "openai/granite-4.1-8b"  # prior sweep: tool-call trigger issue (Ollama variant); retest under new sweep harness
run_one "mistralai/devstral-small-2-2512" lms-openai "openai/granite-4.1-8b" "openai/granite-4.1-8b"  # prior sweep: tool-error storm; retest with new harness
run_one "mistralai/ministral-3-14b-reasoning" lms-openai "openai/granite-4.1-8b" "openai/granite-4.1-8b"  # prior sweep: chat-template strict alternation
run_one "zai-org/glm-4.7-flash"           lms-openai "openai/granite-4.1-8b" "openai/granite-4.1-8b"  # prior sweep: kickoff exceeded context window

# Ollama sweep — note ollama_chat/ prefix is required for LiteLLM routing.
# qwen3-coder:30b auto-injects /no_think (see middleware/claude_sdk/runner.py).
run_one "qwen3-coder:30b"                 ollama-openai "ollama_chat/granite4.1:8b" "ollama_chat/granite4.1:8b"
run_one "gemma4:26b"                      ollama-openai "ollama_chat/granite4.1:8b" "ollama_chat/granite4.1:8b"

# More combos to consider (uncomment to run):
# run_one "gemma4:26b"                      ollama        "ollama_chat/granite4.1:8b" "ollama_chat/granite4.1:8b"   # native /api/chat path
# run_one "qwen/qwen3.6-35b-a3b"            lms-anthropic "anthropic/granite-4.1-8b"  "anthropic/granite-4.1-8b"

echo "=== sweep complete ==="
column -t -s $'\t' "$RESULTS" | tee -a "$MASTER"
