"""Run storage (TECH-SPEC §1: runs + run_events).

DATABASE_URL (Neon Postgres) when configured; otherwise a local SQLite
file — identical SQLAlchemy code path, so pointing at Neon is purely an
environment change at deploy time (M6).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

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
    # computed stats bundle (engine metrics + honesty report) — the ONLY
    # material grounded Q&A may draw numbers from
    stats_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


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


_engine = create_engine(
    _database_url(),
    connect_args={"check_same_thread": False} if _database_url().startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(_engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """Additive micro-migration: create_all never alters existing tables,
    so columns added after first deploy are patched in here."""
    from sqlalchemy import inspect, text

    existing = {c["name"] for c in inspect(_engine).get_columns("runs")}
    with _engine.begin() as conn:
        if "stats_json" not in existing:
            conn.execute(text("ALTER TABLE runs ADD COLUMN stats_json TEXT"))


def session() -> Session:
    return SessionLocal()
