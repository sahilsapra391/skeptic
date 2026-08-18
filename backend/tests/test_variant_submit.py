"""Submit-side variant gates on POST /api/backtest.

THE ORDER IS THE CONTRACT (V-167): lock check first, zero-edit guard second,
debit + run creation + lineage stamping third — and the first two happen
BEFORE the debit exists in any form, not rolled back after. Lineage rides the
same transaction as the debit, so a crash between them leaves neither: a run
with a debit and no lineage is a variant that lost its parent, and there is
no repair path for that.
"""

from __future__ import annotations

import copy
import itertools
import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app import db
from app.api.variant import classify, diff_specs
from app.engine.market import build_fixture_store
from app.main import app
from tests.fixtures.engine import fx_short_put_assigned as fx

PASSWORD = "correct-horse-battery"

_ip_counter = itertools.count(1)


def _fresh_ip() -> dict[str, str]:
    n = next(_ip_counter)
    return {"x-forwarded-for": f"10.77.{n // 250}.{n % 250}"}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    import app.data.chains as chains_module

    monkeypatch.setattr(
        chains_module,
        "load_market_store",
        lambda ticker, **kw: build_fixture_store("SPY", fx.CHAINS, fx.UNDERLYING),
    )
    return TestClient(app, base_url="https://testserver")


def _signup(client: TestClient) -> str:
    email = f"variant-{uuid.uuid4().hex[:10]}@example.com"
    r = client.post(
        "/api/auth/signup",
        json={"email": email, "password": PASSWORD},
        headers=_fresh_ip(),
    )
    assert r.status_code == 200, r.text
    with db.session() as s:
        return s.query(db.User).filter(db.User.email == email).one().id


def _store_parent(uid: str | None, spec: dict[str, Any]) -> str:
    rid = f"vp{uuid.uuid4().hex[:10]}"
    with db.session() as s:
        s.add(db.Run(id=rid, status="done", user_id=uid, spec_json=json.dumps(spec)))
        s.commit()
    return rid


def _debits(uid: str) -> list[str | None]:
    with db.session() as s:
        return [
            r.run_id
            for r in s.query(db.CreditLedger)
            .filter(db.CreditLedger.user_id == uid, db.CreditLedger.reason == "run_debit")
            .all()
        ]


def _run_count() -> int:
    with db.session() as s:
        return s.query(db.Run).count()


def _post_variant(
    client: TestClient,
    parent: str,
    spec: dict[str, Any],
    *,
    untouched: bool = False,
) -> Any:
    return client.post(
        "/api/backtest",
        json={
            "spec": spec,
            "parent_run_id": parent,
            "min_trades": 1,
            "provenance": {
                "v": 1,
                "prompt": {"text": "sell puts"},
                "conversation": [],
                "confirmed": {"untouched": untouched},
            },
        },
    )


def _atm_spec() -> dict[str, Any]:
    spec = copy.deepcopy(fx.SPEC)
    spec["position"]["legs"][0]["strike_selection"] = {"method": "atm"}
    return spec


# --- V-166: the atm shape, both layers in one test ----------------------------


def test_atm_locks_raw_and_diffs_clean_normalized() -> None:
    """The production tier (b) shape read both ways. The RAW layer must lock
    the strike dial (classify sees `atm`, which the dial cannot express); the
    NORMALIZED layer must produce zero diff rows for a verbatim pass-through
    (canonical_spec turns `atm` into delta 0.5 on BOTH sides). If either half
    fails, its assertion names which layer moved."""
    spec = _atm_spec()
    rep = classify(spec)
    assert rep.tier == "b" and "strike" in rep.locked, (
        "RAW layer moved: classify() no longer locks an atm lead strike"
    )
    assert diff_specs(spec, copy.deepcopy(spec)) == [], (
        "NORMALIZED layer moved: a verbatim atm pass-through now diffs non-empty"
    )


# --- V-167 / V-168: lock check first, named rejection --------------------------


def test_locked_ticker_rejects_before_any_debit(client: TestClient) -> None:
    uid = _signup(client)
    parent = _store_parent(uid, fx.SPEC)
    runs_before = _run_count()

    doctored = copy.deepcopy(fx.SPEC)
    doctored["underlying"]["ticker"] = "QQQ"
    r = _post_variant(client, parent, doctored)

    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "underlying.ticker" in detail and "locked on this variant" in detail
    assert _debits(uid) == [], "the lock check must precede the debit (V-167)"
    assert _run_count() == runs_before


