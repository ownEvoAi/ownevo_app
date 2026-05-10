#!/usr/bin/env bash
# One-shot: wait for the running sweep (PID = $1, default 26664) to
# exit, then re-run P1.1 (loop=qwen3.6-35b-a3b) and P1.2 (loop=gemma-
# 4-26b-a4b) with the qwen36-jinja fix in place. Task/user use
# anthropic/qwen/qwen3.6-35b-a3b → LMS /v1/messages instead of the
# broken /v1/chat/completions jinja path.
#
# P1.3 (loop=gemma4:26b on Ollama) is intentionally NOT included here —
# its failure mode was httpx.ReadTimeout on the native /api/chat path,
# which is a separate issue from the jinja bug.
#
# Usage:
#   nohup bash apps/kernel/scripts/tau3_p1_rerun_after_sweep.sh [parent_pid] \
#     > log/tau3_p2/sweep_p1_rerun_nohup.log 2>&1 &
set -u

WAIT_PID="${1:-26664}"
REPO_ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
cd "$REPO_ROOT"

LOGDIR="${OWNEVO_TAU3_LOGDIR:-$REPO_ROOT/log/tau3_p2}"
mkdir -p "$LOGDIR"
MASTER="$LOGDIR/p1_rerun_master.log"

echo "=== [$(date -u +%FT%TZ)] waiting for sweep parent PID=$WAIT_PID to exit ===" \
    | tee -a "$MASTER"
while kill -0 "$WAIT_PID" 2>/dev/null; do sleep 30; done
echo "=== [$(date -u +%FT%TZ)] sweep parent gone — starting P1 reruns ===" \
    | tee -a "$MASTER"

# Brief pause for any LMS unload/cleanup the sweep finalized
sleep 5

# Ensure qwen3.6-35b is loaded in LMS for both runs (loop=qwen36 needs
# it; loop=gemma also needs it because task agent is anthropic/qwen36).
LMS_BIN="${LMS_BIN:-lms}"
"$LMS_BIN" load qwen/qwen3.6-35b-a3b 2>&1 | tee -a "$MASTER" || {
    echo "lms load qwen36 failed — abort" | tee -a "$MASTER"; exit 1; }

# P1.1 — loop=qwen3.6-35b-a3b on lms-openai, task=anthropic/qwen36
echo "=== [$(date -u +%FT%TZ)] P1.1 rerun: loop=qwen3.6-35b-a3b lms-openai ===" \
    | tee -a "$MASTER"
OWNEVO_TAU3_CYCLES="${OWNEVO_TAU3_CYCLES:-5}" \
bash apps/kernel/scripts/tau3_p2_local_loop.sh \
    "qwen/qwen3.6-35b-a3b" lms-openai "p1_rerun__qwen36__qwen36ant" "" \
    "anthropic/qwen/qwen3.6-35b-a3b" "anthropic/qwen/qwen3.6-35b-a3b" \
    2>&1 | tee -a "$MASTER"

# P1.2 — loop=gemma-4-26b-a4b on lms-openai, task=anthropic/qwen36
# Load gemma alongside qwen36 (~22 + 18 = 40 GB on 48 GB total).
"$LMS_BIN" load google/gemma-4-26b-a4b 2>&1 | tee -a "$MASTER" || {
    echo "lms load gemma-4-26b-a4b failed — skipping P1.2" | tee -a "$MASTER"; exit 0; }

echo "=== [$(date -u +%FT%TZ)] P1.2 rerun: loop=gemma-4-26b-a4b lms-openai ===" \
    | tee -a "$MASTER"
OWNEVO_TAU3_CYCLES="${OWNEVO_TAU3_CYCLES:-5}" \
bash apps/kernel/scripts/tau3_p2_local_loop.sh \
    "google/gemma-4-26b-a4b" lms-openai "p1_rerun__gemma426ba4b__qwen36ant" "" \
    "anthropic/qwen/qwen3.6-35b-a3b" "anthropic/qwen/qwen3.6-35b-a3b" \
    2>&1 | tee -a "$MASTER"

echo "=== [$(date -u +%FT%TZ)] P1 reruns complete ===" | tee -a "$MASTER"
