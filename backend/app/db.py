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

from sqlalchemy import DateTime, Index, Integer, String, Text, create_engine, func, text
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
    # V-25: the head of a variant chain. A root run has this NULL; every variant
    # carries the id of the run the chain started from, so the Library can group
    # a family without walking parent links one at a time.
    root_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # V-25: this run's position in its chain, assigned once at creation and
    # STORED. Never recomputed from a live count — a deleted variant must leave
    # a gap rather than renumber its siblings (V-45).
    variant_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # D3c: 5-minute replay receipts attached to this (daily) run — merged
    # into the payload at READ time; the stored verdict is never rewritten
    receipts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # measured run cost {clock, sessions, engine_s, gauntlet_s, conditions}
    # — the pre-run time estimates are medians over THESE, never guesses
    perf_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # F7: on-demand fill audit vs an independent vendor — stored like
    # receipts; the run's verdict is never rewritten
    audit_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # the run's setup story — prompt, clarifying Q&A and the confirmed
    # draft snapshotted at creation, mechanics appended at completion.
    # Display-only: never read by the engine, the verdict LLM, or grounded
    # ask. NULL on runs predating the column — their record is DERIVED at
    # read time (app/api/provenance.py); the conversation is never invented.
    provenance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Tier 1 (notebook): pinned deterministic re-execution outcome — stored
    # like receipts/audit; the run's verdict is never rewritten
    reproduce_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # launch L1b: the account that owns this run. NULL = pre-accounts /
    # anonymous — claimable exactly once at signup (the conversion moment)
    user_id: Mapped[str | None] = mapped_column(String(40), nullable=True)


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    stage: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(120))