def test_lock_rejection_speaks_the_tier_b_register(client: TestClient) -> None:
    """V-168: a doctored client gets the same honest sentence a confused
    legitimate client would — the field, and the parent's real rule in the
    same words the read-only dial uses."""
    uid = _signup(client)
    parent = _store_parent(uid, _atm_spec())

    edited = _atm_spec()
    edited["position"]["legs"][0]["strike_selection"] = {
        "method": "delta",
        "value": 0.20,
    }
    r = _post_variant(client, parent, edited)

    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "at the money" in detail, "the reason speaks the dial's register"
    assert "position.legs" in detail, "the field is named"
    assert _debits(uid) == []


# --- V-169: the zero-edit guard's two causes, distinguished --------------------


def test_true_noop_blocks_before_the_debit_with_the_parent_link(
    client: TestClient,
) -> None:
    uid = _signup(client)
    parent = _store_parent(uid, fx.SPEC)
    runs_before = _run_count()

    r = _post_variant(client, parent, copy.deepcopy(fx.SPEC), untouched=False)

    assert r.status_code == 409, r.text
    body = r.json()
    assert "Nothing changed. This would be the same run." in body["detail"]
    assert parent in body["detail"], "the parent link rides the block"
    assert _debits(uid) == [], "a no-op must never reach the debit"
    assert _run_count() == runs_before


def test_rebuild_mismatch_fails_loudly_naming_the_field(client: TestClient) -> None:
    """V-19 / V-169: the user edited NOTHING and the specs differ anyway —
    that is the lossy rebuild resurfacing, and it fails at the API boundary
    naming the drifted field, not merely by declining to spend a credit."""
    uid = _signup(client)
    parent = _store_parent(uid, fx.SPEC)

    drifted = copy.deepcopy(fx.SPEC)
    drifted["backtest"]["seed"] = 999  # a change the user did not make
    r = _post_variant(client, parent, drifted, untouched=True)

    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "backtest.seed" in detail, "the drifted field is named"
    assert "rebuild" in detail.lower(), "the cause is named as a rebuild defect"
    assert _debits(uid) == []
    # V-171: the error's field strings ARE the pinned V-164 vocabulary — when
    # this alarm fires in production, its text is what gets grepped against
    # the vocabulary, so they cannot be dialects
    for row in diff_specs(fx.SPEC, drifted):
        assert row["field"] in detail, f"{row['field']} missing from the alarm text"


# --- V-167: the happy path stamps lineage in the debit transaction -------------


def test_variant_debits_and_stamps_lineage_atomically(client: TestClient) -> None:
    uid = _signup(client)
    parent = _store_parent(uid, fx.SPEC)

    edited = copy.deepcopy(fx.SPEC)
    edited["backtest"]["seed"] = 7
    r = _post_variant(client, parent, edited)
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    with db.session() as s:
        row = s.get(db.Run, run_id)
        assert row.parent_run_id == parent
        assert row.root_run_id == parent, "a first variant roots at its parent"
        assert row.variant_ordinal == 1
    assert run_id in _debits(uid), "the debit and the lineage share a transaction"

    # second variant of the same parent: ordinal 2, same root
    edited2 = copy.deepcopy(fx.SPEC)
    edited2["backtest"]["seed"] = 8
    r2 = _post_variant(client, parent, edited2)
    assert r2.status_code == 200, r2.text
    with db.session() as s:
        row2 = s.get(db.Run, r2.json()["run_id"])
        assert (row2.root_run_id, row2.variant_ordinal) == (parent, 2)

    # variant OF a variant: the chain keeps ONE root, ordinals keep counting
    edited3 = copy.deepcopy(fx.SPEC)
    edited3["backtest"]["seed"] = 9
    r3 = _post_variant(client, r2.json()["run_id"], edited3)
    assert r3.status_code == 200, r3.text
    with db.session() as s:
        row3 = s.get(db.Run, r3.json()["run_id"])
        assert row3.parent_run_id == r2.json()["run_id"], "parent is the IMMEDIATE run"
        assert row3.root_run_id == parent, "the root never re-roots (V-45)"
        assert row3.variant_ordinal == 3


def test_variant_bumps_the_family_trial_counter(client: TestClient) -> None:
    """V-44: a variant is a human run and bumps like any other — no exemption,
    no multiplier. (Beside test_human_runs_still_bump in spirit; lives here
    because this file owns the variant fixtures.)"""
    uid = _signup(client)
    parent = _store_parent(uid, fx.SPEC)
    family = f"{fx.SPEC['underlying']['ticker']}:{fx.SPEC['position']['structure']}"
    with db.session() as s:
        row = s.get(db.TrialCounter, family)
        before = row.trials if row else 0

    edited = copy.deepcopy(fx.SPEC)
    edited["backtest"]["seed"] = 11
    assert _post_variant(client, parent, edited).status_code == 200

    with db.session() as s:
        assert s.get(db.TrialCounter, family).trials == before + 1


