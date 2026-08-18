"""The V-149 remote-migration guard, and the deploy path that has to satisfy it.

This file exists because the guard shipped in PR-A1 with a docstring asserting
that "the deploy path does" set SKEPTIC_ALLOW_REMOTE_MIGRATION, and nothing
checked that claim. It was false. The flag was set nowhere, so the first
production deploy after the merge raised RemoteMigrationRefused out of
`init_db()` at module import, uvicorn never bound a port, and Railway failed the
healthcheck twice while the whole suite stayed green.

The suite stayed green because every test runs on local SQLite, where
`_is_local_target()` is True and the guard cannot fire. So a unit test of the
guard alone would not have caught it either. The test that catches it reads the
actual deploy artifact and checks the claim the code makes about it:
`test_dockerfile_sets_the_migration_flag`.

NOTHING HERE MAY TOUCH A NETWORK. Every url below uses the RFC 2606 `.invalid`
TLD, which is reserved to never resolve. An earlier draft built a real engine
against the production Neon hostname to reach the remote branch, and the suite
dialled production on every run. That is why the guard is now a pure function
of (url, environment): testing it needs no engine, so it can have no reach.
"""
from __future__ import annotations

import inspect as _inspect
import os
import re
from pathlib import Path

import pytest

from app.db import (
    RemoteMigrationRefused,
    _is_local_target,
    _refuse_remote_migration_if_unchosen,
)

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"

# .invalid is reserved by RFC 2606 and cannot resolve. Never put a real
# hostname in this file: it is committed, and the repo is public.
REMOTE_URL = "postgresql://u:p@ep-example-12345.us-east-1.aws.db.invalid/neondb"


def _host_of(url: str) -> str:
    authority = url.split("@", 1)[-1].split("/", 1)[0]
    if authority.startswith("["):
        return authority[1:].split("]", 1)[0].lower()
    return authority.rsplit(":", 1)[0].lower() if ":" in authority else authority.lower()


# Reserved by RFC so they cannot route: .invalid (2606), 2001:db8::/32 (3849),
# 192.0.2.0/24 + 198.51.100.0/24 + 203.0.113.0/24 (5737). Loopback is fine.
_UNROUTABLE_PREFIXES = ("2001:db8:", "192.0.2.", "198.51.100.", "203.0.113.")
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def test_no_resolvable_host_in_this_file() -> None:
    """The rule enforced on itself. A future edit that pastes a real hostname
    in to reproduce something fails here rather than in production's logs.

    It has already earned its keep twice: the first draft of this file used the
    production Neon hostname, and the second reached for `10.0.0.5` and
    `2600:1f18::1`, the latter being a live AWS prefix.
    """
    text = Path(__file__).read_text()
    for match in re.findall(r"postgres(?:ql)?://[^\s\"']+", text):
        host = _host_of(match)
        ok = (
            host.endswith(".invalid")
            or host in _LOOPBACK
            or host.startswith(_UNROUTABLE_PREFIXES)
        )
        assert ok, (
            f"{host!r} may resolve. Use a host reserved as unroutable so the "
            "suite can never dial out: a .invalid name, 2001:db8:: for IPv6, "
            "or 192.0.2.x / 198.51.100.x / 203.0.113.x for IPv4."
        )


class TestLocalTargetClassification:
    @pytest.mark.parametrize(
        "url",
        [
            "sqlite:///backend/runs.db",
            "sqlite:////tmp/x.db",
            "postgresql://u:p@localhost/neondb",
            "postgresql://u:p@localhost:5432/neondb",
            "postgresql://u:p@127.0.0.1:5432/neondb",
            "postgresql://u:p@[::1]:5432/neondb",
        ],
    )
    def test_local_targets_are_local(self, url: str) -> None:
        assert _is_local_target(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            REMOTE_URL,
            "postgresql://u:p@db.internal.example.invalid:5432/neondb",
            "postgres://u:p@192.0.2.5/neondb",
        ],
    )
    def test_remote_targets_are_remote(self, url: str) -> None:
        assert _is_local_target(url) is False

    def test_bracketed_ipv6_localhost(self) -> None:
        """Regression: `@[::1]:5432/db` split on the FIRST colon to `"["`, so a
        localhost IPv6 Postgres classified as remote and the set's own `"::1"`
        entry was unreachable. Refusing is the safe direction to be wrong in,
        which is exactly why it sat there unnoticed."""
        assert _is_local_target("postgresql://u:p@[::1]:5432/neondb") is True
        assert _is_local_target("postgresql://u:p@[2001:db8::1]:5432/db") is False


