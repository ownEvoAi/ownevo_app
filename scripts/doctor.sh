#!/usr/bin/env bash
# Preflight checks before deploying.
#
# Verifies tool versions, .env contents, fly auth, and Anthropic key shape.
# Exits 0 when all checks pass, non-zero with a summary on first failure
# class (deploy, dev, or env).
#
# Usage:
#   ./scripts/doctor.sh           # all checks
#   ./scripts/doctor.sh --dev     # just dev-stack checks (no flyctl)
#   ./scripts/doctor.sh --deploy  # just deploy-track checks
#
# Exit codes:
#   0  all checks passed
#   1  one or more checks failed (see output for details)

set -uo pipefail

# --------------------------------------------------------------------------
# Pretty printing
# --------------------------------------------------------------------------

if [ -t 1 ]; then
  C_OK="\033[32m"; C_WARN="\033[33m"; C_ERR="\033[31m"; C_DIM="\033[2m"; C_RESET="\033[0m"
else
  C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_RESET=""
fi

PASS=0
FAIL=0
WARN=0

ok()   { printf "%b ✓ %s\n" "$C_OK$C_RESET" "$1";   PASS=$((PASS+1)); }
warn() { printf "%b ! %s\n" "$C_WARN$C_RESET" "$1"; WARN=$((WARN+1)); }
err()  { printf "%b ✗ %s\n" "$C_ERR$C_RESET" "$1";  FAIL=$((FAIL+1)); }
step() { printf "\n%b── %s ──%b\n" "$C_DIM" "$1" "$C_RESET"; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODE="all"
case "${1:-}" in
  --dev)    MODE=dev ;;
  --deploy) MODE=deploy ;;
  --all|"") MODE=all ;;
  *) printf "usage: doctor.sh [--dev|--deploy|--all]\n"; exit 2 ;;
esac

# --------------------------------------------------------------------------
# Tool versions
# --------------------------------------------------------------------------

step "Tools"

if command -v uv >/dev/null 2>&1; then
  ok "uv $(uv --version | awk '{print $2}')"
else
  err "uv not installed — run ./scripts/setup.sh"
fi

if command -v node >/dev/null 2>&1; then
  node_major=$(node --version | sed 's/^v//' | cut -d. -f1)
  if [ "$node_major" -ge 20 ]; then
    ok "node $(node --version)"
  else
    warn "node $(node --version) is older than 20.x — upgrade recommended"
  fi
else
  err "node not installed — required for the web app"
fi

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    ok "docker $(docker --version | awk '{print $3}' | tr -d ,) — daemon reachable"
  else
    err "docker CLI present but daemon not reachable — start Docker Desktop"
  fi
else
  warn "docker not installed — required for 'make dev-up' and sandboxed execution"
fi

if [ "$MODE" = "deploy" ] || [ "$MODE" = "all" ]; then
  if command -v flyctl >/dev/null 2>&1; then
    ok "flyctl $(flyctl version 2>&1 | head -1 | awk '{print $2}')"
  else
    err "flyctl not installed — required for Fly.io deploy (brew install flyctl)"
  fi
fi

# --------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------

step ".env"

if [ ! -f .env ]; then
  err ".env not found — copy from .env.example and edit"
else
  ok ".env present"

  if grep -qE '^ANTHROPIC_API_KEY=["'"'"']?sk-ant-' .env; then
    ok "ANTHROPIC_API_KEY looks like an Anthropic key (sk-ant-…)"
  elif grep -qE '^ANTHROPIC_API_KEY=.+' .env; then
    warn "ANTHROPIC_API_KEY set but doesn't start with 'sk-ant-' — verify it's a real Anthropic key"
  else
    err "ANTHROPIC_API_KEY not set in .env"
  fi

  if grep -qE '^OWNEVO_DATABASE_URL=postgres' .env; then
    ok "OWNEVO_DATABASE_URL set"
  else
    warn "OWNEVO_DATABASE_URL not set in .env (docker compose sets it automatically; only needed for bare-metal dev)"
  fi
fi

# --------------------------------------------------------------------------
# Fly auth
# --------------------------------------------------------------------------

if [ "$MODE" = "deploy" ] || [ "$MODE" = "all" ]; then
  step "Fly.io auth"

  if command -v flyctl >/dev/null 2>&1; then
    if fly_user=$(flyctl auth whoami 2>/dev/null); then
      ok "fly auth: $fly_user"
    else
      err "not logged in to Fly — run 'flyctl auth login'"
    fi
  fi
fi

# --------------------------------------------------------------------------
# Sandbox image (warn, not error — only matters for real iterations)
# --------------------------------------------------------------------------

if [ "$MODE" = "dev" ] || [ "$MODE" = "all" ]; then
  step "Sandbox image"

  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    if docker image inspect ownevo-sandbox-m5:0.1.0 >/dev/null 2>&1; then
      ok "ownevo-sandbox-m5:0.1.0 built"
    else
      warn "ownevo-sandbox-m5:0.1.0 not built — run 'make sandbox-image-m5' before triggering M5 iterations"
    fi
  fi
fi

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

step "Summary"
printf "  %b%d passed%b · %b%d warnings%b · %b%d failed%b\n" \
  "$C_OK" "$PASS" "$C_RESET" \
  "$C_WARN" "$WARN" "$C_RESET" \
  "$C_ERR" "$FAIL" "$C_RESET"

if [ "$FAIL" -gt 0 ]; then
  printf "\n%bdoctor: not ready — address the ✗ items above%b\n" "$C_ERR" "$C_RESET"
  exit 1
fi

if [ "$WARN" -gt 0 ]; then
  printf "\n%bdoctor: ready, but review the ! warnings%b\n" "$C_WARN" "$C_RESET"
else
  printf "\n%bdoctor: all systems go%b\n" "$C_OK" "$C_RESET"
fi
exit 0
