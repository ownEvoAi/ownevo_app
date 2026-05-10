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
# 2026-05-09 results recorded in
# docs/TAU3_LOCAL_TESTPLAN.md § Phase 2 § Local model sweep + compat matrix.
#
# Preset shorthands (matching tau3_p2_local_loop.sh):
#   ollama          → http://$LLM_HOST:11434       api_format=ollama
#   ollama-openai   → http://$LLM_HOST:11434/v1    api_format=openai
#   lms-openai      → http://$LLM_HOST:1234/v1     api_format=openai
#   lms-anthropic   → http://$LLM_HOST:1234        api_format=anthropic
#
# Env vars:
#   OWNEVO_TAU3_LOGDIR  log directory (default <repo>/log/tau3_p2 — survives reboot)
#   OWNEVO_LLM_HOST     desktop ip (default 192.168.1.50)
#   OWNEVO_TAU3_PHASES  comma-separated phase list to run (default "1,2")
#                       Phases:
#                         1  task=user=qwen/qwen3.6-35b-a3b   (35B evaluator)
#                         2  task=user=granite-4.1-8b         (8B evaluator)
#                         3  full LMS sweep (cloud Sonnet/Haiku evaluator)
set -u

KERNEL_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$KERNEL_DIR"

PASS=$(docker inspect ownevo-postgres \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep POSTGRES_PASSWORD | cut -d= -f2)
export OWNEVO_DATABASE_URL="postgresql://ownevo:${PASS}@localhost:5432/ownevo"

DOTENV="$KERNEL_DIR/../../.env"
if [[ -f "$DOTENV" ]]; then
    AKEY=$(grep -E '^(export )?ANTHROPIC_API_KEY=' "$DOTENV" | head -1 | sed 's/^export //' | cut -d= -f2- | tr -d '"'"'")
    [[ -z "$AKEY" ]] && AKEY=$(grep -E '^(export )?OWNEVO_LLM_API_KEY=' "$DOTENV" | head -1 | sed 's/^export //' | cut -d= -f2- | tr -d '"'"'")
    [[ -n "$AKEY" ]] && export ANTHROPIC_API_KEY="$AKEY"
fi

REPO_ROOT=$(cd "$KERNEL_DIR/../.." && pwd)
LOGDIR="${OWNEVO_TAU3_LOGDIR:-$REPO_ROOT/log/tau3_p2}"
LLM_HOST="${OWNEVO_LLM_HOST:-192.168.1.50}"
PHASES="${OWNEVO_TAU3_PHASES:-1,2}"
mkdir -p "$LOGDIR"

RESULTS="$LOGDIR/sweep_results.tsv"
MASTER="$LOGDIR/sweep_master.log"
[[ -f "$RESULTS" ]] || \
    echo -e "phase\tmodel\tpreset\tapi_format\ttask_agent\tworkflow_id\tlog_file\texit_code\tval_score\tdecision\tts_start\tts_end" \
    > "$RESULTS"

# ---------------------------------------------------------------------------
# LMS load/unload helpers — keeps task+user weights resident across runs.
# Ollama isn't load-managed here: $LLM_HOST has OLLAMA_MAX_LOADED_MODELS=1
# (see ~/ollama_open_web.sh), so Ollama already serializes to one model.
# ---------------------------------------------------------------------------
LMS_BIN="${LMS_BIN:-lms}"
_lms_loaded() {
    "$LMS_BIN" ps --json 2>/dev/null \
      | python3 -c "import json,sys; print('\n'.join(m['modelKey'] for m in json.load(sys.stdin)))"
}

lms_load() {
    local model="$1"
    if _lms_loaded | grep -qxF "$model"; then
        echo "lms: $model already loaded" | tee -a "$MASTER"
        return 0
    fi
    echo "lms: loading $model" | tee -a "$MASTER"
    "$LMS_BIN" load "$model" 2>&1 | tee -a "$MASTER" || {
        echo "lms: load FAILED for $model" | tee -a "$MASTER"; return 1; }
}

