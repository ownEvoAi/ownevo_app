#!/usr/bin/env bash
# One-shot first-time Fly.io deploy.
#
# Walks the docs/runbooks/fly-deploy.md steps interactively:
#   1.  Provision Fly Postgres (`ownevo-pg`)
#   2.  Enable pgvector
#   3.  Create kernel + web apps (`ownevo-kernel`, `ownevo-web`)
#   4.  Set kernel secrets (ANTHROPIC_API_KEY, CORS allowlist)
#   5.  Attach Postgres to kernel + rename DATABASE_URL → OWNEVO_DATABASE_URL
#   6.  Deploy kernel (migrations run via release_command)
#   7.  Create web app + set secrets
#   8.  Deploy web
#   9.  Seed demo data (optional)
#  10.  Smoke test
#  11.  Print custom-domain instructions
#
# Idempotent — checks each resource before creating. Safe to re-run
# after a partial failure.
#
# Usage:
#   ./scripts/fly_bootstrap.sh            # interactive
#   ./scripts/fly_bootstrap.sh --no-seed  # skip the seed step
#   ./scripts/fly_bootstrap.sh --dry-run  # print what would happen, do nothing

set -uo pipefail

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
run()  { if [ "$DRY_RUN" = "1" ]; then say "${C_DIM}\$ $*${C_RESET}"; else say "${C_DIM}\$ $*${C_RESET}"; "$@"; fi }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --------------------------------------------------------------------------
# Config (env-overridable for non-default deploys)
# --------------------------------------------------------------------------

PG_APP="${PG_APP:-ownevo-pg}"
KERNEL_APP="${KERNEL_APP:-ownevo-kernel}"
WEB_APP="${WEB_APP:-ownevo-web}"
REGION="${REGION:-sjc}"
SEED=1
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --no-seed) SEED=0 ;;
    --dry-run) DRY_RUN=1 ;;
    --help|-h)
      printf "usage: fly_bootstrap.sh [--no-seed] [--dry-run]\n"
      printf "env: PG_APP, KERNEL_APP, WEB_APP, REGION (default: %s, %s, %s, %s)\n" "$PG_APP" "$KERNEL_APP" "$WEB_APP" "$REGION"
      exit 0
      ;;
    *) err "unknown arg: $arg"; exit 2 ;;
  esac
done

# --------------------------------------------------------------------------
# Preflight (delegate to doctor.sh for full check)
# --------------------------------------------------------------------------

step "Preflight"

if ! command -v flyctl >/dev/null 2>&1; then
  err "flyctl not installed — brew install flyctl"
  exit 2
fi

if ! flyctl auth whoami >/dev/null 2>&1; then
  err "not logged in to Fly — run 'flyctl auth login' first"
  exit 2
fi
ok "fly auth: $(flyctl auth whoami 2>&1)"

if [ ! -f .env ]; then
  err ".env not found — run ./scripts/setup.sh first"
  exit 2
fi

ANTHROPIC_KEY=$(grep -E '^ANTHROPIC_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"'"'"'')
if [ -z "$ANTHROPIC_KEY" ] || [[ "$ANTHROPIC_KEY" != sk-ant-* ]]; then
  err "ANTHROPIC_API_KEY in .env doesn't look right (must start with sk-ant-)"
  exit 2
fi
ok "ANTHROPIC_API_KEY found in .env"

if [ "$DRY_RUN" = "1" ]; then
  warn "DRY RUN — printing commands but not executing"
fi

# --------------------------------------------------------------------------
# Step 1 — Postgres
# --------------------------------------------------------------------------

step "Step 1 — Postgres ($PG_APP)"

if flyctl apps list 2>/dev/null | grep -qE "^$PG_APP\b"; then
  ok "Postgres app '$PG_APP' already exists"
else
  warn "Creating Postgres cluster — this takes ~2 min and prints a connection string. SAVE IT."
  run flyctl postgres create \
    --name "$PG_APP" \
    --region "$REGION" \
    --vm-size shared-cpu-1x \
    --volume-size 1 \
    --initial-cluster-size 1
fi

# --------------------------------------------------------------------------
# Step 2 — pgvector
# --------------------------------------------------------------------------

step "Step 2 — Enable pgvector on $PG_APP"

warn "Run this in another terminal if needed:"
say  "${C_DIM}    flyctl postgres connect -a $PG_APP${C_RESET}"
say  "${C_DIM}    > CREATE EXTENSION IF NOT EXISTS vector;${C_RESET}"
say  "${C_DIM}    > \\q${C_RESET}"
say  "Continuing — re-run this script if pgvector wasn't enabled and the kernel deploy fails."

# --------------------------------------------------------------------------
# Step 3 — Kernel app
# --------------------------------------------------------------------------

step "Step 3 — Kernel app ($KERNEL_APP)"

if flyctl apps list 2>/dev/null | grep -qE "^$KERNEL_APP\b"; then
  ok "Kernel app '$KERNEL_APP' already exists"
else
  run flyctl apps create "$KERNEL_APP"
fi

# --------------------------------------------------------------------------
# Step 4 — Kernel secrets
# --------------------------------------------------------------------------

step "Step 4 — Kernel secrets"

# Use stdin import so the API key never appears in the process argument list or terminal.
if [ "$DRY_RUN" = "1" ]; then
  say "${C_DIM}\$ flyctl secrets import -a $KERNEL_APP --stage  (ANTHROPIC_API_KEY=*** OWNEVO_CORS_ORIGINS=...)${C_RESET}"
