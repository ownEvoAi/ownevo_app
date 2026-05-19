#!/usr/bin/env bash
# One-shot fresh-machine setup for ownEvo.
#
# Idempotent — safe to re-run. Detects already-installed dependencies
# and only acts on what's missing. Exits non-zero with a clear message
# on first failure.
#
# Targets macOS + Linux. Windows users should run inside WSL2.

set -euo pipefail

# --------------------------------------------------------------------------
# Pretty printing
# --------------------------------------------------------------------------

if [ -t 1 ]; then
  C_OK="\033[32m"; C_WARN="\033[33m"; C_ERR="\033[31m"; C_DIM="\033[2m"; C_RESET="\033[0m"
else
  C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_RESET=""
fi

say()  { printf "%b\n" "$1"; }
ok()   { say "${C_OK}✓${C_RESET} $1"; }
warn() { say "${C_WARN}!${C_RESET} $1"; }
err()  { say "${C_ERR}✗${C_RESET} $1"; }
step() { say "\n${C_DIM}── $1 ──${C_RESET}"; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --------------------------------------------------------------------------
# Detect platform
# --------------------------------------------------------------------------

case "$(uname -s)" in
  Darwin) PLATFORM=mac ;;
  Linux)  PLATFORM=linux ;;
  *) err "Unsupported platform: $(uname -s). Use macOS or Linux (WSL2 on Windows)."; exit 2 ;;
esac

step "ownEvo setup — $PLATFORM"

# --------------------------------------------------------------------------
# brew (mac only, used to install everything else)
# --------------------------------------------------------------------------

if [ "$PLATFORM" = "mac" ]; then
  if command -v brew >/dev/null 2>&1; then
    ok "brew $(brew --version | head -1 | awk '{print $2}')"
  else
    err "brew not installed. Install from https://brew.sh, then re-run this script."
    exit 2
  fi
fi

# --------------------------------------------------------------------------
# uv (Python package manager)
# --------------------------------------------------------------------------

step "uv (Python package manager)"
if command -v uv >/dev/null 2>&1; then
  ok "uv $(uv --version | awk '{print $2}')"
else
  warn "uv not found — installing via the official installer"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  if ! command -v uv >/dev/null 2>&1; then
    # Common path on a fresh install before shell restart
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
  fi
  command -v uv >/dev/null 2>&1 || { err "uv install failed — check https://docs.astral.sh/uv/getting-started/installation/"; exit 2; }
  ok "uv installed"
fi

# --------------------------------------------------------------------------
# Node + npm (for the web app)
# --------------------------------------------------------------------------

step "Node (web app)"
if command -v node >/dev/null 2>&1; then
  node_major=$(node --version | sed 's/^v//' | cut -d. -f1)
  if [ "$node_major" -ge 20 ]; then
    ok "node $(node --version)"
  else
    warn "node $(node --version) is older than 20.x — upgrade recommended"
  fi
else
  if [ "$PLATFORM" = "mac" ]; then
    warn "node not found — installing via brew"
    brew install node
    ok "node $(node --version)"
  else
    err "node not found. Install Node 20+ from https://nodejs.org or your distro's package manager."
    exit 2
  fi
fi

# --------------------------------------------------------------------------
# Docker (for the local stack + sandbox image)
# --------------------------------------------------------------------------

step "Docker"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  ok "docker $(docker --version | awk '{print $3}' | tr -d ,)"
else
  if command -v docker >/dev/null 2>&1; then
    warn "docker CLI present but daemon not reachable — start Docker Desktop, then re-run"
  else
    warn "docker not installed — install Docker Desktop from https://www.docker.com/products/docker-desktop/"
  fi
  warn "Docker is required for 'make dev-up' and sandboxed code execution. Continuing anyway."
fi

# --------------------------------------------------------------------------
# flyctl (optional — only needed for production deploys)
# --------------------------------------------------------------------------

step "flyctl (optional — for Fly.io deploys)"
if command -v flyctl >/dev/null 2>&1; then
  ok "flyctl $(flyctl version 2>&1 | head -1 | awk '{print $2}')"
else
  if [ "$PLATFORM" = "mac" ]; then
    warn "flyctl not installed — run 'brew install flyctl' if you plan to deploy"
  else
    warn "flyctl not installed — install from https://fly.io/docs/flyctl/install/ if you plan to deploy"
  fi
fi

# --------------------------------------------------------------------------
# uv sync — Python deps for the workspace
# --------------------------------------------------------------------------

step "Python deps (uv sync)"
uv sync --quiet
ok "uv workspace synced"

# --------------------------------------------------------------------------
# npm install — web app deps
# --------------------------------------------------------------------------

step "Web app deps (npm install)"
(cd apps/web && npm install --silent)
ok "apps/web node_modules installed"

# --------------------------------------------------------------------------
# .env bootstrap
# --------------------------------------------------------------------------

step ".env"
if [ -f .env ]; then
  ok ".env already exists — leaving it alone"
else
  cp .env.example .env
  ok "copied .env.example → .env (edit it to add ANTHROPIC_API_KEY)"
fi

# --------------------------------------------------------------------------
# Next steps
# --------------------------------------------------------------------------

step "Next steps"
cat <<'EOF'

  1. Add your Anthropic API key:
       echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env

  2. Bring up the local stack:
       make dev-up

  3. Seed demo data (~3 min, costs ~$0.30 in Anthropic credits):
       make seed-demo-with-iter

  4. Open the workspace:
       http://localhost:3000/workspaces/acme

  Or for a Fly.io deploy:
       make doctor          # preflight check
       make fly-bootstrap   # first-time setup
       make fly-deploy-kernel && make fly-deploy-web

  Help:  make help
EOF
