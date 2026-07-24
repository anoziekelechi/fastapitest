# =====================================================================
# STAGE 1: Builder - Two-step sync for optimal caching
# =====================================================================
FROM ghcr.io/astral-sh/uv:0.5.11-python3.12-bookworm AS builder

# Ensure binaries work across stages and pre-compile bytecode
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# Step 1: Install dependencies ONLY (maximally cached)
COPY pyproject.toml uv.lock ./
COPY README.md ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /app/.venv && \
    . /app/.venv/bin/activate && \
    uv sync --frozen --no-dev --no-install-project

# Step 2: Install project (only rebuilds when source code changes)
COPY alembic.ini ./
COPY alembic ./alembic
COPY api ./api
COPY core ./core

RUN --mount=type=cache,target=/root/.cache/uv \
    . /app/.venv/bin/activate && \
    uv sync --frozen --no-dev

# =====================================================================
# STAGE 2: Runtime - Slim & secure
# =====================================================================
FROM python:3.12-slim-bookworm

WORKDIR /app

# Copy venv with working binaries
COPY --from=builder /app/.venv /app/.venv

# Copy application code (explicit, not /app /app)
COPY --from=builder /app/alembic.ini ./
COPY --from=builder /app/alembic ./alembic
COPY --from=builder /app/api ./api
COPY --from=builder /app/core ./core

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Security: non-root user
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
