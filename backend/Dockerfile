# Skeptic backend — Railway deploy (BUILD-PLAN M6).
# uv-based build; runs uvicorn on Railway's injected $PORT.

FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    # cap glibc per-thread arenas: with the default (8 × nCPU) the sweep's
    # freed numpy/pandas buffers linger unreturned and RSS ratchets on a
    # small container. 2 arenas keeps allocator retention bounded.
    MALLOC_ARENA_MAX=2

WORKDIR /app

# dependency layer (cached until the lock changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app

# runtime env expected from the platform:
#   R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET
#   OPENROUTER_API_KEY (parser + verdict narration + grounded Q&A)
#   SKEPTIC_ACCESS_TOKEN (bearer auth; unset = open, dev only)
#   DATABASE_URL (Neon; omit to run on ephemeral local SQLite)
EXPOSE 8000
CMD ["sh", "-c", "uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