class User(Base):
    """Accounts (launch L1). This table IS the traction record — every
    signup lands here regardless of auth provider (PRD C)."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    # normalized to lowercase in code before insert — citext is Postgres-only
    # and the local/test path is SQLite, so the DB type can't do it for us
    email: Mapped[str] = mapped_column(String(320), unique=True)
    # NULL under managed auth (owner decision D1 = Clerk); the column exists
    # so a later self-rolled provider slots in without a migration
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    clerk_user_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CreditLedger(Base):
    """Credits are an append-only ledger; balance = SUM(delta), never a
    mutable balance column (PRD E). Rows are only ever inserted."""

    __tablename__ = "credit_ledger"
    __table_args__ = (
        # exactly one signup grant per user, enforced by the DATABASE — a
        # retried or racing signup cannot double-grant even through a bug
        # in the application path
        Index(
            "uq_credit_ledger_signup_grant",
            "user_id",
            unique=True,
            sqlite_where=text("reason = 'signup_grant'"),
            postgresql_where=text("reason = 'signup_grant'"),
        ),
        # at most one engine_refund per run (launch L2): the credit law is
        # "you only pay for a graded verdict", so a refusal / our-fault
        # failure refunds — but exactly ONCE, DB-enforced against retries.
        Index(
            "uq_credit_ledger_refund",
            "run_id",
            unique=True,
            sqlite_where=text("reason = 'engine_refund'"),
            postgresql_where=text("reason = 'engine_refund'"),
        ),
        # exactly one purchase grant per Stripe event (launch L3): Stripe
        # redelivers webhook events, so the credit grant is idempotent on the
        # event id, DB-enforced — a redelivered checkout.session.completed can
        # never double-grant credits.
        Index(
            "uq_credit_ledger_purchase",
            "ext_ref",
            unique=True,
            sqlite_where=text("reason = 'purchase'"),
            postgresql_where=text("reason = 'purchase'"),
        ),
        # at most one chargeback per PAYMENT (launch L3 money-exposure fix):
        # a refund or a dispute claws back the credits a purchase granted, but
        # the money only leaves our account ONCE — so the reversal is idempotent
        # on the Stripe payment_intent, not the event. That backstops BOTH a
        # redelivered event AND a refund arriving alongside a dispute on the
        # same charge (two different event ids, one payment) — neither can
        # double-reverse.
        Index(
            "uq_credit_ledger_chargeback",
            "payment_ref",
            unique=True,
            sqlite_where=text("reason = 'chargeback'"),
            postgresql_where=text("reason = 'chargeback'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    # "signup_grant" | "purchase" | "run_debit" | "engine_refund"
    #   | "admin_adjust" | "chargeback"
    reason: Mapped[str] = mapped_column(String(20))
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # external idempotency key — the Stripe EVENT id for a purchase row (L3);
    # the partial unique index above makes the grant exactly-once per event.
    # For a chargeback row it holds the refund/dispute event id, for audit.
    ext_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # the Stripe payment_intent id (L3): stamped on a purchase row so a later
    # refund/dispute event (which carries the payment_intent, not our event id)
    # can find the grant to reverse; and on the chargeback row itself, where
    # the partial unique index makes the reversal exactly-once per payment.
    payment_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class AuthSession(Base):
    """Self-rolled sessions (launch L1b, owner decision D1-reversed): the
    cookie carries an opaque random token; the DB stores only its SHA-256.
    Revocation is a row update — sessions are DB truth, never stateless."""

    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EmailToken(Base):
    """Single-use email-verification tokens — stored hashed, like sessions."""

    __tablename__ = "email_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    purpose: Mapped[str] = mapped_column(String(20), default="verify")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AnonTrial(Base):
    """One row per anonymous free backtest (launch L4 anon armor). The
    signed anon-token's SHA-256 and a salted hash of the client IP gate the
    one-free-run-per-device rule; the global daily budget counts rows by
    created_at. run_id ties the trial to its run so signup can re-parent it
    (the claim flow). No raw token or IP is ever stored."""

    __tablename__ = "anon_trials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    ip_hash: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), index=True
    )


LEDGER_REASONS = {
    "signup_grant", "purchase", "run_debit", "engine_refund", "admin_adjust", "chargeback",
}


def credit_balance(user_id: str) -> int:
    """Balance = SUM over the append-only ledger — computed, never stored."""
    with SessionLocal() as s:
        return credit_balance_tx(s, user_id)


def credit_balance_tx(s: Session, user_id: str) -> int:
    """Balance within an EXISTING transaction — the debit path recomputes the
    balance under a user-row lock, so the read and the debit are one atomic
    decision (no overdraft from two simultaneous runs)."""
    total = (
        s.query(func.coalesce(func.sum(CreditLedger.delta), 0))
        .filter(CreditLedger.user_id == user_id)
        .scalar()
    )
    return int(total or 0)


def was_refunded(run_id: str) -> bool:
    """True once a run's credit has been given back (engine_refund exists).
    The view-time re-grade uses this to SEAL a refunded run: you got the
    credit OR a blessed verdict, never both — a refunded refusal must not be
    re-graded into a graded verdict at a lower bar (that would be a free
    graded verdict + free engine compute = a paywall bypass)."""
    with SessionLocal() as s:
        return (
            s.query(CreditLedger.id)
            .filter(CreditLedger.run_id == run_id, CreditLedger.reason == "engine_refund")
            .first()
            is not None
        )


def refund_run_tx(s: Session, run_id: str) -> bool:
    """Add the engine_refund row to the CALLER's transaction (no commit).
    Idempotent + self-scoped: a no-op if the run was never charged (anon /
    service / claimed-anon) or was already refunded. Used INSIDE the same
    transaction that flips the run to done/error, so 'the run is visible'
    implies 'the refund is visible' — a concurrent read-time re-grade can
    never catch a refunded run in an un-refunded window (the paywall SEAL)."""
    debit = (
        s.query(CreditLedger)
        .filter(CreditLedger.run_id == run_id, CreditLedger.reason == "run_debit")
        .first()
    )
    if debit is None:
        return False  # never charged — nothing to refund
    already = (
        s.query(CreditLedger.id)
        .filter(CreditLedger.run_id == run_id, CreditLedger.reason == "engine_refund")
        .first()
    )
    if already is not None:
        return False  # idempotent — already refunded
    s.add(CreditLedger(user_id=debit.user_id, delta=1, reason="engine_refund", run_id=run_id))
    return True


def refund_run(run_id: str) -> bool:
    """Give back the credit a run debited — the credit law (owner override):
    you only pay for a GRADED verdict, so a refusal or an our-fault failure
    refunds. Standalone (own transaction) wrapper over refund_run_tx; the
    engine_refund unique index is the DB backstop against a race."""
    from sqlalchemy.exc import IntegrityError

    with SessionLocal() as s:
        if not refund_run_tx(s, run_id):
            return False
        try:
            s.commit()
        except IntegrityError:  # a concurrent refund won the race — fine
            s.rollback()
            return False
    return True


def grant_purchase(
    user_id: str, credits: int, stripe_event_id: str, payment_intent: str | None = None
) -> bool:
    """Grant purchased credits (launch L3). Idempotent per Stripe EVENT id —
    Stripe redelivers webhook events, so a redelivered checkout.session.completed
    must not double-grant. Returns True if THIS call granted; False if the event
    was already processed (the uq_credit_ledger_purchase index is the backstop).
    Only ever ADDS a row — balance stays SUM over the append-only ledger.

    payment_intent (the Stripe charge/PI id) is stamped on the row so a later
    refund or dispute can find this grant and reverse it (reverse_purchase)."""
    from sqlalchemy.exc import IntegrityError

    with SessionLocal() as s:
        s.add(
            CreditLedger(
                user_id=user_id,
                delta=credits,
                reason="purchase",
                ext_ref=stripe_event_id,
                payment_ref=payment_intent,
            )
        )
        try:
            s.commit()
        except IntegrityError:  # this event already granted — idempotent
            s.rollback()
            return False
    return True


def reverse_purchase(payment_intent: str, stripe_event_id: str) -> bool:
    """Claw back the credits a purchase granted (launch L3 refund/dispute).

    A charge.refunded or charge.dispute.created webhook means the buyer got
    their money back — so we reverse the credits by APPENDING a negative
    'chargeback' row (the ledger is append-only; we never mutate the grant).
    The reversal is exactly-once per PAYMENT: the money left our account once,
    so a redelivered event, or a refund arriving alongside a dispute on the
    same charge, must reverse only once — the uq_credit_ledger_chargeback index
    (on payment_ref) is the DB backstop.

    Reverses the EXACT credits that payment granted (summed from its purchase
    rows), not a constant — a promo grant of a different size is reversed to
    match. Returns True if THIS call reversed; False if there's no matching
    purchase (an unrelated charge → reverse nothing) or it was already reversed.

    The balance may go NEGATIVE if the buyer already spent the credits — that's
    correct: they can't run again until they re-buy."""
    from sqlalchemy.exc import IntegrityError

    with SessionLocal() as s:
        grants = (
            s.query(CreditLedger)
            .filter(
                CreditLedger.payment_ref == payment_intent,
                CreditLedger.reason == "purchase",
            )
            .all()
        )
        if not grants:
            return False  # no purchase for this charge — unrelated, reverse nothing
        granted = sum(g.delta for g in grants)
        if granted <= 0:
            return False  # nothing was granted for this payment — nothing to claw back
        s.add(
            CreditLedger(
                user_id=grants[0].user_id,
                delta=-granted,
                reason="chargeback",
                ext_ref=stripe_event_id,
                payment_ref=payment_intent,
            )
        )
        try:
            s.commit()
        except IntegrityError:  # already reversed for this payment — idempotent
            s.rollback()
            return False
    return True


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
    except RemoteMigrationRefused:
        # V-149: this is a REFUSAL, not an outage. Falling back to local SQLite
        # here would be worse than the accident it prevents — the server would
        # come up healthy on the wrong database and nobody would know.
        raise
    except Exception as exc:  # unreachable/refusing DB — degrade, loudly
        reason = str(exc).strip().split("\n")[0][:200]
        log.error("configured database unavailable (%s) — falling back to local SQLite", reason)
        FALLBACK_REASON = reason
        _engine = create_engine(_DEFAULT_SQLITE, connect_args={"check_same_thread": False})
        SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
        Base.metadata.create_all(_engine)
        _ensure_columns()


