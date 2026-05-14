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
#   $2  base url OR preset see Preset shorthands below; or a full http(s):// URL
#   $3  workflow tag       e.g. "qwen36"  (becomes workflow_id tau3-retail-v1__qwen36)
#   $4  api format         openai | ollama | anthropic — optional; presets set this
#
# Optional args (positional):
#   $5  task-agent-model   e.g. "openai/qwen/qwen3.6-35b-a3b" (default: cloud Sonnet)
#   $6  task-user-model    e.g. "openai/qwen/qwen3.6-35b-a3b" (default: cloud Haiku)
#
# Preset shorthands for $2 (host = $OWNEVO_LLM_HOST, default localhost):
#   ollama          → http://$LLM_HOST:11434       api_format=ollama     (Ollama native /api/chat)
#   ollama-openai   → http://$LLM_HOST:11434/v1    api_format=openai     (Ollama OpenAI-compat)
#   lms-openai      → http://$LLM_HOST:1234/v1     api_format=openai     (LMS OpenAI-compat)
#   lms-anthropic   → http://$LLM_HOST:1234        api_format=anthropic  (LMS Anthropic-compat)
#
# Env vars (override defaults):
#   OWNEVO_LLM_HOST     desktop ip (default localhost)
#   OWNEVO_TAU3_LOGDIR  log directory (default <repo>/log/tau3_p2 — survives reboot)
#   OWNEVO_TAU3_CYCLES  number of cycles (default 10)
#   OWNEVO_TAU3_CONCURRENCY  override per-preset default (LMS=4, Ollama=2)
#
# Model-swap hooks (for proposer+task models that can't co-reside in VRAM):
#   OWNEVO_TAU3_SWAP_PROPOSER       LMS id loaded for proposer phase (e.g. "qwen/qwen3-30b-a3b-2507")
#   OWNEVO_TAU3_SWAP_TASK           LMS id loaded for eval phase   (e.g. "qwen/qwen3.6-35b-a3b")
#   OWNEVO_TAU3_SWAP_PROPOSER_CTX   ctx for proposer load (default 32768)
#   OWNEVO_TAU3_SWAP_TASK_CTX       ctx for task load     (default 65536)
#
# When both _SWAP_* vars are set, the wrapper:
#   1. ensures proposer model is loaded at start of cycle
#   2. between proposer and eval phases, runs:
#        lms unload <proposer> && lms load <task> -c <task_ctx>
#   3. after eval, runs:
#        lms unload <task> && lms load <proposer> -c <proposer_ctx>   (for next cycle)
# Use only with `lms-*` presets — Ollama doesn't need manual swap.
#
# Examples — qwen3.6-35b-a3b on LMS desktop (loop only; task agent on cloud):
#   bash scripts/tau3_p2_local_loop.sh \
#     "qwen/qwen3.6-35b-a3b" lms-openai "qwen36"
#
# Examples — gemma4:26b on Ollama native API:
#   bash scripts/tau3_p2_local_loop.sh \
#     "gemma4:26b" ollama "gemma4_26b"
#
# Examples — qwen3.6-35b-a3b on LMS Anthropic API, all 3 LLMs local:
#   bash scripts/tau3_p2_local_loop.sh \
#     "qwen/qwen3.6-35b-a3b" lms-anthropic "qwen36_lms_ant" "" \
#     "anthropic/qwen/qwen3.6-35b-a3b" "anthropic/qwen/qwen3.6-35b-a3b"
set -u

