"""The deploy config and the app have to agree, and nothing checked that they did.

Railway decides a deployment succeeded by asking the container one question: does
`healthcheckPath` answer. That path lives in `backend/railway.json`, the route
lives in `app/main.py`, and the two were free to drift apart with every test
still green. Today's outage was the same shape one layer down (the Dockerfile
not setting a flag the app requires at import), and the lesson generalizes: a
deploy fails on the seams between the artifact, its config, and the code, and
those seams had no tests at all.

These are cheap, they run in the normal suite, and each one names a way the
container can be perfectly correct and still never come up.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
RAILWAY_JSON = BACKEND / "railway.json"
DOCKERFILE = BACKEND / "Dockerfile"


def _railway() -> dict:
    return json.loads(RAILWAY_JSON.read_text())


def test_healthcheck_path_is_a_real_route() -> None:
    """Rename or move the health route and Railway keeps polling the old path,
    every deploy fails, and the app itself is fine. The failure appears as
    "Healthcheck failure" with no clue that a route moved."""
    from app.main import app

    configured = _railway()["deploy"]["healthcheckPath"]
    paths = {getattr(route, "path", None) for route in app.routes}
    assert configured in paths, (
        f"railway.json healthchecks {configured!r}, which is not a route on the "
        f"app. Railway will fail every deployment while the container is healthy."
    )


def test_healthcheck_route_needs_no_auth() -> None:
    """The bearer middleware is applied app-wide. If it ever starts covering the
    health route, Railway's unauthenticated probe gets a 401 and every deploy
    fails a healthcheck that the app is answering correctly."""
    from fastapi.testclient import TestClient

    from app.main import app

    configured = _railway()["deploy"]["healthcheckPath"]
    response = TestClient(app).get(configured)
    assert response.status_code == 200, (
        f"{configured} returned {response.status_code} without credentials. "
        "Railway's probe does not authenticate, so this is a failed deploy."
    )


def test_container_binds_all_interfaces_on_the_injected_port() -> None:
    """Railway injects $PORT and routes to it from outside the container. A CMD
    binding 127.0.0.1, or a hardcoded port, works perfectly under `docker run`
    on a laptop and is unreachable in production."""
    cmd = DOCKERFILE.read_text()
    assert "--host 0.0.0.0" in cmd, (
        "the container must bind 0.0.0.0; 127.0.0.1 is unreachable from outside it"
    )
    assert re.search(r"\$\{?PORT", cmd), (
        "the container must listen on Railway's injected $PORT, not a fixed port"
    )


def test_healthcheck_timeout_clears_a_cold_start() -> None:
    """Not a correctness bound, a regret bound. The gauntlet imports numpy and
    pandas at startup; a timeout trimmed below a cold start turns a slow boot
    into a failed deploy, which reads identically to a broken build."""
    timeout = _railway()["deploy"].get("healthcheckTimeout", 0)
    assert timeout >= 60, f"healthcheckTimeout is {timeout}s, too tight for a cold start"


def test_tests_never_ship_inside_the_image() -> None:
    """This file asserts things about the deploy; it must not be part of it.
    More practically, the exclusion is what lets a test-only commit trigger a
    deployment whose image is byte-identical to the one already verified."""
    ignored = (BACKEND / ".dockerignore").read_text().splitlines()
    assert "tests" in [line.strip() for line in ignored]
