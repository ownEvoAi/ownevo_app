#!/usr/bin/env bash
# Post-deploy smoke test for the kernel API.
#
# Fast (<10s), no LLM calls. Verifies the substrate is alive after a
# deploy or local stack bring-up. Designed to be safe to run against the
# DEMO_MODE=true production demo — every endpoint hit is a read.
#
# Usage:
#   ./scripts/smoke.sh                              # default: localhost:8000
#   ./scripts/smoke.sh https://demo.ownevo.ai        # against a remote URL
#   ./scripts/smoke.sh --web https://demo.ownevo.ai # also smoke the web app
#
# Exit codes:
#   0  every check passed
#   1  one or more checks failed

set -uo pipefail

if [ -t 1 ]; then
  C_OK="\033[32m"; C_ERR="\033[31m"; C_DIM="\033[2m"; C_RESET="\033[0m"
else
  C_OK=""; C_ERR=""; C_DIM=""; C_RESET=""
fi

PASS=0
FAIL=0

ok()   { printf "%b ✓ %s%b\n" "$C_OK" "$1" "$C_RESET";   PASS=$((PASS+1)); }
err()  { printf "%b ✗ %s%b\n" "$C_ERR" "$1" "$C_RESET";  FAIL=$((FAIL+1)); }
note() { printf "%b   %s%b\n" "$C_DIM" "$1" "$C_RESET"; }

# --------------------------------------------------------------------------
# Parse args
# --------------------------------------------------------------------------

API_URL=""
WEB_URL=""

while [ $# -gt 0 ]; do
  case "$1" in
    --web)   shift; WEB_URL="${1:-}"; shift || true ;;
    --help|-h) printf "usage: smoke.sh [API_URL] [--web WEB_URL]\n"; exit 0 ;;
    *) API_URL="$1"; shift ;;
  esac
done

API_URL="${API_URL:-http://localhost:8000}"
API_URL="${API_URL%/}"

printf "%b── Smoke test: %s ──%b\n\n" "$C_DIM" "$API_URL" "$C_RESET"

# --------------------------------------------------------------------------
# Helper: curl wrapper with timeout
# --------------------------------------------------------------------------

fetch() {
  # $1 = URL  $2 = description
  local url="$1" desc="$2"
  local response status body
  response=$(curl -fsS --max-time 8 -w "\n%{http_code}" "$url" 2>&1) || {
    err "$desc — request failed"
    note "$response"
    return 1
  }
  status=$(printf "%s" "$response" | tail -1)
  body=$(printf "%s" "$response" | sed '$d')
  if [ "$status" = "200" ]; then
    SMOKE_BODY="$body"
    return 0
  fi
  err "$desc — HTTP $status"
  note "$(printf "%s" "$body" | head -3)"
  return 1
}

# --------------------------------------------------------------------------
# Kernel health
# --------------------------------------------------------------------------

if fetch "$API_URL/api/health" "GET /api/health"; then
  if printf "%s" "$SMOKE_BODY" | grep -qE '"status"\s*:\s*"ok"'; then
    ok "kernel health: status=ok"
  else
    err "kernel health endpoint returned 200 but status != ok"
    note "$SMOKE_BODY"
  fi
  if printf "%s" "$SMOKE_BODY" | grep -qE '"db"\s*:\s*"ok"'; then
    ok "kernel health: db=ok"
  else
    err "kernel health: db not ok"
    note "$SMOKE_BODY"
  fi
fi

# --------------------------------------------------------------------------
# Workflows endpoint — must return at least one row on seeded data
# --------------------------------------------------------------------------

if fetch "$API_URL/api/workflows" "GET /api/workflows"; then
  if printf "%s" "$SMOKE_BODY" | grep -qE '"id"'; then
    ok "workflows endpoint returns at least one row"
  else
    err "workflows endpoint returned no rows — did seed-demo-with-iter run?"
    note "$SMOKE_BODY"
  fi
fi

# --------------------------------------------------------------------------
# Audit list endpoint — proves audit_entries table is wired
# --------------------------------------------------------------------------

if fetch "$API_URL/api/audit?limit=1" "GET /api/audit?limit=1"; then
  ok "audit list endpoint responds (audit_entries table reachable)"
fi

# --------------------------------------------------------------------------
# Web app (optional)
# --------------------------------------------------------------------------

if [ -n "$WEB_URL" ]; then
  WEB_URL="${WEB_URL%/}"
  printf "\n%b── Web app: %s ──%b\n\n" "$C_DIM" "$WEB_URL" "$C_RESET"
  if fetch "$WEB_URL/workspaces/acme" "GET /workspaces/acme"; then
    if printf "%s" "$SMOKE_BODY" | grep -qi 'ownevo'; then
      ok "web app responds and references ownEvo"
    else
      err "web app responded but content looks wrong"
    fi
  fi
fi

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

printf "\n%b── Summary ──%b\n" "$C_DIM" "$C_RESET"
printf "  %b%d passed%b · %b%d failed%b\n" "$C_OK" "$PASS" "$C_RESET" "$C_ERR" "$FAIL" "$C_RESET"

if [ "$FAIL" -gt 0 ]; then
  printf "\n%bsmoke: failed%b\n" "$C_ERR" "$C_RESET"
  exit 1
fi
printf "\n%bsmoke: all good%b\n" "$C_OK" "$C_RESET"
exit 0