if [[ $# -lt 3 ]]; then
    echo "usage: $0 <model> <base_url|preset> <workflow_tag> [api_format] [task-agent-model] [task-user-model]" >&2
    echo "  presets: ollama | ollama-openai | lms-openai | lms-anthropic" >&2
    echo "example (lms openai loop, cloud task):" >&2
    echo "    $0 'qwen/qwen3.6-35b-a3b' lms-openai 'qwen36'" >&2
    echo "example (ollama native loop):" >&2
    echo "    $0 'gemma4:26b' ollama 'gemma4_26b'" >&2
    echo "example (lms anthropic, all 3 local):" >&2
    echo "    $0 'qwen/qwen3.6-35b-a3b' lms-anthropic 'qwen36_full' '' \\" >&2
    echo "      'anthropic/qwen/qwen3.6-35b-a3b' 'anthropic/qwen/qwen3.6-35b-a3b'" >&2
    exit 2
fi

LLM_HOST="${OWNEVO_LLM_HOST:-localhost}"

MODEL="$1"
BASE_URL_OR_PRESET="$2"
WORKFLOW_TAG="$3"
API_FORMAT_ARG="${4:-}"
TASK_AGENT_MODEL="${5:-anthropic/claude-sonnet-4-6}"
TASK_USER_MODEL="${6:-anthropic/claude-haiku-4-5-20251001}"

# Resolve $2 → BASE_URL + preset-implied API_FORMAT.
case "$BASE_URL_OR_PRESET" in
    ollama)         BASE_URL="http://${LLM_HOST}:11434"     ; PRESET_FMT="ollama"    ;;
    ollama-openai)  BASE_URL="http://${LLM_HOST}:11434/v1"  ; PRESET_FMT="openai"    ;;
    lms-openai)     BASE_URL="http://${LLM_HOST}:1234/v1"   ; PRESET_FMT="openai"    ;;
    lms-anthropic)  BASE_URL="http://${LLM_HOST}:1234"      ; PRESET_FMT="anthropic" ;;
    http://*|https://*) BASE_URL="$BASE_URL_OR_PRESET"      ; PRESET_FMT=""          ;;
    *) echo "error: \$2 must be a URL or one of: ollama|ollama-openai|lms-openai|lms-anthropic (got '$BASE_URL_OR_PRESET')" >&2 ; exit 2 ;;
esac

# $4 wins if explicitly passed, else preset, else default openai.
API_FORMAT="${API_FORMAT_ARG:-${PRESET_FMT:-openai}}"

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
    # .env may use ANTHROPIC_API_KEY or OWNEVO_LLM_API_KEY (same key, different name)
    # .env lines may have optional "export " prefix; key may be ANTHROPIC_API_KEY or OWNEVO_LLM_API_KEY
    AKEY=$(grep -E '^(export )?ANTHROPIC_API_KEY=' "$DOTENV" | head -1 | sed 's/^export //' | cut -d= -f2- | tr -d '"'"'")
    [[ -z "$AKEY" ]] && AKEY=$(grep -E '^(export )?OWNEVO_LLM_API_KEY=' "$DOTENV" | head -1 | sed 's/^export //' | cut -d= -f2- | tr -d '"'"'")
    [[ -n "$AKEY" ]] && export ANTHROPIC_API_KEY="$AKEY"
fi