def test_crash_after_debit_before_run_insert_leaves_neither(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V-167: the atomicity guarantee extended to lineage. creation_record is
    evaluated while building the Run row — AFTER the ledger add, BEFORE the
    insert — so raising there is a crash in exactly the window where a debit
    exists and lineage does not. Neither may survive."""
    uid = _signup(client)
    parent = _store_parent(uid, fx.SPEC)
    runs_before = _run_count()

    import app.api.runs as runs_api

    def boom(*a: Any, **kw: Any) -> str:
        raise RuntimeError("crash between debit and run insert")

    monkeypatch.setattr(runs_api, "creation_record", boom)
    edited = copy.deepcopy(fx.SPEC)
    edited["backtest"]["seed"] = 13
    # TestClient re-raises server exceptions rather than fabricating a 500 —
    # the crash is real either way; what matters is the DB state after it
    with pytest.raises(RuntimeError, match="crash between debit"):
        _post_variant(client, parent, edited)

    assert _debits(uid) == [], "a debit survived a crash before the run existed"
    assert _run_count() == runs_before


# --- V-170: the ordinal race loses at the database ------------------------------


def test_duplicate_ordinal_is_refused_by_the_unique_index() -> None:
    """Two concurrent variants computing the same max+1: the second INSERT
    must fail, because the ordinal is stored forever (V-25) and a duplicate
    would be permanent. Enforced by uq_runs_variant_ordinal, not by hope."""
    root = f"root{uuid.uuid4().hex[:8]}"
    with db.session() as s:
        s.add(db.Run(id=f"{root}a", spec_json="{}", root_run_id=root, variant_ordinal=1))
        s.commit()
    with db.session() as s:
        s.add(db.Run(id=f"{root}b", spec_json="{}", root_run_id=root, variant_ordinal=1))
        with pytest.raises(IntegrityError):
            s.commit()


# --- the automatic origins are NOT variants -------------------------------------


def test_auto_rerun_of_the_same_spec_is_not_blocked(client: TestClient) -> None:
    """An auto_unlock re-run's whole point is the SAME spec on more data
    (HONESTY.md D3b). The zero-edit guard applies to origin="user" variants
    only; gating automatic origins on it would break the unlock queue."""
    parent = _store_parent(None, fx.SPEC)
    import os

    os.environ["SKEPTIC_ACCESS_TOKEN"] = "svc-variant-test"
    try:
        r = client.post(
            "/api/backtest",
            json={
                "spec": copy.deepcopy(fx.SPEC),
                "parent_run_id": parent,
                "origin": "auto_unlock",
                "min_trades": 1,
            },
            headers={"authorization": "Bearer svc-variant-test"},
        )
        assert r.status_code == 200, r.text
    finally:
        os.environ["SKEPTIC_ACCESS_TOKEN"] = ""


def test_ordinal_collision_retries_once_and_the_user_never_sees_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V-172: a cross-tab race is the legitimate path to the collision, and
    the correct experience is ONE run created and no error surfaced. Feed the
    first attempt a stale ordinal (as if another tab landed between compute
    and commit); the unique index rejects it, the retry recomputes on a fresh
    transaction, and the submit succeeds."""
    uid = _signup(client)
    parent = _store_parent(uid, fx.SPEC)
    # ordinal 1 is already taken in this family, as if the other tab won
    with db.session() as s:
        s.add(db.Run(id=f"{parent}sib", spec_json="{}",
                     root_run_id=parent, variant_ordinal=1))
        s.commit()

    import app.api.runs as runs_api

    real = runs_api._next_ordinal
    calls = {"n": 0}

    def stale_then_real(s: Any, root: str) -> int:
        calls["n"] += 1
        return 1 if calls["n"] == 1 else real(s, root)  # stale on attempt 1

    monkeypatch.setattr(runs_api, "_next_ordinal", stale_then_real)
    edited = copy.deepcopy(fx.SPEC)
    edited["backtest"]["seed"] = 21
    r = _post_variant(client, parent, edited)

    assert r.status_code == 200, "the user must never see the race"
    assert calls["n"] == 2, "attempt 1 collided, attempt 2 recomputed"
    with db.session() as s:
        row = s.get(db.Run, r.json()["run_id"])
        assert row.variant_ordinal == 2, "the retry landed the next free ordinal"
    assert _debits(uid).count(r.json()["run_id"]) == 1, "exactly one debit"


def test_an_anonymous_caller_cannot_submit_a_variant(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V-04 server-side. `origin` defaults to "user" and anon callers are not
    charged, so without this an anonymous POST carrying parent_run_id got a
    free variant that stamped lineage into a family with user_id NULL. The API
    must decline what the UI declines to offer, in the same words."""
    parent = _store_parent(None, fx.SPEC)          # unowned, so access allows it
    fresh = TestClient(app, base_url="https://testserver")   # no session cookie
    r = fresh.post(
        "/api/backtest",
        json={"spec": copy.deepcopy(fx.SPEC), "parent_run_id": parent,
              "min_trades": 1},
    )
    assert r.status_code == 402, r.text
    assert "create a free account" in r.json()["detail"]
    with db.session() as s:
        assert s.query(db.Run).filter(db.Run.parent_run_id == parent).count() == 0


def test_a_non_ordinal_integrity_error_is_not_blamed_on_the_race(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 409 may only be raised for a verified uq_runs_variant_ordinal
    violation. Any other constraint failure — a credit-ledger partial index, a
    duplicate run id — must propagate rather than be reported as "another
    variant landed at the same moment", a cause nothing checked. Same rule the
    V-168 lock message follows: name a reason you established first."""
    uid = _signup(client)
    parent = _store_parent(uid, fx.SPEC)

    import app.api.runs as runs_api

    real = runs_api._next_ordinal

    def boom_on_commit(s: Any, root: str) -> int:
        # force an integrity failure that has nothing to do with the ordinal:
        # a second run_debit row for a run id that already has one
        s.add(db.CreditLedger(user_id=uid, delta=-1, reason="engine_refund",
                              run_id="dupe-refund"))
        s.add(db.CreditLedger(user_id=uid, delta=-1, reason="engine_refund",
                              run_id="dupe-refund"))
        return real(s, root)

    calls = {"n": 0}
    real_ordinal = runs_api._next_ordinal

    def counting(s: Any, root: str) -> int:
        calls["n"] += 1
        return boom_on_commit(s, root)

    monkeypatch.setattr(runs_api, "_next_ordinal", counting)
    edited = copy.deepcopy(fx.SPEC)
    edited["backtest"]["seed"] = 31

    with pytest.raises(IntegrityError):
        _post_variant(client, parent, edited)

    # V-185: NO RETRY on a non-ordinal cause — one attempt, then propagate
    assert calls["n"] == 1, "a non-ordinal integrity error must not retry"
    # and nothing was written: the rollback still discards the debit
    assert _debits(uid) == []
    assert real_ordinal is not None  # keep the reference meaningful


def test_the_ordinal_collision_check_reads_both_engines() -> None:
    """Postgres names the constraint, SQLite names the columns. The predicate
    has to recognise its own race on either, and decline everything else."""
    from app.api.runs import _is_ordinal_collision

    class _Diag:
        constraint_name = "uq_runs_variant_ordinal"

    class _PgOrig:
        diag = _Diag()

    class _Exc:
        def __init__(self, orig: Any) -> None:
            self.orig = orig

    assert _is_ordinal_collision(_Exc(_PgOrig()))          # postgres
    assert _is_ordinal_collision(_Exc(
        "UNIQUE constraint failed: runs.root_run_id, runs.variant_ordinal"))
    # a different constraint on either engine is NOT this race
    assert not _is_ordinal_collision(_Exc(
        "UNIQUE constraint failed: credit_ledger.run_id"))

    class _OtherDiag:
        constraint_name = "uq_credit_ledger_refund"

    class _OtherOrig:
        diag = _OtherDiag()

    assert not _is_ordinal_collision(_Exc(_OtherOrig()))


def test_a_non_variant_integrity_error_never_yields_the_variant_race_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V-185 case 2. A plain run has no parent, so `variant_root` is None and
    the old handler skipped the retry and fell straight to a 409 announcing
    that "another variant of this run landed" — on a run with no family at
    all. The error must propagate as itself instead."""
    uid = _signup(client)

    import app.api.runs as runs_api

    real = runs_api.creation_record

    def duplicate_refund(*a: Any, **kw: Any) -> str:
        # a ledger constraint, nothing to do with ordinals, raised while the
        # Run row is being built — inside the debit transaction
        raise IntegrityError(
            "INSERT INTO credit_ledger",
            {},
            Exception("UNIQUE constraint failed: credit_ledger.run_id"),
        )

    monkeypatch.setattr(runs_api, "creation_record", duplicate_refund)
    r = None
    try:
        r = client.post("/api/backtest", json={"spec": copy.deepcopy(fx.SPEC),
                                              "min_trades": 1})
    except IntegrityError:
        pass  # propagated, which is the point
    if r is not None:
        assert r.status_code != 409, (
            "a non-variant integrity error was reported as the variant race"
        )
    assert real is not None
    assert _debits(uid) == []
