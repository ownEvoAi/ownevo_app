FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /usr/local/bin/uv

WORKDIR /app

# --- dependency layer (cached unless uv.lock or pyproject.toml changes) ---
COPY uv.lock pyproject.toml ./
COPY packages/trace-format/pyproject.toml packages/trace-format/pyproject.toml
COPY apps/kernel/pyproject.toml apps/kernel/pyproject.toml
RUN uv sync --package ownevo-kernel --extra api --extra agent --no-dev --frozen --no-install-project

# --- source layer ---
COPY packages/trace-format/ packages/trace-format/
COPY apps/kernel/ apps/kernel/
RUN uv sync --package ownevo-kernel --extra api --extra agent --no-dev --frozen

EXPOSE 8000
# Dual-stack bind via apps/kernel/scripts/boot.py. uvicorn's `--host`
# accepts a single address; on Fly's Linux machines `--host ::` listens
# IPv6-only and `--host 0.0.0.0` listens IPv4-only — neither covers both
# paths we need (Fly Proxy reaches the machine over IPv4; other Fly apps
# reach `<app>.internal` over IPv6). boot.py opens one AF_INET and one
# AF_INET6 socket (V6ONLY=1) and hands both to uvicorn.
CMD ["uv", "run", "--package", "ownevo-kernel", "--extra", "api", "--extra", "agent", \
     "python", "apps/kernel/scripts/boot.py"]
