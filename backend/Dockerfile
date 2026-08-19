# Skeptic backend — Railway deploy (BUILD-PLAN M6).
# uv-based build; runs uvicorn on Railway's injected $PORT.

# Both base images are pinned by DIGEST, not by tag. `python:3.13-slim` and
# `ghcr.io/astral-sh/uv:latest` are moving tags on external registries, pulled
# fresh on every build, which means the deploy could change underneath us
# without a commit and a build could fail for reasons no one here authored.
# Update these deliberately, the same way a lockfile is updated.
#
# Resolved with `docker buildx imagetools inspect python:3.13-slim` on 2026-08-18.
#   python:3.13-slim -> sha256:ffb752e1...c6e30a
#
# The runtime Python is 3.13 and that is the DECIDED target (V-235), not a drift.
# CI moves to 3.13 to match; testing on an interpreter production does not run is
# the false-green shape at the platform layer. CI was in fact the only 3.12 in the
# system: `requires-python` is ">=3.12", so uv resolves 3.13 locally, and every
# test run through A2 was already a 3.13 result.
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a

# uv comes from PyPI, pinned, and NOT from ghcr.io.
#
# Two changes arrived here from different directions and both survive, because
# they remove different things. The ghcr.io dependency is gone (one registry
# instead of two), and what remains is pinned by version instead of tracking
# `:latest`. Together with the digest-pinned base above, no part of this build
# resolves a moving reference on an external host any more.
#
# The ghcr removal's original rationale was WRONG and is recorded as such: it was
# a hypothesis that Railway could not reach ghcr.io, and the actual cause was a
# Railway platform incident. It stays because removing a build-time dependency on
# a second registry, and pinning the tool that replaces it, are worth doing on
# their own merits — not because the theory that produced it held up.
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