lms_unload() {
    local model="$1"
    if ! _lms_loaded | grep -qxF "$model"; then
        echo "lms: $model not loaded — skip unload" | tee -a "$MASTER"
        return 0
    fi
    echo "lms: unloading $model" | tee -a "$MASTER"
    "$LMS_BIN" unload "$model" 2>&1 | tee -a "$MASTER" || true
}

# ---------------------------------------------------------------------------
# Preset resolver + per-call env routing
# ---------------------------------------------------------------------------
_resolve_preset() {
    case "$1" in
        ollama)         echo "http://${LLM_HOST}:11434|ollama|ollama"        ;;
        ollama-openai)  echo "http://${LLM_HOST}:11434/v1|openai|ollama"     ;;
        lms-openai)     echo "http://${LLM_HOST}:1234/v1|openai|lms"         ;;
        lms-anthropic)  echo "http://${LLM_HOST}:1234|anthropic|lms"         ;;
        *) echo "error: unknown preset '$1'" >&2; return 2 ;;
    esac
}

# run_one <phase> <model> <preset> [task_agent_model] [task_user_model]
# Returns: exit code from run_tau3_loop.py.
run_one() {
    local phase="$1" model="$2" preset="$3"
    local task_agent="${4:-anthropic/claude-sonnet-4-6}"
    local task_user="${5:-anthropic/claude-haiku-4-5-20251001}"

    local resolved
    resolved=$(_resolve_preset "$preset") || return 2
    local base_url="${resolved%%|*}"; local rest="${resolved#*|}"
    local api_format="${rest%%|*}"

    # Save / restore api-base env per call so successive presets don't
    # inherit stale values.
    local prev_OAI_B="${OPENAI_API_BASE:-}"   prev_OAI_K="${OPENAI_API_KEY:-}"
    local prev_OLL_B="${OLLAMA_API_BASE:-}"   prev_ANT_B="${ANTHROPIC_API_BASE:-}"
    unset OPENAI_API_BASE OPENAI_API_KEY OLLAMA_API_BASE ANTHROPIC_API_BASE

    if [[ "$task_agent" == openai/* || "$task_user" == openai/* ]]; then
        export OPENAI_API_BASE="$base_url"; export OPENAI_API_KEY="lm-studio"
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
    local task_tag="${task_agent//\//_}"; task_tag="${task_tag//:/_}"
    local workflow_id="tau3-retail-v1__p${phase}__${tag}__eval_${task_tag}"
    local log="$LOGDIR/p${phase}_${tag}__${preset}.log"
    local extra=()
    [[ "$api_format" == "anthropic" ]] && extra+=(--no-stream)

    local ts_start
    ts_start=$(date -u +%FT%TZ)
    echo "=== [$ts_start] P${phase} loop=$model preset=$preset task=$task_agent workflow=$workflow_id" \
        | tee -a "$MASTER"

    PYTHONUNBUFFERED=1 uv run --extra agent python -u scripts/run_tau3_loop.py \
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
    local val=$(grep -oE 'val_score=[0-9.]+' "$log" | tail -1 | cut -d= -f2)
    local decision=$(grep -oE 'decision=[A-Z_]+' "$log" | tail -1 | cut -d= -f2)
    echo -e "${phase}\t${model}\t${preset}\t${api_format}\t${task_agent}\t${workflow_id}\t${log}\t${rc}\t${val:-?}\t${decision:-?}\t${ts_start}\t${ts_end}" \
        >> "$RESULTS"
    echo "=== [$ts_end] rc=$rc val=${val:-?} decision=${decision:-?}" | tee -a "$MASTER"

    [[ -n "$prev_OAI_B" ]] && export OPENAI_API_BASE="$prev_OAI_B"   || unset OPENAI_API_BASE
    [[ -n "$prev_OAI_K" ]] && export OPENAI_API_KEY="$prev_OAI_K"   || unset OPENAI_API_KEY
    [[ -n "$prev_OLL_B" ]] && export OLLAMA_API_BASE="$prev_OLL_B"   || unset OLLAMA_API_BASE
    [[ -n "$prev_ANT_B" ]] && export ANTHROPIC_API_BASE="$prev_ANT_B" || unset ANTHROPIC_API_BASE

    return $rc
}

# Try presets in order; return on first that produces a parseable val_score
# (a numeric value in the log). rc-only fallback isn't enough because a
# successful loop driver can still produce 0 tool calls / no proposal.
run_with_fallback() {
    local phase="$1" model="$2" task_agent="$3" task_user="$4"; shift 4
    local presets=("$@") preset rc=0
    for preset in "${presets[@]}"; do
        run_one "$phase" "$model" "$preset" "$task_agent" "$task_user"
        rc=$?
        local last_val
        last_val=$(awk -F'\t' -v m="$model" -v p="$preset" \
            '$2==m && $3==p {v=$9} END{print v}' "$RESULTS")
        if [[ $rc -eq 0 && -n "$last_val" && "$last_val" != "?" ]]; then
            echo ">>> $model on $preset produced val=$last_val — keeping" | tee -a "$MASTER"
            return 0
        fi
        echo ">>> $model on $preset failed (rc=$rc val=${last_val:-?}) — trying next preset" | tee -a "$MASTER"
    done
    echo ">>> $model exhausted preset list — all failed" | tee -a "$MASTER"
    return 1
}

# Pre-warm an Ollama model so the first request doesn't pay full load latency.
# Ollama itself enforces MAX_LOADED_MODELS=1 — no unload helper needed.
ollama_warm() {
    local model="$1"
    echo "ollama: warming $model" | tee -a "$MASTER"
    curl -sS --max-time 120 \
        "http://${LLM_HOST}:11434/api/generate" \
        -d "{\"model\":\"${model}\",\"keep_alive\":-1,\"prompt\":\"\"}" \
        > /dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# PHASE 1 — task=user=qwen/qwen3.6-35b-a3b (35B evaluator, all-local)
#
# When loop=qwen36, only one LMS model is loaded (loop and task share
# the same weights). When loop=gemma4-26b-a4b, both LMS models stay
# resident (~22 + 18 = 40 GB on the 48 GB 2x3090). When loop=gemma4:26b,
# qwen36 stays in LMS, gemma4:26b loads in Ollama (MAX_LOADED_MODELS=1).
# ---------------------------------------------------------------------------
phase1_qwen36_eval() {
    echo "===== PHASE 1 — task=user=qwen/qwen3.6-35b-a3b =====" | tee -a "$MASTER"
    lms_load "qwen/qwen3.6-35b-a3b" || return 1

    # loop=qwen36 — same model as task+user, single load
    run_with_fallback 1 "qwen/qwen3.6-35b-a3b" \
        "openai/qwen/qwen3.6-35b-a3b" "openai/qwen/qwen3.6-35b-a3b" \
        lms-openai lms-anthropic

    # loop=gemma4-26b-a4b — needs second LMS model resident
    lms_load "google/gemma-4-26b-a4b" || true
    run_with_fallback 1 "google/gemma-4-26b-a4b" \
        "openai/qwen/qwen3.6-35b-a3b" "openai/qwen/qwen3.6-35b-a3b" \
        lms-openai lms-anthropic
    lms_unload "google/gemma-4-26b-a4b"

    # loop=gemma4:26b on Ollama — task+user stay on LMS qwen36
    ollama_warm "gemma4:26b"
    run_with_fallback 1 "gemma4:26b" \
        "openai/qwen/qwen3.6-35b-a3b" "openai/qwen/qwen3.6-35b-a3b" \
        ollama-openai ollama
}

# ---------------------------------------------------------------------------
# PHASE 2 — task=user=granite-4.1-8b (8B evaluator, all-local, low VRAM)
#
# Same loop models as Phase 1. Granite is small (~5 GB) so we can keep
# every loop model loaded alongside it without VRAM pressure.
# ---------------------------------------------------------------------------
phase2_granite_eval() {
    echo "===== PHASE 2 — task=user=granite-4.1-8b =====" | tee -a "$MASTER"
    lms_unload "qwen/qwen3.6-35b-a3b"   # free VRAM for loop models
    lms_load "granite-4.1-8b" || return 1

    # loop=qwen36 — load alongside granite
    lms_load "qwen/qwen3.6-35b-a3b" || true
    run_with_fallback 2 "qwen/qwen3.6-35b-a3b" \
        "openai/granite-4.1-8b" "openai/granite-4.1-8b" \
        lms-openai lms-anthropic
    lms_unload "qwen/qwen3.6-35b-a3b"

    # loop=gemma4-26b-a4b
    lms_load "google/gemma-4-26b-a4b" || true
    run_with_fallback 2 "google/gemma-4-26b-a4b" \
        "openai/granite-4.1-8b" "openai/granite-4.1-8b" \
        lms-openai lms-anthropic
    lms_unload "google/gemma-4-26b-a4b"

    # loop=gemma4:26b on Ollama
    ollama_warm "gemma4:26b"
    run_with_fallback 2 "gemma4:26b" \
        "openai/granite-4.1-8b" "openai/granite-4.1-8b" \
        ollama-openai ollama
}

# ---------------------------------------------------------------------------
# PHASE 3 — broader LMS sweep (cloud Sonnet/Haiku evaluator)
#
# Run after phases 1+2 narrow down which loop models are worth deeper
# eval. Cloud evaluator gives val_score directly comparable to all
# prior τ³ runs. Costs cloud $.
# ---------------------------------------------------------------------------
phase3_full_lms_sweep() {
    echo "===== PHASE 3 — full LMS sweep, cloud Sonnet/Haiku evaluator =====" | tee -a "$MASTER"
    # Models with prior failures (annotated in compat matrix) — retest with
    # new harness in case the failure mode was harness-side.
    run_with_fallback 3 "qwen/qwen3-30b-a3b-2507"          "" "" lms-openai
    run_with_fallback 3 "google/gemma-4-31b"               "" "" lms-openai
    run_with_fallback 3 "unsloth/gemma-4-26b-a4b-it"       "" "" lms-openai
    run_with_fallback 3 "qwen/qwen3-32b"                   "" "" lms-openai
    run_with_fallback 3 "qwen/qwen3-coder-30b"             "" "" lms-openai
    run_with_fallback 3 "qwen2.5-coder-32b-instruct"       "" "" lms-openai
    run_with_fallback 3 "mistralai/devstral-small-2-2512"  "" "" lms-openai
    run_with_fallback 3 "mistralai/ministral-3-14b-reasoning" "" "" lms-openai
    run_with_fallback 3 "zai-org/glm-4.7-flash"            "" "" lms-openai
    # Extra Ollama candidates worth a smoke (per testplan § Pending)
    run_with_fallback 3 "qwen3-coder:30b"                  "" "" ollama-openai
    # run_with_fallback 3 "Qwq:32b"                          "" "" ollama-openai
    # run_with_fallback 3 "gpt-oss:120b"                     "" "" ollama-openai
}

# Phase 3 entries pass empty task/user → run_one defaults to cloud
# Sonnet/Haiku. Re-export ANTHROPIC_API_KEY (already loaded above).

# ---------------------------------------------------------------------------
# Driver — run phases listed in $PHASES (default "1,2"). Phase 3 by opt-in:
#   OWNEVO_TAU3_PHASES=1,2,3 bash apps/kernel/scripts/tau3_p2_local_sweep.sh
# ---------------------------------------------------------------------------
IFS=',' read -ra _phase_list <<< "$PHASES"
for _p in "${_phase_list[@]}"; do
    case "$_p" in
        1) phase1_qwen36_eval   ;;
        2) phase2_granite_eval  ;;
        3) phase3_full_lms_sweep ;;
        *) echo "warn: unknown phase '$_p' — skipping" | tee -a "$MASTER" ;;
    esac
done

echo "=== sweep complete ===" | tee -a "$MASTER"
column -t -s $'\t' "$RESULTS" | tee -a "$MASTER"
