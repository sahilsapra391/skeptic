"""Guard for the idle-connection fix (incident 2026-07-06).

After the app sits idle, Neon drops the pooled SSL connection; without
pool_pre_ping the next request hands out the dead socket and dies with
'SSL connection has been closed unexpectedly' — surfacing as `500: {}` on
Library/Data. These pins keep the pool config from silently regressing.
"""

from __future__ import annotations

from sqlalchemy import create_engine

from app.db import _engine_kwargs


def test_postgres_engine_pre_pings_and_recycles() -> None:
    kwargs = _engine_kwargs("postgresql://u:p@host/db")
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 280
    # create_engine is lazy (no connection made) — assert the pool actually
    # carries the setting, not just the kwarg dict
    engine = create_engine("postgresql+psycopg2://u:p@localhost/db", **kwargs)
    assert engine.pool._pre_ping is True
    assert engine.pool._recycle == 280


def test_postgres_scheme_variant_also_covered() -> None:
    # db._database_url() rewrites postgres:// → postgresql://, but guard the
    # raw scheme too so any caller gets pre_ping on a non-sqlite URL
    assert _engine_kwargs("postgres://u:p@host/db")["pool_pre_ping"] is True


def test_sqlite_engine_keeps_thread_arg_and_no_pool_ping() -> None:
    kwargs = _engine_kwargs("sqlite:////tmp/runs.db")
    assert kwargs == {"connect_args": {"check_same_thread": False}}
    assert "pool_pre_ping" not in kwargs
