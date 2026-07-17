"""Test env: run storage goes to a throwaway SQLite file, never the repo's
local runs.db (and never Neon). Must run before anything imports app.db."""

import os
import tempfile

import pytest

_tmp = tempfile.NamedTemporaryFile(prefix="skeptic-test-runs-", suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "real_anon_armor: exercise the real launch-L4 anon armor "
        "(opt out of the default neutralizer — see tests/test_anon.py)",
    )


@pytest.fixture(autouse=True)
def _neutralize_anon_armor(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The launch-L4 anon armor limits an anonymous device to ONE free
    backtest (per signed token, per IP window, under a global daily budget,
    daily clock + ≤3y only). The many tests written before it fire several
    anonymous runs from one TestClient and are NOT about the armor — the
    armor would 402 their second run. Neutralize the anonymous gate for them
    so the run pipeline is exercised exactly as before.

    Tests that DO exercise the armor opt out with @pytest.mark.real_anon_armor
    (tests/test_anon.py marks its whole module) and see the real thing."""
    if request.node.get_closest_marker("real_anon_armor"):
        return
    from app import anon

    monkeypatch.setattr(anon, "verify_turnstile", lambda token, ip: True)
    monkeypatch.setattr(anon, "enforce_constraints", lambda spec: None)
    monkeypatch.setattr(anon, "check_limits", lambda token_h, ip_h: "ok")
    monkeypatch.setattr(anon, "record_trial", lambda *a, **k: None)