class TestGuard:
    """Pure function, no engine, no connection."""

    def test_remote_without_the_flag_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SKEPTIC_ALLOW_REMOTE_MIGRATION", raising=False)
        with pytest.raises(RemoteMigrationRefused) as exc:
            _refuse_remote_migration_if_unchosen(REMOTE_URL)
        # names the host, so the reader knows WHICH database was refused...
        assert "db.invalid" in str(exc.value)
        # ...and never the credentials in front of the @
        assert "u:p" not in str(exc.value)
        assert ":p@" not in str(exc.value)

    def test_remote_with_the_flag_is_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKEPTIC_ALLOW_REMOTE_MIGRATION", "1")
        _refuse_remote_migration_if_unchosen(REMOTE_URL)  # must not raise

    @pytest.mark.parametrize(
        "url", ["sqlite:///x.db", "postgresql://u:p@localhost/db", "postgresql://u:p@[::1]/db"]
    )
    def test_local_never_needs_permission(
        self, url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SKEPTIC_ALLOW_REMOTE_MIGRATION", raising=False)
        _refuse_remote_migration_if_unchosen(url)  # must not raise


class TestEnsureColumnsUsesTheEngine:
    def test_guard_judges_the_engine_not_the_configured_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The question is "am I about to reshape somebody's real database",
        and only the engine knows that.

        Judging the configured DATABASE_URL string instead breaks `init_db`'s
        documented fallback: when a remote database is unreachable, init_db
        swaps `_engine` to local SQLite and calls `_ensure_columns` again, at
        which point the configured string still says remote. The guard would
        then refuse a migration against a local SQLite file and the process
        dies, turning "degrade, loudly" into an outage. No flag is set here on
        purpose: local SQLite needs no permission.
        """
        import app.db as db

        monkeypatch.delenv("SKEPTIC_ALLOW_REMOTE_MIGRATION", raising=False)
        monkeypatch.setattr(db, "_database_url", lambda: REMOTE_URL)
        assert str(db._engine.url).startswith("sqlite"), "harness expects the SQLite test engine"
        db.Base.metadata.create_all(db._engine)  # _ensure_columns inspects `runs`

        try:
            db._ensure_columns()
        except RemoteMigrationRefused:  # pragma: no cover - the failure we guard
            pytest.fail(
                "refused a migration against local SQLite because the configured "
                "URL string was remote; this is init_db's fallback path dying"
            )

    def test_ensure_columns_reads_the_engine_url(self) -> None:
        source = _inspect.getsource(
            __import__("app.db", fromlist=["_ensure_columns"])._ensure_columns
        )
        assert "_engine.url" in source, (
            "_ensure_columns must pass the engine's url to the guard, not "
            "_database_url(); see the fallback failure above"
        )


class TestDeployPathSatisfiesTheGuard:
    """The claim the code makes about a file it never reads."""

    def test_dockerfile_sets_the_migration_flag(self) -> None:
        text = DOCKERFILE.read_text()
        assert re.search(
            r"^\s*(ENV\s+)?SKEPTIC_ALLOW_REMOTE_MIGRATION[=\s]+1\b", text, re.MULTILINE
        ), (
            "backend/Dockerfile does not set SKEPTIC_ALLOW_REMOTE_MIGRATION=1. "
            "The container is the deploy path, and app.db refuses to migrate a "
            "remote database without it, at import time, so the app never binds "
            "a port and the Railway healthcheck fails. Setting it in a dashboard "
            "variable instead of here is what produced the outage: nothing in "
            "the repo could see it."
        )

    def test_the_flag_the_dockerfile_sets_is_the_one_the_guard_reads(self) -> None:
        """Names drift, and a typo in either place is an outage."""
        import app.db as db

        source = _inspect.getsource(db._refuse_remote_migration_if_unchosen)
        assert "SKEPTIC_ALLOW_REMOTE_MIGRATION" in source
        assert "SKEPTIC_ALLOW_REMOTE_MIGRATION" in DOCKERFILE.read_text()
        assert os.environ.get("SKEPTIC_ALLOW_REMOTE_MIGRATION") in (None, "", "1")