else
  say "${C_DIM}\$ flyctl secrets import -a $KERNEL_APP --stage  (ANTHROPIC_API_KEY=*** OWNEVO_CORS_ORIGINS=...)${C_RESET}"
  printf 'ANTHROPIC_API_KEY=%s\nOWNEVO_CORS_ORIGINS=https://%s.fly.dev,https://demo.ownevo.ai\n' \
    "$ANTHROPIC_KEY" "$WEB_APP" \
    | flyctl secrets import -a "$KERNEL_APP" --stage
fi
ok "Anthropic key + CORS origins staged on $KERNEL_APP"

# --------------------------------------------------------------------------
# Step 5 — Attach Postgres + rename DATABASE_URL → OWNEVO_DATABASE_URL
# --------------------------------------------------------------------------
# flyctl postgres attach sets DATABASE_URL on the app; the kernel reads
# OWNEVO_DATABASE_URL (db.py:26). We capture the attach output (which prints
# the connection string), then stage it under the name the kernel expects.

step "Step 5 — Attach $PG_APP to $KERNEL_APP"

if flyctl secrets list -a "$KERNEL_APP" 2>/dev/null | grep -qF "OWNEVO_DATABASE_URL"; then
  ok "OWNEVO_DATABASE_URL already set on $KERNEL_APP"
elif flyctl secrets list -a "$KERNEL_APP" 2>/dev/null | grep -qF "DATABASE_URL"; then
  # Partial run: postgres was attached (sets DATABASE_URL) but OWNEVO_DATABASE_URL
  # was never staged. We can't read secret values via flyctl; operator must set manually.
  err "DATABASE_URL found on $KERNEL_APP but OWNEVO_DATABASE_URL is missing."
  say  "Set it with the connection string from 'flyctl postgres create' output:"
  say  "${C_DIM}    flyctl secrets set -a $KERNEL_APP OWNEVO_DATABASE_URL=<postgres://...>${C_RESET}"
  exit 2
else
  if [ "$DRY_RUN" = "1" ]; then
    say "${C_DIM}\$ flyctl postgres attach $PG_APP -a $KERNEL_APP${C_RESET}"
    say "${C_DIM}\$ flyctl secrets import -a $KERNEL_APP --stage  (OWNEVO_DATABASE_URL=***)${C_RESET}"
  else
    say "${C_DIM}\$ flyctl postgres attach $PG_APP -a $KERNEL_APP${C_RESET}"
    attach_out=$(flyctl postgres attach "$PG_APP" -a "$KERNEL_APP" 2>&1)
    printf '%s\n' "$attach_out"
    db_url=$(printf '%s\n' "$attach_out" | grep -oE 'postgres://[^ ]+' | head -1)
    if [ -n "$db_url" ]; then
      say "${C_DIM}\$ flyctl secrets import -a $KERNEL_APP --stage  (OWNEVO_DATABASE_URL=***)${C_RESET}"
      printf 'OWNEVO_DATABASE_URL=%s\n' "$db_url" \
        | flyctl secrets import -a "$KERNEL_APP" --stage
      ok "OWNEVO_DATABASE_URL staged on $KERNEL_APP"
    else
      err "Could not extract connection URL from attach output — set it manually:"
      say  "${C_DIM}    flyctl secrets set -a $KERNEL_APP OWNEVO_DATABASE_URL=<postgres://...>${C_RESET}"
      exit 2
    fi
  fi
fi

# --------------------------------------------------------------------------
# Step 6 — Deploy kernel
# --------------------------------------------------------------------------

step "Step 6 — Deploy kernel (release_command runs migrations)"
run flyctl deploy --config fly.toml --remote-only -a "$KERNEL_APP"

# --------------------------------------------------------------------------
# Step 7 — Web app + secrets
# --------------------------------------------------------------------------

step "Step 7 — Web app ($WEB_APP)"

if flyctl apps list 2>/dev/null | grep -qE "^$WEB_APP\b"; then
  ok "Web app '$WEB_APP' already exists"
else
  run flyctl apps create "$WEB_APP"
fi

run flyctl secrets set -a "$WEB_APP" \
  OWNEVO_KERNEL_API_URL="http://$KERNEL_APP.internal:8000" \
  --stage

# --------------------------------------------------------------------------
# Step 8 — Deploy web
# --------------------------------------------------------------------------

step "Step 8 — Deploy web"
run flyctl deploy --config apps/web/fly.toml --remote-only -a "$WEB_APP"

# --------------------------------------------------------------------------
# Step 9 — Seed (optional, costs ~$0.30)
# --------------------------------------------------------------------------

if [ "$SEED" = "1" ]; then
  step "Step 9 — Seed demo data (~3 min, costs ~\$0.30 in Anthropic credits)"
  run flyctl ssh console -a "$KERNEL_APP" -C \
    "uv run --package ownevo-kernel --extra api --extra agent python apps/kernel/scripts/seed_demo.py --with-iterations"
else
  step "Step 9 — Skipped (--no-seed); run 'make fly-seed' later"
fi

# --------------------------------------------------------------------------
# Step 10 — Smoke + DNS instructions
# --------------------------------------------------------------------------

step "Step 10 — Smoke"
if [ "$DRY_RUN" = "0" ]; then
  ./scripts/smoke.sh "https://$KERNEL_APP.fly.dev" --web "https://$WEB_APP.fly.dev" || true
fi

step "Custom domain (optional)"
cat <<EOF

  1. Point DNS at Fly:
       CNAME demo.ownevo.ai → $WEB_APP.fly.dev

  2. Provision the cert (~5 min for Let's Encrypt to propagate):
       flyctl certs add demo.ownevo.ai -a $WEB_APP

  Live URLs:
       Web:    https://$WEB_APP.fly.dev
       Kernel: https://$KERNEL_APP.fly.dev/api/health

EOF

ok "Bootstrap done."
