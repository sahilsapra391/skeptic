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
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    stage: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(120))


_engine = create_engine(
    _database_url(),
    connect_args={"check_same_thread": False} if _database_url().startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(_engine)


def session() -> Session:
    return SessionLocal()
