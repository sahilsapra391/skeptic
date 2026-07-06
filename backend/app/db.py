"""Run storage (TECH-SPEC §1: runs + run_events).

DATABASE_URL (Neon Postgres) when configured; otherwise a local SQLite
file — identical SQLAlchemy code path, so pointing at Neon is purely an
environment change at deploy time (M6). If the configured database is
unreachable at startup (e.g. Neon transfer quota exhausted), the app
FALLS BACK to local SQLite and says so in /api/health — a dead runs DB
must not take the charts and parser down with it.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

log = logging.getLogger("db")

_DEFAULT_SQLITE = f"sqlite:///{Path(__file__).resolve().parents[1] / 'runs.db'}"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", _DEFAULT_SQLITE)
    # SQLAlchemy 2 wants postgresql://, Neon hands out postgres://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|error
    stage: Mapped[int] = mapped_column(Integer, default=0)
    seed: Mapped[int] = mapped_column(Integer, default=42)
    spec_json: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # tiny library-card summary — listings read THIS, never the full
    # payload (full payloads over the wire is how a transfer quota dies)
    summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # computed stats bundle (engine metrics + honesty report) — the ONLY
    # material grounded Q&A may draw numbers from
    stats_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # real per-stage preview lines shown while the gauntlet runs
    previews_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # D3: structured unlock needs stored when a verdict is REFUSED — the
    # nightly auto-unlock scan reasons from these, not from display text
    unlock_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # D3: who started the run — "user" | "auto_unlock" | "receipt"
    origin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # D3: the refused/original run an automatic run supersedes or replays
    parent_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # D3c: 5-minute replay receipts attached to this (daily) run — merged
    # into the payload at READ time; the stored verdict is never rewritten
    receipts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # measured run cost {clock, sessions, engine_s, gauntlet_s, conditions}
    # — the pre-run time estimates are medians over THESE, never guesses
    perf_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    stage: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(120))


class TrialCounter(Base):
    """Per-strategy-family test count for the deflated Sharpe correction
    (TECH-SPEC §6.5). Family = underlying + structure; every run and every
    sweep value increments it — trying again IS the multiple-testing bias."""

    __tablename__ = "trial_counter"

    family: Mapped[str] = mapped_column(String(60), primary_key=True)
    trials: Mapped[int] = mapped_column(Integer, default=0)


def bump_trials(family: str, n: int = 1) -> int:
    """Increment and return the family's trial count."""
    with SessionLocal() as s:
        row = s.get(TrialCounter, family)
        if row is None:
            row = TrialCounter(family=family, trials=0)
            s.add(row)
        row.trials += n
        s.commit()
        return row.trials


def _engine_kwargs(url: str) -> dict[str, object]:
    """SQLite wants check_same_thread off (one file, many threads). Postgres
    (Neon) closes idle SSL connections aggressively — without pre_ping the
    pool hands out a dead socket and the request dies with
    'SSL connection has been closed unexpectedly'; pre_ping validates the
    connection first and recycle retires it before Neon's ~5-min idle cut."""
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True, "pool_recycle": 280}


_engine = create_engine(_database_url(), **_engine_kwargs(_database_url()))
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)

# set when the configured database was unreachable and we fell back
FALLBACK_REASON: str | None = None


def init_db() -> None:
    global _engine, SessionLocal, FALLBACK_REASON
    try:
        Base.metadata.create_all(_engine)
        _ensure_columns()
    except Exception as exc:  # unreachable/refusing DB — degrade, loudly
        reason = str(exc).strip().split("\n")[0][:200]
        log.error("configured database unavailable (%s) — falling back to local SQLite", reason)
        FALLBACK_REASON = reason
        _engine = create_engine(_DEFAULT_SQLITE, connect_args={"check_same_thread": False})
        SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
        Base.metadata.create_all(_engine)
        _ensure_columns()


def status() -> str:
    if FALLBACK_REASON:
        return f"local SQLite fallback — configured DB unavailable: {FALLBACK_REASON}"
    return "postgres (Neon)" if not _database_url().startswith("sqlite") else "local SQLite"


def _ensure_columns() -> None:
    """Additive micro-migration: create_all never alters existing tables,
    so columns added after first deploy are patched in here."""
    from sqlalchemy import inspect, text

    existing = {c["name"] for c in inspect(_engine).get_columns("runs")}
    with _engine.begin() as conn:
        for column in ("stats_json", "previews_json", "summary_json", "unlock_json",
                       "receipts_json", "perf_json"):
            if column not in existing:
                conn.execute(text(f"ALTER TABLE runs ADD COLUMN {column} TEXT"))
        for column, kind in (("origin", "VARCHAR(20)"), ("parent_run_id", "VARCHAR(40)")):
            if column not in existing:
                conn.execute(text(f"ALTER TABLE runs ADD COLUMN {column} {kind}"))


def session() -> Session:
    return SessionLocal()