# Task agent / user simulator routing. tau2 inside the sandbox uses LiteLLM,
# which reads OPENAI_API_BASE / OLLAMA_API_BASE / ANTHROPIC_API_BASE +
# matching keys to route `openai/...`, `ollama_chat/...`, `anthropic/...`
# model ids. Without these, an `openai/<local-model>` is silently sent to
# api.openai.com. Auto-export based on task-model prefixes; caller env
# always wins.
if [[ "$TASK_AGENT_MODEL" == openai/* || "$TASK_USER_MODEL" == openai/* ]]; then
    export OPENAI_API_BASE="${OPENAI_API_BASE:-$BASE_URL}"
    export OPENAI_API_KEY="${OPENAI_API_KEY:-lm-studio}"
fi
if [[ "$TASK_AGENT_MODEL" == ollama_chat/* || "$TASK_AGENT_MODEL" == ollama/* \
   || "$TASK_USER_MODEL" == ollama_chat/* || "$TASK_USER_MODEL" == ollama/* ]]; then
    # OLLAMA_API_BASE must point at the Ollama daemon host root (no /v1 suffix).
    # Default to Ollama native port on LLM_HOST, NOT BASE_URL — when the proposer
    # runs on LMS (lms-openai/lms-anthropic, port 1234), BASE_URL is the LMS
    # address and would route ollama_chat/ calls to the wrong backend.
    export OLLAMA_API_BASE="${OLLAMA_API_BASE:-http://${LLM_HOST}:11434}"
fi
# anthropic/<model> on a non-cloud base_url ⇒ LMS Anthropic-compat at
# /v1/messages. The Anthropic SDK appends /v1/messages itself, so the
# base must be the LMS root (no /v1 suffix). Pin to LMS root regardless
# of the loop's base_url — when the loop is on Ollama, the task agent
# still needs LMS for anthropic-compat; when the loop is on lms-openai
# (base ends in /v1) we'd otherwise build the wrong URL.
if [[ "$TASK_AGENT_MODEL" == anthropic/* || "$TASK_USER_MODEL" == anthropic/* ]]; then
    case "$BASE_URL" in
        *api.anthropic.com*) : ;;  # cloud loop → cloud task agent, no override
        *) export ANTHROPIC_API_BASE="${ANTHROPIC_API_BASE:-http://${LLM_HOST}:1234}" ;;
    esac
fi

# Persist logs at project root so they survive reboot. /tmp gets wiped.
REPO_ROOT=$(cd "$KERNEL_DIR/../.." && pwd)
LOGDIR="${OWNEVO_TAU3_LOGDIR:-$REPO_ROOT/log/tau3_p2}"
mkdir -p "$LOGDIR"

N_CYCLES="${OWNEVO_TAU3_CYCLES:-10}"
# Whole-sandbox-run budget for all 40 retail tasks combined. Default 2400s
# (40 min) is sized for cloud Sonnet; slow local backends (Ollama qwen3.6:35b
# at NUM_PARALLEL=2) need 1.5-3 hr. Override with OWNEVO_TAU3_TASK_TIMEOUT.
TASK_TIMEOUT="${OWNEVO_TAU3_TASK_TIMEOUT:-2400}"
# tau2 eval concurrency. LMS handles 4 well; Ollama is throughput-bound, keep at 2.
case "$BASE_URL_OR_PRESET" in
    lms-*)              CONCURRENCY_DEFAULT=4 ;;
    ollama|ollama-*)    CONCURRENCY_DEFAULT=2 ;;
    *)                  CONCURRENCY_DEFAULT=3 ;;
esac
CONCURRENCY="${OWNEVO_TAU3_CONCURRENCY:-$CONCURRENCY_DEFAULT}"
WORKFLOW_ID="tau3-retail-v1__${WORKFLOW_TAG}"
MASTER="$LOGDIR/${WORKFLOW_TAG}_p2_master.log"

# ── Model-swap mode (set both _SWAP_PROPOSER and _SWAP_TASK to enable) ──
SWAP_PROPOSER="${OWNEVO_TAU3_SWAP_PROPOSER:-}"
SWAP_TASK="${OWNEVO_TAU3_SWAP_TASK:-}"
SWAP_PROPOSER_CTX="${OWNEVO_TAU3_SWAP_PROPOSER_CTX:-32768}"
SWAP_TASK_CTX="${OWNEVO_TAU3_SWAP_TASK_CTX:-65536}"
if [[ -n "$SWAP_PROPOSER" && -n "$SWAP_TASK" ]]; then
    SWAP_MODE=1
    # Pre-cycle: make sure proposer is the loaded model.
    echo "swap-mode: loading proposer '$SWAP_PROPOSER' (ctx=$SWAP_PROPOSER_CTX)" | tee -a "$MASTER"
    lms unload "$SWAP_TASK" 2>/dev/null || true
    lms load "$SWAP_PROPOSER" --context-length "$SWAP_PROPOSER_CTX"
    # Hooks consumed by run_tau3_loop.py at phase boundaries.
    export OWNEVO_TAU3_AFTER_PROPOSER_CMD="lms unload '$SWAP_PROPOSER' && lms load '$SWAP_TASK' --context-length $SWAP_TASK_CTX"
    export OWNEVO_TAU3_AFTER_EVAL_CMD="lms unload '$SWAP_TASK' && lms load '$SWAP_PROPOSER' --context-length $SWAP_PROPOSER_CTX"
else
    SWAP_MODE=0
fi

for i in $(seq 1 "$N_CYCLES"); do
    ts=$(date -u +%FT%TZ)
    log="$LOGDIR/${WORKFLOW_TAG}_p2_cycle${i}.log"
    echo "=== [$ts] $WORKFLOW_TAG P2 cycle $i/$N_CYCLES ===" | tee -a "$MASTER"

    uv run --extra agent python scripts/run_tau3_loop.py \
        --workflow-id "$WORKFLOW_ID" \
        --api-format "$API_FORMAT" \
        --llm-base-url "$BASE_URL" \
        --llm-model "$MODEL" \
        --task-agent-model "$TASK_AGENT_MODEL" \
        --task-user-model "$TASK_USER_MODEL" \
        --task-concurrency "$CONCURRENCY" \
        --task-timeout-seconds "$TASK_TIMEOUT" \
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