def target_line() -> str:
    """V-150: what this process actually connected to, in plain terms.

    A whole cycle was spent discovering after the fact which engine was in
    play. Same reasoning as V-85: the thing that ran announces what it ran
    against, at the moment it runs.
    """
    url = _database_url()
    if url.startswith("sqlite"):
        return f"LOCAL SQLite — {url.split('sqlite:///', 1)[-1]}"
    host = url.split("@", 1)[-1].split("/", 1)[0]
    return f"REMOTE postgres — {host}"


def status() -> str:
    if FALLBACK_REASON:
        return f"local SQLite fallback — configured DB unavailable: {FALLBACK_REASON}"
    return "postgres (Neon)" if not _database_url().startswith("sqlite") else "local SQLite"


def _is_local_target(url: str) -> bool:
    """SQLite anywhere, or Postgres on this machine. Everything else is
    somebody's real database."""
    if url.startswith("sqlite"):
        return True
    host = url.split("@", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    return host in {"localhost", "127.0.0.1", "::1", ""}


class RemoteMigrationRefused(RuntimeError):
    """V-149: a schema change against a remote database that nobody asked for."""


def _ensure_columns() -> None:
    """Additive micro-migration: create_all never alters existing tables,
    so columns added after first deploy are patched in here.

    V-149: REFUSES to run against a remote database unless
    SKEPTIC_ALLOW_REMOTE_MIGRATION is set. Local SQLite and localhost Postgres
    proceed silently; the deploy path sets the flag deliberately.

    This is not about any particular migration. It is that a dev server booting
    with the wrong DATABASE_URL should not be able to reshape production on its
    way up — a schema change should be something someone CHOSE. Today's
    additions were additive and nullable, so the accident was harmless; the
    next one might not be.
    """
    from sqlalchemy import inspect, text

    url = _database_url()
    if not _is_local_target(url) and not os.environ.get("SKEPTIC_ALLOW_REMOTE_MIGRATION"):
        host = url.split("@", 1)[-1].split("/", 1)[0]
        raise RemoteMigrationRefused(
            f"refusing to migrate a remote database ({host}). Set "
            "SKEPTIC_ALLOW_REMOTE_MIGRATION=1 if you mean it — the deploy "
            "path does."
        )

    existing = {c["name"] for c in inspect(_engine).get_columns("runs")}
    with _engine.begin() as conn:
        for column in ("stats_json", "previews_json", "summary_json", "unlock_json",
                       "receipts_json", "perf_json", "audit_json", "provenance_json",
                       "reproduce_json"):
            if column not in existing:
                conn.execute(text(f"ALTER TABLE runs ADD COLUMN {column} TEXT"))
        for column, kind in (("origin", "VARCHAR(20)"), ("parent_run_id", "VARCHAR(40)"),
                             ("user_id", "VARCHAR(40)"),
                             # V-25: variant lineage. root_run_id is the head of
                             # the chain; variant_ordinal is assigned ONCE at
                             # creation and stored, never recomputed from a live
                             # count. Deleting a variant leaves a gap, and gaps
                             # are correct: renumbering would make a saved PDF
                             # and the live app disagree about which run is which.
                             ("root_run_id", "VARCHAR(40)"),
                             ("variant_ordinal", "INTEGER")):
            if column not in existing:
                conn.execute(text(f"ALTER TABLE runs ADD COLUMN {column} {kind}"))
    # launch L3: the Stripe idempotency keys on the live credit_ledger —
    # ext_ref (event id, the purchase grant) and payment_ref (payment_intent,
    # the refund/dispute reversal link)
    ledger_cols = {c["name"] for c in inspect(_engine).get_columns("credit_ledger")}
    with _engine.begin() as conn:
        if "ext_ref" not in ledger_cols:
            conn.execute(text("ALTER TABLE credit_ledger ADD COLUMN ext_ref VARCHAR(80)"))
        if "payment_ref" not in ledger_cols:
            conn.execute(text("ALTER TABLE credit_ledger ADD COLUMN payment_ref VARCHAR(80)"))
    _ensure_indexes()


def _ensure_indexes() -> None:
    """create_all adds new indexes only to tables it CREATES, never to a
    table that already exists — so a partial unique index added after first
    deploy (L2 refund-once) is patched onto the live credit_ledger here.
    Both Postgres and SQLite (>=3.8) accept this exact partial-index DDL."""
    from sqlalchemy import text

    with _engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_ledger_refund "
            "ON credit_ledger (run_id) WHERE reason = 'engine_refund'"
        ))
        # launch L3: one purchase grant per Stripe event id (idempotent webhook)
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_ledger_purchase "
            "ON credit_ledger (ext_ref) WHERE reason = 'purchase'"
        ))
        # launch L3 refund/dispute: one chargeback per payment_intent — a refund
        # AND a dispute on the same charge (or a redelivered event) reverse once
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_ledger_chargeback "
            "ON credit_ledger (payment_ref) WHERE reason = 'chargeback'"
        ))


def session() -> Session:
    return SessionLocal()
