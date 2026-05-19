FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

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
# Bind to `::` (IPv6 wildcard, dual-stacks IPv4 on Linux) so the kernel
# is reachable on Fly's internal 6PN network. Fly's `<app>.internal` DNS
# returns IPv6-only `fdaa:…` addresses; `--host 0.0.0.0` (IPv4-only)
# means web→kernel internal connections get ECONNREFUSED. Public traffic
# through Fly's edge proxy works either way because the proxy forwards
# to 127.0.0.1.
CMD ["uv", "run", "--package", "ownevo-kernel", "--extra", "api", "--extra", "agent", \
     "uvicorn", "ownevo_kernel.api.app:app", \
     "--host", "::", "--port", "8000", "--workers", "1"]
