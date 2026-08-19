# Skeptic backend — Railway deploy (BUILD-PLAN M6).
# uv-based build; runs uvicorn on Railway's injected $PORT.

FROM python:3.13-slim

# uv comes from PyPI, not from ghcr.io.
#
# This used to be `COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/`, which
# made every build depend on a SECOND registry, and on an unpinned `:latest` tag
# there. When Railway began failing at "building the image" and hanging before it
# failed, a registry it cannot reach was the hypothesis that fit: the same inputs
# built and booted fine on GitHub's amd64 runners from a clean checkout, three
# times, so the Dockerfile was not the thing breaking.
#
# PyPI is a registry this build already depends on for every other dependency, so
# this removes a failure domain rather than trading one for another. Pinned,
# because an unpinned build tool is the same class of problem as an unpinned base.
RUN pip install --no-cache-dir uv==0.12.5

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

# V-149's migration guard refuses to reshape a REMOTE database unless this is
# set, and it raises at import time, so without it the app never binds a port
# and the healthcheck fails. This container IS the deploy path, which is what
# the guard means by "someone chose it": the threat it defends against is a
# local dev server booting on the wrong DATABASE_URL, and that server runs from
# .claude/launch.json, never from this image.
#
# It lives here rather than in a Railway dashboard variable on purpose. The
# outage happened because the code asserted "the deploy path does" set this
# while the setting existed nowhere the repo could see, so nothing could check
# the claim. In here it is reviewable, it ships with the image that needs it,
# and backend/tests/test_remote_migration_guard.py reads this file and fails if
# it goes missing again.
ENV SKEPTIC_ALLOW_REMOTE_MIGRATION=1

# runtime env expected from the platform:
#   R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET
#   OPENROUTER_API_KEY (parser + verdict narration + grounded Q&A)
#   SKEPTIC_ACCESS_TOKEN (bearer auth; unset = open, dev only)
#   DATABASE_URL (Neon; omit to run on ephemeral local SQLite)
EXPOSE 8000
CMD ["sh", "-c", "uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
