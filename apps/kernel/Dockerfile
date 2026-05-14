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
CMD ["uv", "run", "--package", "ownevo-kernel", "--extra", "api", "--extra", "agent", \
     "uvicorn", "ownevo_kernel.api.app:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
