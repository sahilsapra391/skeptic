"""Run pipeline routes.

M2+M3: POST /api/backtest runs the engine AND the full honesty gauntlet
(OOS split, walk-forward, Monte Carlo, sensitivity, DSR, regime guard),
then the verdict writer — trust levels computed deterministically, every
narrated number validated against the stats payload.
/api/runs/{id}/ask answers questions grounded in the stored stats bundle
(same numeric validator); /api/parse (M4) stays an explicit 501.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import IntegrityError

from app import db
from app.api.jobs import claim_run_job, marker_age_minutes, pinned_engine_rerun
from app.api.payload import build_run_payload, run_summary
from app.api.provenance import (
    attach_mechanics,
    creation_record,
    derived_record,
    mechanics_record,
)
from app.engine.concurrency import ENGINE_LOCK, release_memory
from app.honesty.stages import MIN_TRADES
from app.models.spec import StrategySpec

log = logging.getLogger("runs")
router = APIRouter()

_PENDING_PARSE_KEY = (
    "the NL parser needs OPENROUTER_API_KEY — without it nothing is "
    "guessed server-side, ever."
)
_PENDING_ASK_KEY = (
    "grounded Q&A needs OPENROUTER_API_KEY — no key, no answers, "
    "and no numbers are invented in the meantime."
)
_PENDING_ASK_STATS = (
    "this run predates grounded Q&A (no stored stats bundle) — "
    "re-run the strategy to ask about it."
)
_PENDING_SWEEP = (
    "standalone sweeps arrive with the compare/sweep UI — the gauntlet already "
    "runs a sensitivity sweep on every backtest."
)


class ParseRequest(BaseModel):
    text: str
    answers: dict[str, Any] | None = None


VALID_ORIGINS = {"user", "auto_unlock", "receipt"}
AUTO_NOTE_MAX = 120


def _validation_detail(exc: ValidationError, limit: int | None = None) -> list[Any]:
    """Pydantic errors stripped to JSON-safe fields (type/loc/msg). Raw
    errors() embeds the live ValueError in ctx for every model_validator
    refusal, and the HTTPException handler json.dumps's the detail — so an
    honest 422 explanation became a bare `500: {}` in the UI (2026-07-07)."""
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    return errors if limit is None else errors[:limit]


class BacktestRequest(BaseModel):
    spec: dict[str, Any]
    seed: int | None = None
    # D3: automatic runs declare themselves — origin drives the trial-
    # counter policy and the Library's upgrade markers
    origin: str = "user"
    parent_run_id: str | None = None
    auto_note: str | None = None  # e.g. "62 new sessions" (server-truncated)
    # the evidence bar for a graded verdict (user setting, 2026-07-14).
    # Floor 1 — zero would grade an untraded strategy, which is nonsense;
    # None = the standard bar (or the parent's, for automatic re-runs)
    min_trades: int | None = Field(default=None, ge=1, le=10_000)
    # UX Chunk A: the client-captured setup story (prompt, clarifying Q&A,
    # confirmed draft) — display-only, size-capped in creation_record;
    # ignored on automatic runs, which have no conversation
    provenance: dict[str, Any] | None = None
    # launch L4 anon armor: the Cloudflare Turnstile token, required only on
    # the anonymous free-run path (signed-in / service callers ignore it)
    turnstile_token: str | None = Field(default=None, max_length=4000)


def _inherit_trials(parent_run_id: str | None, family: str) -> int:
    """Trial count for an AUTO re-run (owner decision, HONESTY.md): the same
    spec on more data is NOT a new try at the family — no bump. Prefer the
    parent's recorded trial count; fall back to the current counter value
    read without incrementing."""
    if parent_run_id:
        with db.session() as s:
            parent = s.get(db.Run, parent_run_id)
            if parent is not None and parent.stats_json:
                try:
                    trials = json.loads(parent.stats_json)["honesty_report"]["dsr"]["trials"]
                    return max(int(trials), 1)
                except Exception:
                    pass
    with db.session() as s:
        row = s.get(db.TrialCounter, family)
        return max(row.trials if row else 1, 1)


def _run_label(run: db.Run) -> str | None:
    """V-155/V-160: how a person recognises a run — the Library's own name
    (summary_json), falling back to spec meta.name only when no summary
    exists. Shared by the variant endpoint and the lineage header so the
    screen and the Library can never disagree about what a run is called."""
    if run.summary_json:
        try:
            name = (json.loads(run.summary_json) or {}).get("name")
            if name:
                return str(name)
        except Exception:
            # a corrupt summary must not look like a legitimately unnamed run:
            # this feeds the variant framing, the back link and the lineage
            # header, so it degrades three surfaces at once and silently
            log.warning("unreadable summary_json on run %s", run.id, exc_info=True)
    try:
        return (json.loads(run.spec_json).get("meta") or {}).get("name")
    except Exception:
        log.warning("unreadable spec_json on run %s", run.id, exc_info=True)
        return None


def _is_ordinal_collision(exc: IntegrityError) -> bool:
    """Did THIS integrity error come from uq_runs_variant_ordinal?

    Postgres names the constraint on the diagnostics object; SQLite names the
    columns instead ("UNIQUE constraint failed: runs.root_run_id,
    runs.variant_ordinal") and carries no constraint name at all, so both
    engines need their own read. Anything else — a credit-ledger partial index,
    a duplicate run id — is NOT this race and must not be reported as one.
    """
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    if getattr(diag, "constraint_name", None) == "uq_runs_variant_ordinal":
        return True
    text = str(getattr(exc, "orig", exc))
    return "root_run_id" in text and "variant_ordinal" in text


def _next_ordinal(s: Any, root: str) -> int:
    """The next variant_ordinal in a root's family, computed INSIDE the
    debit transaction. Deliberately a seam: the V-172 retry test feeds it a
    stale value to force the collision the unique index then catches."""
    from sqlalchemy import func

    current = (
        s.query(func.max(db.Run.variant_ordinal))
        .filter(db.Run.root_run_id == root)
        .scalar()
    )
    return int(current or 0) + 1


def _inherit_min_trades(parent_run_id: str | None) -> int:
    """Evidence bar for an AUTOMATIC re-run (auto-unlock / receipt): the
    bar its parent was scored at — an unlock promised at the parent's bar
    must not silently re-refuse (or over-bless) at a different one. Falls
    back to the standard bar when the parent predates the setting."""
    if parent_run_id:
        with db.session() as s:
            parent = s.get(db.Run, parent_run_id)
            if parent is not None and parent.stats_json:
                try:
                    sample = json.loads(parent.stats_json)[
                        "honesty_report"]["regime_sample"]
                    return max(int(sample.get("min_trades", MIN_TRADES)), 1)
                except Exception:
                    pass
    return MIN_TRADES


def _execute_run(run_id: str, auto_note: str | None = None,
                 min_trades: int = MIN_TRADES) -> None:
    """Background job: serialize on the engine lock, run one gauntlet, then
    return freed memory to the OS regardless of outcome. The LLM narration
    runs AFTER the lock releases — it is pure network I/O (2–5 minutes at
    worst on OpenRouter retries) and must hold neither the user's result
    nor the next queued engine run hostage."""
    with ENGINE_LOCK:
        try:
            _run_and_store(run_id, auto_note, min_trades)
        finally:
            release_memory()
    _narrate_and_patch(run_id)


def _run_and_store(run_id: str, auto_note: str | None = None,
                   min_trades: int = MIN_TRADES) -> None:
    """Load the lake, run the engine + gauntlet, store the payload."""
    with db.session() as s:
        run = s.get(db.Run, run_id)
        if run is None:
            return
        run.status = "running"
        run.stage = 0
        s.add(db.RunEvent(run_id=run_id, stage=0, label="backtest running"))
        s.commit()
        spec_json = run.spec_json
        origin = run.origin or "user"
        parent_run_id = run.parent_run_id

    store = None
    try:
        spec = StrategySpec.model_validate(json.loads(spec_json))
        from app.data.chains import load_market_store
        from app.engine.runner import run_backtest
        from app.honesty.gauntlet import run_gauntlet
        from app.honesty.verdict import retail_template_verdict, template_verdict

        store = load_market_store(spec.underlying.ticker.value)
        intraday = None
        if spec.backtest.clock.value != "daily":
            from app.data.intraday import load_intraday_store

            intraday = load_intraday_store(spec.underlying.ticker.value)
            # run-START refresh, exactly once: the run and every gauntlet
            # sub-run then see ONE session listing (determinism rule)
            intraday.refresh_sessions()

        def _progress(done: int, total: int) -> None:
            # a full-history 5-min run takes minutes; prove life in the
            # event log so a stuck stage is distinguishable from work
            with db.session() as ps:
                ps.add(db.RunEvent(run_id=run_id, stage=0,
                                   label=f"simulating — {done}/{total} sessions"))
                ps.commit()

        engine_t0 = time.monotonic()
        result = run_backtest(spec, store, intraday, progress=_progress)
        engine_seconds = time.monotonic() - engine_t0

        # every HUMAN attempt at a family is a trial — the multiple-testing
        # bias the deflated Sharpe corrects for (TECH-SPEC §6.5). AUTO
        # re-runs are the same spec on more data — no new choice was made,
        # so they inherit the parent's count instead of bumping (owner
        # decision; docs/HONESTY.md).
        family = f"{spec.underlying.ticker.value}:{spec.position.structure.value}"
        if origin in ("auto_unlock", "receipt"):
            trials = _inherit_trials(parent_run_id, family)
        else:
            trials = db.bump_trials(family)

        previews: list[dict[str, str]] = []

        def on_stage(stage: int, label: str, preview: dict[str, str] | None = None) -> None:
            if preview:
                previews.append(preview)
            with db.session() as s2:
                r2 = s2.get(db.Run, run_id)
                if r2 is not None:
                    r2.stage = stage
                    r2.previews_json = json.dumps(previews)
                    s2.add(db.RunEvent(run_id=run_id, stage=stage, label=label))
                    s2.commit()

        gauntlet_t0 = time.monotonic()
        report = run_gauntlet(spec, store, result, trials=trials, on_stage=on_stage,
                              intraday=intraday, min_trades=min_trades)
        gauntlet_seconds = time.monotonic() - gauntlet_t0
        # D3a: refused verdicts store their unlock needs structured — the
        # nightly auto-unlock scan reasons from these
        from app.honesty.stages import unlock_conditions

        unlock = unlock_conditions(report, spec)
        # Deterministic templates ship the run NOW — grounded by
        # construction, same numbers the LLM would narrate. The narration
        # upgrade happens in _narrate_and_patch, off the critical path
        # (owner ask 2026-07-14: this stage stalled 2–5 min on LLM retries).
        verdict_t0 = time.monotonic()
        verdict = template_verdict(report)
        retail_verdict = retail_template_verdict(report)
        verdict_seconds = time.monotonic() - verdict_t0
        # forward-record disclosure: convention seams the window crossed
        from app.engine.engine import data_provenance

        provenance = data_provenance(
            spec, store, result.effective_start, result.effective_end)
        payload = build_run_payload(run_id, spec, result, report, verdict,
                                    retail_verdict, data_provenance=provenance)
        # the UI polls this flag briefly and swaps the wording in when the
        # async narration lands; without a key there is nothing to wait for.
        # The start stamp bounds the wait: a worker killed mid-narration
        # would otherwise strand the flag true forever (review finding) —
        # get_run clears it once the attempt is provably dead.
        payload["narrationPending"] = bool(os.environ.get("OPENROUTER_API_KEY"))
        if payload["narrationPending"]:
            payload["narrationStartedAt"] = datetime.now(UTC).isoformat()
        # the stats bundle is the ONLY material grounded Q&A may quote from
        stats = {
            "metrics": result.metrics,
            "filled": result.filled,
            "skipped": result.skipped,
            "initial_capital": spec.backtest.initial_capital,
            "final_equity": result.equity[-1] if result.equity else None,
            "honesty_report": report.model_dump(),
            # FX.4: the per-run resolution mix rides the stats bundle so a
            # future receipt comparing two resolution-carrying runs can name
            # an upgrade (empty on daily runs — they have no mix)
            "resolutionMix": result.resolution_mix or None,
        }
        # measured run cost — the pre-run time estimates are medians over
        # these rows (per clock, on THIS box), never invented numbers
        perf = {
            "clock": spec.backtest.clock.value,
            "sessions": report.coverage.chain_sessions,
            "engine_s": round(engine_seconds, 2),
            "gauntlet_s": round(gauntlet_seconds, 2),
            # the BLOCKING verdict cost the user actually waits on (template
            # assembly — the LLM narration moved off the critical path and
            # records narration_s when its upgrade lands). The estimate adds
            # this so wall-clock predictions stay honest.
            "verdict_s": round(verdict_seconds, 2),
            # marks the NEW verdict_s semantics — pre-change rows measured
            # the blocking LLM narration (minutes) and must not inflate the
            # estimate's verdict constant now that nothing blocks on it
            "narration_off_path": True,
            "conditions": bool(spec.entry.conditions),
        }
        with db.session() as s:
            run = s.get(db.Run, run_id)
            if run is None:
                return
            run.status = "done"
            run.stage = 6
            run.perf_json = json.dumps(perf)
            # UX Chunk A section 4: measured mechanics complete the setup
            # story written at creation. Isolated: a paperwork failure must
            # never error a computed run — the verdict outranks the diary.
            try:
                run.provenance_json = attach_mechanics(
                    run.provenance_json,
                    mechanics_record(perf, result, spec.spec_version),
                    origin, parent_run_id,
                )
            except Exception:
                log.exception("provenance mechanics attach failed for %s", run_id)
            if origin == "auto_unlock":
                payload["meta"] += " · auto-upgraded" + (f" ({auto_note})" if auto_note else "")
            run.payload_json = json.dumps(payload)
            run.stats_json = json.dumps(stats)
            run.unlock_json = unlock.model_dump_json() if unlock else None
            created = run.created_at.strftime("%b %-d ’%y") if run.created_at else ""
            summary = run_summary(run_id, payload, created)
            if origin == "auto_unlock":
                summary["upgradeOf"] = parent_run_id
                summary["autoNote"] = (
                    f"re-ran automatically — {auto_note}" if auto_note
                    else "re-ran automatically on new data"
                )
            elif origin == "receipt":
                summary["upgradeOf"] = parent_run_id
                summary["autoNote"] = "5-min replay (verdict receipt)"
            run.summary_json = json.dumps(summary)
            s.add(db.RunEvent(run_id=run_id, stage=6, label="gauntlet complete"))
            # the superseded refusal points forward to its upgrade
            if origin == "auto_unlock" and parent_run_id:
                parent = s.get(db.Run, parent_run_id)
                if parent is not None and parent.summary_json:
                    try:
                        psum = json.loads(parent.summary_json)
                        psum["supersededBy"] = run_id
                        parent.summary_json = json.dumps(psum)
                    except Exception:
                        pass
            # D3c: a completed replay writes its receipt onto the ORIGINAL
            # run — appended, never overwriting the stored verdict
            if origin == "receipt" and parent_run_id:
                parent = s.get(db.Run, parent_run_id)
                if parent is not None and parent.stats_json:
                    try:
                        from app.api.replay import build_receipt

                        receipt = build_receipt(
                            run_id,
                            json.loads(parent.stats_json),
                            stats,
                            payload,
                            datetime.now(UTC).isoformat(),
                        )
                        existing = (
                            json.loads(parent.receipts_json)
                            if parent.receipts_json else []
                        )
                        existing.append(receipt)
                        parent.receipts_json = json.dumps(existing)
                    except Exception:
                        log.exception("receipt attach failed for %s", parent_run_id)
            # L2 credit law: a refusal refunds — you only pay for a GRADED
            # verdict. Written in THIS transaction (with status='done'), so the
            # run becomes visible and refunded ATOMICALLY — a concurrent
            # read-time re-grade can never catch it in an un-refunded window
            # (the paywall SEAL keys on the refund). Idempotent + self-scoped
            # (a no-op for anon / service runs that were never charged).
            if bool(payload.get("verdict", {}).get("refusal")):
                db.refund_run_tx(s, run_id)
            s.commit()
    except Exception as exc:
        log.exception("run %s failed", run_id)
        with db.session() as s:
            run = s.get(db.Run, run_id)
            if run is not None:
                run.status = "error"
                run.error = f"{type(exc).__name__}: {exc}"
                s.add(db.RunEvent(run_id=run_id, stage=run.stage, label="failed"))
                # an our-fault failure refunds too — atomic with status='error'
                db.refund_run_tx(s, run_id)
                s.commit()
    finally:
        # this run's daily-series memo, dropped on the store THIS run used
        # (it is cached for 30 minutes across runs — chains.STORE_TTL_SECONDS
        # — so the transient must not ride along). Error paths too.
        if store is not None:
            store.drop_daily_series_cache()


def _patch_perf_narration(run: db.Run, narration_seconds: float | None) -> None:
    """Record the measured narration time on the run's perf row — the ONE
    writer for narration_s, shared by the success and fallback paths."""
    if narration_seconds is None or not run.perf_json:
        return
    try:
        perf = json.loads(run.perf_json)
        perf["narration_s"] = round(narration_seconds, 2)
        run.perf_json = json.dumps(perf)
    except Exception:
        pass


def _clear_narration_pending(run_id: str,
                             narration_seconds: float | None = None) -> None:
    """The narration attempt is over (failed, or template stood) — stop the
    UI's brief upgrade poll and record the measured narration time."""
    with db.session() as s:
        run = s.get(db.Run, run_id)
        if run is None or not run.payload_json:
            return
        payload = json.loads(run.payload_json)
        if payload.get("narrationPending"):
            payload["narrationPending"] = False
            run.payload_json = json.dumps(payload)
        _patch_perf_narration(run, narration_seconds)
        s.commit()


def _narrate_and_patch(run_id: str) -> None:
    """LLM narration OFF the run's critical path (owner ask 2026-07-14: the
    'honest verdict' stage stalled 2–5 minutes on OpenRouter retries). The
    run is already stored done with grounded template verdicts; when the
    narration clears the numeric + English validators it swaps the WORDING
    in place — same numbers, same trust, better words. Any failure leaves
    the template standing, exactly like the old inline fallback."""
    with db.session() as s:
        run = s.get(db.Run, run_id)
        if run is None or run.status != "done" or not run.payload_json:
            return
        payload = json.loads(run.payload_json)
        if not payload.get("narrationPending"):
            return  # keyless run, or a pre-async payload
        stats_json = run.stats_json
    stats = json.loads(stats_json) if stats_json else {}
    report_doc = stats.get("honesty_report")
    if not isinstance(report_doc, dict):
        _clear_narration_pending(run_id)
        return
    narration_t0 = time.monotonic()
    try:
        from app.honesty.report import HonestyReport
        from app.honesty.verdict import write_verdicts

        report = HonestyReport.model_validate(report_doc)
        verdict, retail_verdict = write_verdicts(report)
    except Exception:
        log.exception("narration failed for %s — the template verdict stands",
                      run_id)
        _clear_narration_pending(run_id)
        return
    narration_seconds = time.monotonic() - narration_t0
    if verdict.source != "llm" and retail_verdict.source != "llm":
        # every attempt fell back to the template already stored
        _clear_narration_pending(run_id, narration_seconds)
        return
    from app.api.payload import apply_verdict_text, run_summary

    with db.session() as s:
        run = s.get(db.Run, run_id)
        if run is None or run.status != "done" or not run.payload_json:
            return
        payload = json.loads(run.payload_json)
        payload = apply_verdict_text(payload, report, verdict, retail_verdict)
        payload["narrationPending"] = False
        run.payload_json = json.dumps(payload)
        _patch_perf_narration(run, narration_seconds)
        # the library card quotes the headline — keep it in the narrated
        # voice, formatted by the same run_summary the initial store used
        # (only the quote fields move; upgradeOf/autoNote markers stay)
        if run.summary_json:
            try:
                summary = json.loads(run.summary_json)
                fresh = run_summary(run_id, payload, created="")
                summary["quote"] = fresh["quote"]
                summary["quoteRetail"] = fresh["quoteRetail"]
                run.summary_json = json.dumps(summary)
            except Exception:
                pass
        s.commit()


@router.post("/parse")
def parse(req: ParseRequest) -> dict[str, Any]:
    if not req.text.strip():
        raise HTTPException(status_code=422, detail="empty strategy text")

    from app.parser.parse import ParserUnavailableError, parse_strategy, spec_to_draft

    answers = {str(k): str(v) for k, v in (req.answers or {}).items()}
    try:
        outcome = parse_strategy(req.text, answers or None)
    except ParserUnavailableError as exc:
        # upstream failed or the parse budget ran out — a retryable error,
        # reported as one; never a fake "clarifying question" (it rendered as
        # "QUESTION 1 OF 1 — I DON'T GUESS" and entered the provenance record)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if outcome is None:
        raise HTTPException(status_code=501, detail=_PENDING_PARSE_KEY)
    if outcome.status == "questions":
        return {
            "status": "questions",
            "demo": False,
            "questions": [q.model_dump() for q in outcome.questions],
        }
    assert outcome.spec is not None
    return {
        "status": "spec",
        "demo": False,
        "draft": spec_to_draft(outcome.spec, req.text.strip()),
        "spec": outcome.spec,
    }


@router.post("/backtest")
def backtest(
    req: BacktestRequest,
    tasks: BackgroundTasks,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    # launch L1b: runs belong to the account that started them. Resolution
    # is lazy and optional — service/automation and anonymous runs stamp
    # NULL, which stays claimable at signup. The verified-email bar applies
    # to signed-in people only, and only once the owner flips
    # SKEPTIC_REQUIRE_VERIFIED (needs a configured mail sender).
    from app import auth

    # a session presented but unresolvable (accounts DB on the SQLite
    # fallback) is a likely signed-in person we can't validate right now —
    # remembered so the anon armor doesn't clamp/Turnstile-gate a real account
    # mid-outage (a bogus cookie under NORMAL operation still resolves to None
    # and IS armored)
    session_seen = auth.session_presented(request)
    accounts_down = False
    try:
        run_user = auth.resolve_user(request)
    except auth.AccountsUnavailableError:
        run_user = None  # runs-DB fallback still accepts system work
        accounts_down = True
    if (
        run_user is not None
        and os.environ.get("SKEPTIC_REQUIRE_VERIFIED") == "1"
        and run_user.verified_at is None
    ):
        raise HTTPException(
            status_code=403,
            detail="verify your email to run backtests — resend the link "
            "from your account",
        )
    try:
        spec = StrategySpec.model_validate(req.spec)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc
    if req.seed is not None:
        spec.backtest.seed = req.seed

    if req.origin not in VALID_ORIGINS:
        raise HTTPException(status_code=422, detail=f"unknown origin {req.origin!r}")
    # a signed-in caller's run is ALWAYS origin="user" (launch L2): the
    # automation origins (auto_unlock / receipt) belong to the nightly
    # principal, which carries the service bearer. Forcing it server-side also
    # stops a session caller from dodging the credit debit by declaring an
    # automation origin (the field is client-supplied).
    origin = "user" if run_user is not None else req.origin

    # ---- variant gates (V-167: THIS ORDER IS THE CONTRACT) -----------------
    # A variant is a HUMAN copy: origin "user" carrying parent_run_id. The
    # automatic origins re-run the SAME spec by design (HONESTY.md D3b), so
    # the zero-edit guard must never see them. Order: lock check first,
    # zero-edit guard second, and only then the debit — a rejection here
    # happens BEFORE the debit exists in any form, not rolled back after.
    variant_root: str | None = None
    variant_diff: list[dict[str, Any]] | None = None
    if (
        req.parent_run_id
        and origin == "user"
        # same predicate as `is_anon` below, which is defined after the
        # anon-armor block; the gate has to run BEFORE the debit, so it
        # cannot wait for that binding
        and run_user is None
        and not auth.is_service(request)
    ):
        # V-04 said the button "shows and routes to signup" — that was only
        # ever enforced in the client, and `origin` defaults to "user", so an
        # anonymous POST carrying parent_run_id walked straight into the
        # variant path: free (charge_credit is False for anon), and stamping
        # lineage into someone's family with user_id NULL. The API now declines
        # what the UI declines to offer, in the same words.
        raise HTTPException(
            status_code=402,
            detail="create a free account to run a variant",
        )
    if origin == "user" and req.parent_run_id:
        from app.api.variant import classify, diff_specs, locked_field_paths

        with db.session() as s:
            vparent = s.get(db.Run, req.parent_run_id)
            if vparent is None or not vparent.spec_json:
                raise HTTPException(
                    status_code=404, detail=f"run {req.parent_run_id} not found"
                )
            _enforce_run_access(vparent, req.parent_run_id, request)
            parent_spec = json.loads(vparent.spec_json)
            variant_root = vparent.root_run_id or vparent.id
        rep = classify(parent_spec)
        if rep.blocks:
            # V-128 posture at the submit boundary too: the real reason
            raise HTTPException(
                status_code=422,
                detail="this run cannot be reopened as a variant: "
                + "; ".join(rep.reasons.values()),
            )
        variant_diff = diff_specs(parent_spec, json.loads(spec.model_dump_json()))

        # 1) V-22 / V-168: locked fields, named in the dial's own register.
        # A doctored client gets the same honest sentence a confused
        # legitimate one would.
        def _hits(field: str, path: str) -> bool:
            return field == path or field.startswith((path + ".", path + "["))

        # V-168: the reason comes from the DIAL that produced the locked path,
        # so a lock added later cannot inherit a sentence about the strike.
        # The identity locks share one explanation; every other lock is a
        # tier (b) dial and speaks rep.reasons in the dial's own words.
        _IDENTITY_WHY = (
            "ticker and structure define the strategy family the trial "
            "counter tracks — changing them is a New Analysis, not a variant"
        )
        _PATH_DIAL = {
            "underlying.ticker": "ticker",
            "position.structure": "structure",
            "position.legs": "strike",
        }
        for row in variant_diff:
            for path in locked_field_paths(rep):
                if not _hits(row["field"], path):
                    continue
                dial = _PATH_DIAL.get(path)
                if dial in ("ticker", "structure"):
                    why = _IDENTITY_WHY
                elif dial and dial in rep.reasons:
                    why = (
                        f"the parent chose its {dial} as {rep.reasons[dial]}, "
                        "which the dial cannot express"
                    )
                else:
                    # an unmapped lock path: refuse without inventing a cause
                    log.error("locked path %s has no reason mapping", path)
                    why = "it is locked on a variant and this run changed it"
                raise HTTPException(
                    status_code=422,
                    detail=f"{row['field']} is locked on this variant: {why}",
                )

        # 2) V-10 / V-19 / V-169: the zero-edit guard, both causes named.
        if not variant_diff:
            raise HTTPException(
                status_code=409,
                detail="Nothing changed. This would be the same run. "
                f"See /runs/{req.parent_run_id}.",
            )
        claims_untouched = bool(
            ((req.provenance or {}).get("confirmed") or {}).get("untouched")
        )
        if claims_untouched:
            drifted = ", ".join(r["field"] for r in variant_diff[:5])
            raise HTTPException(
                status_code=422,
                detail="zero-edit mismatch: no dial was touched, yet the "
                f"rebuilt spec differs from the parent at {drifted} — this "
                "is the lossy rebuild resurfacing (V-19), a defect, not "
                "your edit; no credit was spent",
            )

    # launch L4 anon armor: the anonymous free-run path is defended so a
    # doctored client can't turn the engine into free compute. Signed-in
    # users and the service principal skip ALL of this. Everything that can
    # reject runs BEFORE the run row is created; the trial is recorded after.
    anon_token_h: str | None = None
    anon_ip_h: str | None = None
    # anonymous = no account we can act for AND not the service principal. It
    # deliberately does NOT key on req.origin: an anon POSTing origin=auto_unlock
    # (VALID_ORIGINS, no service bearer) must be ARMORED, not waved through —
    # the only legitimate auto_unlock/receipt caller is the nightly principal,
    # which carries the service bearer and is excluded by not is_service.
    is_anon = run_user is None and not auth.is_service(request)
    # a session presented but unresolvable (accounts DB on the SQLite fallback)
    # is a likely signed-in person we can't validate right now. The DB-free
    # layers below (the human check + the fast-path constraint) STILL apply to
    # them — so an outage is never a bot-flushable free-compute hole — but the
    # per-device DB limits (token/IP/budget) relax, so we don't one-run-block a
    # real account mid-outage. A bogus cookie under NORMAL operation resolves to
    # None (no exception) → outage_session is False → the full armor applies.
    outage_session = accounts_down and session_seen
    if is_anon:
        from app import anon

        ip = anon.client_ip(request)
        if not anon.verify_turnstile(req.turnstile_token, ip):
            raise HTTPException(
                status_code=403,
                detail="the human check didn't pass — please try again",
            )
        anon.enforce_constraints(spec)  # daily clock + <=3y window, or 422
        if not outage_session:
            anon_token_h = anon.verified_hash(request.cookies.get(anon.ANON_COOKIE))
            anon_ip_h = anon.ip_hash(ip)
            verdict = anon.check_limits(anon_token_h, anon_ip_h)
            if verdict == "budget":
                raise HTTPException(
                    status_code=402,
                    detail="free trials are busy right now — create a free "
                    "account for 5 backtests, no card",
                )
            if verdict in ("used_token", "used_ip"):
                raise HTTPException(
                    status_code=402,
                    detail="you've used this device's free backtest — create a "
                    "free account for 5 more, no card",
                )
            if anon_token_h is None:  # first run from this device — mint a token
                raw_token, anon_token_h = anon.new_token()
                response.set_cookie(
                    anon.ANON_COOKIE,
                    raw_token,
                    max_age=60 * 60 * 24 * 365,
                    httponly=True,
                    secure=True,
                    samesite="lax",
                    path="/",
                )

    # the evidence bar: the caller's setting; automatic re-runs without one
    # inherit their parent's so an unlock never moves its own goalposts
    if req.min_trades is not None:
        min_trades = req.min_trades
    elif origin in ("auto_unlock", "receipt"):
        min_trades = _inherit_min_trades(req.parent_run_id)
    else:
        min_trades = MIN_TRADES

    # launch L2 credits: a signed-in caller spends 1 credit per run. The anon
    # path is defended by the armor (no credits); the service principal is
    # never charged. Hard block at 0 (L3 adds top-ups).
    charge_credit = run_user is not None and not auth.is_service(request)
    run_id = uuid.uuid4().hex[:12]
    note = (req.auto_note or "")[:AUTO_NOTE_MAX] or None

    # Built ONCE, outside the ordinal retry loop: nothing in it depends on the
    # attempt, and rebuilding it per attempt would log the V-204 tally twice for
    # a single run.
    provenance_blob = creation_record(
        req.provenance, origin, req.parent_run_id, note, what_changed=variant_diff,
    )
    if variant_diff:
        # V-204: the reconciler's misses are counted, never swallowed, and they
        # are reported WITH the total they came from — a bare miss count reads
        # as coverage, and exchanges are already a lossy sample of the triggers
        # that fired, since the parser caps a round at four questions.
        #
        # This is the tally that decides whether V-57 is worth doing: it is the
        # only measure of how often value-matching cannot explain a carried
        # exchange. It goes to the log, never to the UI (a user seeing "we could
        # not map your question" learns nothing they can act on).
        stored = json.loads(provenance_blob)
        counts = (stored.get("reconcile_telemetry") or {}).get("counts")
        if counts:
            # V-214: telemetry, and the wording says so. "would have fired" is
            # not hedging — nothing renders, so a match is a hypothetical, and
            # a log line that read "superseded: 1" would be the same false
            # claim as the marker, written somewhere a future reader trusts.
            log.info(
                "variant reconcile telemetry (nothing rendered): %d carried, "
                "%d would-have-fired, %d unmatched, %d suppressed, "
                "%d unparseable (parent %s)",
                counts["carried"], counts["superseded"], counts["unmatched"],
                counts["suppressed"], counts["unparseable"], req.parent_run_id,
            )
        # V-208: separate line, because it counts a different thing and fires on
        # runs the reconciler never sees. A variant whose parent recorded no
        # conversation has no exchanges to reconcile and still has fields to
        # label, and that is the common case rather than the edge (measured: of
        # 99 production runs, 9 carry a conversation at all).
        labeling = stored.get("labeling")
        if labeling and labeling["unlabeled"]:
            log.info(
                "variant labels: %d of %d changed fields have no label — "
                "add them to app/api/field_labels.py (parent %s)",
                labeling["unlabeled"], labeling["rows"], req.parent_run_id,
            )
    # V-172: the ordinal race (two tabs submitting variants of one root
    # at the same moment) retries ONCE with a fresh transaction — the
    # loser recomputes max+1 and lands the next ordinal. One run per
    # submit, no error surfaced on the legitimate double-submit path; the
    # 409 below remains only for a double collision, genuine contention.
    for _ordinal_attempt in (1, 2):
        with db.session() as s:
            if charge_credit and run_user is not None:  # 2nd clause narrows for mypy
                uid = run_user.id
                # lock THIS account's row (Postgres) so two simultaneous runs
                # can't both spend the last credit, then recompute the balance
                # under the lock and debit + create in ONE transaction — a crash
                # between them leaves NEITHER (the atomicity guarantee). On the
                # SQLite fallback with_for_update is a no-op, so a concurrent
                # overdraft of 1 credit is possible there; acceptable, as the
                # fallback is degraded single-node mode and prod is Postgres.
                s.query(db.User).filter(db.User.id == uid).with_for_update().first()
                if db.credit_balance_tx(s, uid) <= 0:
                    raise HTTPException(
                        status_code=402,
                        detail="you're out of backtest credits — top-ups are coming soon",
                    )
                s.add(
                    db.CreditLedger(user_id=uid, delta=-1, reason="run_debit", run_id=run_id)
                )
            # V-167 step 3: lineage is stamped in the SAME transaction as the
            # debit. A crash between them must leave neither — a run with a debit
            # and no lineage is a variant that lost its parent, unrepairable.
            variant_ordinal: int | None = None
            if variant_root is not None:
                variant_ordinal = _next_ordinal(s, variant_root)
            s.add(
                db.Run(
                    id=run_id,
                    status="queued",
                    stage=0,
                    seed=spec.backtest.seed,
                    spec_json=spec.model_dump_json(),
                    origin=origin,
                    parent_run_id=req.parent_run_id,
                    root_run_id=variant_root,
                    variant_ordinal=variant_ordinal,
                    user_id=run_user.id if run_user is not None else None,
                    provenance_json=provenance_blob,
                )
            )
            try:
                s.commit()
                break
            except IntegrityError as exc:
                # the rollback discards the debit with the insert — nothing
                # half-written, whatever the cause turns out to be
                s.rollback()
                # Only the ordinal index may be BLAMED on the ordinal race, and
                # only after checking. Claiming a cause this never verified is
                # the same defect class as a verdict citing a number it does
                # not have: every other refusal here names a reason it
                # established first (db.was_refunded, the V-168 lock message).
                if not _is_ordinal_collision(exc):
                    raise
                # V-172: attempt 1 retries on a fresh transaction — the loser
                # of a cross-tab race recomputes max+1 and lands the next
                # ordinal, so a legitimate double submit sees ONE run and no
                # error. A second collision is genuine contention: honest 409.
                if _ordinal_attempt == 1:
                    log.warning(
                        "variant ordinal collision on root %s — retrying",
                        variant_root,
                    )
                    continue
                raise HTTPException(
                    status_code=409,
                    detail="another variant of this run landed at the same "
                    "moment — try again",
                ) from None
    # record the anon trial only after the run row exists, so a failed
    # creation never burns the visitor's one free run. Best-effort: a
    # trial-write hiccup must not 500 a run that already exists and is about
    # to execute — the per-IP window and global daily budget still bound abuse.
    if is_anon and anon_token_h is not None and anon_ip_h is not None:
        from app import anon

        try:
            anon.record_trial(anon_token_h, anon_ip_h, run_id)
        except Exception:  # noqa: BLE001 — never fail a created run on the audit write
            log.exception("anon trial write failed for run %s", run_id)
    tasks.add_task(_execute_run, run_id, note, min_trades)
    out: dict[str, Any] = {"run_id": run_id, "demo": False, "status": "queued"}
    if is_anon:
        from app import anon

        out["queuePosition"] = anon.queue_position(run_id)
        out["trialConstraint"] = "daily resolution · up to a 3-year window"
    return out


def example_run_ids() -> tuple[str, ...]:
    """The showcase runs every visitor sees (owner picks, 2026-07-17): a
    solid pass, a big winner, a destructive one, and a withheld verdict —
    the instrument's full range, all real. Env-overridable so re-pinning
    never needs a deploy."""
    raw = os.environ.get(
        "SKEPTIC_EXAMPLE_RUN_IDS",
        "612905835dca,2a4f48d6178e,7bbf2837653f,42ce700a376f",
    )
    return tuple(x.strip() for x in raw.split(",") if x.strip())


@router.get("/runs")
def list_runs(
    request: Request,
    scope: str = Query(default="examples"),
    include: str = Query(default="", max_length=1200),
) -> dict[str, Any]:
    """Library listing. Reads ONLY the small summary column — pulling 50
    full payloads (equity series and all) per listing is how a database
    transfer quota dies. Queued/running runs get an ephemeral summary so
    navigating away from the progress screen never 'loses' a run.

    Curation (owner 2026-07-17): a signed-in user's library is THEIR runs
    plus the pinned, badged examples; anonymous/pre-account callers get
    the examples plus the ids their device remembers (`include` — exactly
    the list signup re-parents). scope=all stays for the service principal
    and pre-flip owner use (nightly automation reads the DB directly)."""
    from app import auth

    try:
        viewer = auth.resolve_user(request)
    except auth.AccountsUnavailableError:
        viewer = None
    with db.session() as s:
        query = s.query(
            db.Run.id,
            db.Run.created_at,
            db.Run.status,
            db.Run.stage,
            db.Run.summary_json,
            db.Run.spec_json,
            # V-12: lineage rides the ROW, injected at read — never written
            # into summary_json, so old summaries and the narration rebuild
            # need no special-casing and pre-phase rows group correctly
            db.Run.root_run_id,
            db.Run.variant_ordinal,
        )
        # scope=all is the full cross-account listing — automation ONLY.
        # A non-service caller appending it (review finding: it was
        # unguarded) just gets the normal curated view, never everyone's
        # runs.
        examples_only = not (scope == "all" and auth.is_service(request))
        examples = set(example_run_ids())  # once — not per row (env parse)
        if examples_only:
            from sqlalchemy import ColumnElement, or_

            own = tuple(x.strip() for x in include.split(",") if x.strip())[:50]
            conds: list[ColumnElement[bool]] = [
                db.Run.id.in_(tuple(examples))  # public showcase, always
            ]
            if viewer is not None:
                # the account's own runs ride on OWNERSHIP, not breadcrumbs
                conds.append(db.Run.user_id == viewer.id)
            if own:
                # include= surfaces an anon device's OWN runs — which are
                # unowned. An owned run named in include stays private to
                # its account (review finding: include= leaked owned
                # summaries by id, contradicting get_run's 404)
                conds.append(db.Run.id.in_(own) & db.Run.user_id.is_(None))
            query = query.filter(or_(*conds))
        rows = (
            query.filter(db.Run.status.in_(["queued", "running", "done"]))
            .order_by(db.Run.created_at.desc())
            # the curated branch is already bounded by the id filter — a flat
            # 50 would trim the OLDER pinned examples out from under a heavy
            # user's 50 own runs (review finding)
            .limit(60 if examples_only else 50)
            .all()
        )
        runs: list[dict[str, Any]] = []
        backfilled = False
        for (run_id, created_at, status, stage, summary_json, spec_json,
             root_run_id, variant_ordinal) in rows:
            if status in ("queued", "running"):
                spec = json.loads(spec_json)
                created = created_at.strftime("%b %-d ’%y") if created_at else ""
                runs.append(
                    {
                        "id": run_id,
                        "demo": False,
                        "status": "running",
                        "stage": stage or 0,
                        "name": spec.get("meta", {}).get("name", run_id),
                        "meta": f"started {created}" if created else "in progress",
                        "quote": "",
                        "kind": "verdict",
                        **(
                            {"rootRunId": root_run_id, "variantOrdinal": variant_ordinal}
                            if variant_ordinal is not None
                            else {}
                        ),
                    }
                )
                continue
            if summary_json:
                summary = json.loads(summary_json)
                if run_id in examples:
                    summary["example"] = True
                if variant_ordinal is not None:
                    summary["rootRunId"] = root_run_id
                    summary["variantOrdinal"] = variant_ordinal
                runs.append(summary)
                continue
            # stored before summary_json existed — build once, persist, done
            run = s.get(db.Run, run_id)
            if run is None or not run.payload_json:
                continue
            created = created_at.strftime("%b %-d ’%y") if created_at else ""
            summary = run_summary(run_id, json.loads(run.payload_json), created)
            run.summary_json = json.dumps(summary)
            backfilled = True
            runs.append(summary)
        if backfilled:
            s.commit()
    return {"runs": runs, "demo": False}


def _enforce_run_access(run: db.Run, run_id: str, request: Request) -> None:
    """launch L1b: OWNED runs are private to their account (service and the
    pinned examples excepted; unowned pre-account runs stay reachable by id —
    that's how an anonymous device revisits its own run). 404, not 403 —
    existence is nobody else's business. Shared by get_run / ask / replay so
    reading, questioning, and receipting a run all enforce the SAME boundary
    (ask + replay were missing it — a cross-user IDOR on paid graded runs)."""
    if run.user_id is None or run_id in example_run_ids():
        return
    from app import auth

    if auth.is_service(request):
        return
    try:
        viewer = auth.resolve_user(request)
    except auth.AccountsUnavailableError:
        viewer = None
    if viewer is None or viewer.id != run.user_id:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    request: Request,
    min_trades: int | None = Query(default=None, ge=1, le=10_000),
) -> dict[str, Any]:
    with db.session() as s:
        run = s.get(db.Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    _enforce_run_access(run, run_id, request)
    if run.status == "done" and run.payload_json:
        payload = dict(json.loads(run.payload_json))
        # a worker killed mid-narration must not leave the UI polling a
        # forever-pending flag — release it once the attempt is stale
        payload = _release_stale_narration(run_id, payload)
        # D3c: receipts arrive AFTER the payload froze — merged at read
        # time; the stored verdict/trust inside the payload is untouched
        if run.receipts_json:
            try:
                payload["receipts"] = json.loads(run.receipts_json)
            except Exception:
                pass
        # F7: the fill audit also arrives after the payload froze
        if run.audit_json:
            try:
                payload["fillAudit"] = json.loads(run.audit_json)
            except Exception:
                pass
        # 2026-07-14: the evidence bar is a user setting — a stored run
        # re-grades at read time against the caller's bar (both ways:
        # a 13-trade refusal unlocks at bar 1, a graded run re-caps at
        # 300). Per-request view; the stored row is never mutated.
        # L2 SEAL: a REFUNDED refusal must NOT unlock at a lower bar — the
        # credit was given back, so blessing it now would be a free graded
        # verdict (submit at min_trades=10000 to force a refund, then view at
        # ?min_trades=1 to unlock it — a full paywall bypass). Only a stored
        # refusal can be unlocked downward, so the ledger check is scoped to
        # that case (graded and anon/unpaid runs re-grade freely).
        if min_trades is not None and run.stats_json:
            from app.api.payload import regrade_for_min_trades

            stored_refusal = bool(payload.get("verdict", {}).get("refusal"))
            if not (stored_refusal and db.was_refunded(run_id)):
                try:
                    stats = json.loads(run.stats_json)
                except Exception:
                    stats = None
                payload = regrade_for_min_trades(payload, stats, min_trades)
        spec_dict = json.loads(run.spec_json) if run.spec_json else {}
        # UX Chunk A: the setup story. Stored records merge verbatim; rows
        # predating the column get a READ-TIME derivation from stored fields
        # (owner amendment 2026-07-14) — nothing is written back, and the
        # never-stored conversation is never invented. A corrupt record must
        # not take the run screen down: degrade to no key.
        try:
            if run.provenance_json:
                payload["provenance"] = json.loads(run.provenance_json)
            else:
                payload["provenance"] = derived_record(
                    spec_dict,
                    json.loads(run.perf_json) if run.perf_json else None,
                    json.loads(run.stats_json) if run.stats_json else None,
                    run.origin,
                    run.parent_run_id,
                )
        except Exception:
            log.exception("provenance merge failed for %s", run_id)
        # V-12: the lineage header — "Variant N, from <parent>", parent and
        # root both linkable, parent named the way the Library names it
        # (V-155). A deleted parent keeps the lineage and says so (V-45).
        if run.variant_ordinal is not None:
            with db.session() as s:
                vparent = s.get(db.Run, run.parent_run_id) if run.parent_run_id else None
            payload["variant"] = {
                "ordinal": run.variant_ordinal,
                "parent": {
                    "id": run.parent_run_id,
                    "label": _run_label(vparent) if vparent else None,
                    "deleted": run.parent_run_id is not None and vparent is None,
                },
                "root": {"id": run.root_run_id},
            }
        from app.api.replay import replay_eligible_spec

        payload["replayEligible"] = (
            (run.origin or "user") == "user" and replay_eligible_spec(spec_dict)
        )
        # owner 2026-07-17: the two pinned showcase runs say so, explicitly —
        # a stranger must never mistake an example for their own result
        if run_id in example_run_ids():
            payload["example"] = True
        return payload
    if run.status == "error":
        return {"id": run_id, "demo": False, "status": "error",
                "error": run.error or "run failed", "stage": run.stage}
    spec = json.loads(run.spec_json)
    return {
        "id": run_id,
        "demo": False,
        "status": "running",
        "stage": run.stage,
        "name": spec.get("meta", {}).get("name", run_id),
        # real intermediate stats from finished stages — the progress teasers
        "previews": json.loads(run.previews_json) if run.previews_json else [],
    }


class AskRequest(BaseModel):
    question: str
    verbiage: str | None = None  # "institutional" (default) | "retail"
    # the viewer's evidence bar — Q&A must describe the SAME verdict the
    # screen shows when a stored run was re-graded at read time
    min_trades: int | None = Field(default=None, ge=1, le=10_000)


@router.get("/runs/{run_id}/variant")
def variant_draft(run_id: str, request: Request) -> dict[str, Any]:
    """V-08 / V-20 / V-28: everything the spec screen needs to reopen THIS run
    as a variant, projected server-side from the stored spec.

    Costs nothing and commits to nothing (V-09) — the credit is debited at
    submit, atomically with run creation, exactly as for any other run.

    Works for every run the caller owns, including refused ones (V-03): the
    usual fix for a refusal is widening the window, which is precisely what
    this button is for.
    """
    from app.api.variant import build_variant_draft, classify, locked_field_paths

    with db.session() as s:
        run = s.get(db.Run, run_id)
        if run is None or not run.spec_json:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        _enforce_run_access(run, run_id, request)
        spec = json.loads(run.spec_json)
        provenance = json.loads(run.provenance_json) if run.provenance_json else None
        stats = json.loads(run.stats_json) if run.stats_json else None
        root_id = run.root_run_id or run_id
        parent_ordinal = run.variant_ordinal
        # V-155: no screen on the variant path shows a raw run id to a person.
        parent_label = _run_label(run)

    rep = classify(spec)
    parent = {
        "id": run_id,
        "rootId": root_id,
        "ordinal": parent_ordinal,
        "label": parent_label,
    }

    if rep.blocks:
        # V-128: the refusal names the ACTUAL reason, never a generic error,
        # in the same plain register as the tier (b) read-only dial.
        # V-129: zero in 99 is today's measurement, not a permanent property,
        # so the first real occurrence announces itself rather than arriving
        # as a support message.
        log.warning(
            "variant blocked: tier c run=%s reasons=%s", run_id, rep.reasons
        )
        return {"parent": parent, "draft": None, "spec": None, **rep.as_dict()}

    return {
        "parent": parent,
        "draft": build_variant_draft(run_id, spec, provenance, stats, parent_label),
        # the rebuild base, so parser-only vocabulary survives a dial edit
        "spec": spec,
        # V-05: carried verbatim and NOT re-asked. Absent on a run that never
        # stored one, and never invented (V-28).
        "prompt": (provenance or {}).get("prompt") or None,
        "conversation": (provenance or {}).get("conversation") or [],
        "lockedPaths": locked_field_paths(rep),
        **rep.as_dict(),
    }



class ArgueBackRequest(BaseModel):
    """The candidate variant spec, as the dials currently stand."""

    spec: dict[str, Any]


@router.post("/runs/{run_id}/argue-back")
def argue_back(run_id: str, req: ArgueBackRequest, request: Request) -> dict[str, Any]:
    """V-14: did the parent's sweep already run the edit the user is about to submit?

    Read-only and free. It reads the parent's STORED sweep and returns that cell's
    stored numbers, or nothing. No engine call, no credit, no run, no write — the
    whole point is that this answer already exists and nobody has to pay to see it
    again.

    POST rather than GET because it carries a spec, not because it changes anything.

    `{"hit": null}` is the ordinary answer and is not an error: it means the parent
    never ran this configuration, so there is nothing to say (V-231 prefers silence
    to a nearest-neighbour guess).
    """
    from app.api.argue_back import lookup

    with db.session() as s:
        run = s.get(db.Run, run_id)
        if run is None or not run.spec_json:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        _enforce_run_access(run, run_id, request)
        parent_spec = json.loads(run.spec_json)
        parent_stats = json.loads(run.stats_json) if run.stats_json else None
        # V-243: the SAME label the variant endpoint and the lineage header use
        parent_label = _run_label(run)

    return {"hit": lookup(parent_spec, parent_stats, req.spec, parent_label)}

@router.post("/runs/{run_id}/replay")
def replay_run(run_id: str, tasks: BackgroundTasks, request: Request) -> dict[str, Any]:
    """On-demand verdict receipt (D3c, owner amendment 1): replay THIS
    daily run at the 5-minute clock, right now. The receipt attaches to
    the original when the replay completes; the stored verdict is never
    rewritten."""
    from app.api.replay import build_replay_spec, replay_eligible_spec

    with db.session() as s:
        run = s.get(db.Run, run_id)
        if run is None or run.status != "done" or not run.spec_json:
            raise HTTPException(status_code=404, detail="no completed run to replay")
        _enforce_run_access(run, run_id, request)  # only the owner replays their run
        parent_user_id = run.user_id
        spec_dict = json.loads(run.spec_json)
    # a REFUNDED run's verdict is sealed (you got the credit back, not the
    # verdict). Replaying it would spawn a fresh, never-charged receipt run
    # that a lower-bar re-grade could unlock for free — the seal's escape
    # hatch. Block it. (An uncharged anon/example refused run has no such
    # paywall and can still be receipted.)
    if db.was_refunded(run_id):
        raise HTTPException(
            status_code=409,
            detail="nothing to replay — this run's verdict was withheld and "
                   "its credit refunded; there is no blessed result to receipt",
        )
    if not replay_eligible_spec(spec_dict):
        raise HTTPException(
            status_code=409,
            detail="not replayable: only daily-clock specs whose whole tenor "
                   "band fits the intraday slice (max_dte ≤ 2) can face the "
                   "5-minute record like-for-like",
        )
    try:
        replay_spec = StrategySpec.model_validate(build_replay_spec(spec_dict))
    except ValidationError as exc:
        raise HTTPException(status_code=409, detail=_validation_detail(exc, limit=3)) from exc

    new_id = uuid.uuid4().hex[:12]
    with db.session() as s:
        s.add(db.Run(id=new_id, status="queued", stage=0,
                     seed=replay_spec.backtest.seed,
                     spec_json=replay_spec.model_dump_json(),
                     origin="receipt", parent_run_id=run_id,
                     # inherit the parent's owner so the receipt is as private
                     # as the run it verifies (a receipt of an owned run must
                     # not be world-readable via its own id)
                     user_id=parent_user_id,
                     provenance_json=creation_record(None, "receipt", run_id)))
        s.commit()
    # the receipt faces the same evidence bar its parent was scored at
    tasks.add_task(_execute_run, new_id, None, _inherit_min_trades(run_id))
    return {"run_id": new_id, "demo": False, "status": "queued", "parent": run_id}


_AUDIT_RUNNING = "__running__"

# narration worst case is ~3 validated retries × 45s per register; a pending
# flag older than this survived a worker death mid-attempt — the template
# verdict stands and the UI's upgrade poll must be released
_NARRATION_STALE_MINUTES = 10


def _release_stale_narration(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """A narrationPending flag whose attempt is provably dead (no start
    stamp, or one past the stale horizon) is cleared — persisted, so every
    later read is cheap and the pollers stop."""
    if not payload.get("narrationPending"):
        return payload
    age_min = marker_age_minutes(payload.get("narrationStartedAt"),
                                 _NARRATION_STALE_MINUTES)
    if age_min < _NARRATION_STALE_MINUTES:
        return payload
    log.warning("run %s: narration attempt is stale (%.0f min) — the "
                "template verdict stands", run_id, age_min)
    _clear_narration_pending(run_id)
    return {**payload, "narrationPending": False}


@router.post("/runs/{run_id}/audit")
def audit_run(run_id: str, tasks: BackgroundTasks) -> dict[str, Any]:
    """On-demand fill audit (F7, owner decision 2026-07-08): re-run THIS
    spec deterministically over the ORIGINAL effective window and check
    every regenerated option-leg fill against Alpaca minute TRADES — a
    vendor no fill price came from. Stored like a receipt; the run's
    verdict is never rewritten. Repeated POSTs while one is in flight
    are refused (each audit is a full engine re-run behind the lock)."""
    claim_run_job(run_id, column="audit_json",
                  running_status=_AUDIT_RUNNING, verb="audit")
    tasks.add_task(_execute_audit, run_id)
    return {"run_id": run_id, "demo": False, "status": "auditing"}


def _execute_audit(run_id: str) -> None:
    try:
        with db.session() as s:
            run = s.get(db.Run, run_id)
            if run is None or not run.spec_json:
                return
            spec_doc = json.loads(run.spec_json)
            stats = json.loads(run.stats_json) if run.stats_json else {}
        from app.data import r2 as _r2
        from app.data.fill_audit import audit_fills

        # window pin + lock + refresh=False stores: the shared verification
        # scaffold (app.api.jobs) — the fill-count guard below is this job's
        # own drift check on top of it
        rerun = pinned_engine_rerun(spec_doc, stats)
        spec, result = rerun.spec, rerun.result
        # in-window lake drift is still possible (self-healing artifacts,
        # growing resolution maps): the regenerated run must reproduce the
        # ORIGINAL fill count or the audit refuses — attributing
        # independent verification to fills the run never made is the
        # worst class of bug on this product
        original_filled = stats.get("filled")
        if original_filled is not None and result.filled != original_filled:
            with db.session() as s:
                run = s.get(db.Run, run_id)
                if run is not None:
                    run.audit_json = json.dumps({
                        "error": (
                            f"audit refused: the lake has changed since this "
                            f"run — the deterministic re-run produced "
                            f"{result.filled} fills vs the original "
                            f"{original_filled}; the regenerated fills are "
                            f"not this run's fills"),
                        "generated_at": datetime.now(UTC).isoformat(),
                    })
                    s.commit()
            return
        ticker = spec.underlying.ticker.value
        s3 = _r2.r2_client()

        def _load_day(d: str) -> Any:
            return _r2.get_parquet(
                s3, f"options_minute/source=alpaca/ticker={ticker}"
                    f"/date={d}/bars.parquet")

        audit = audit_fills(result.fill_log, _load_day)
        audit["generated_at"] = datetime.now(UTC).isoformat()
        audit["fills_total"] = len(result.fill_log)
        with db.session() as s:
            run = s.get(db.Run, run_id)
            if run is not None:
                run.audit_json = json.dumps(audit)
                s.commit()
    except Exception:
        log.exception("fill audit failed for %s", run_id)
        with db.session() as s:
            run = s.get(db.Run, run_id)
            if run is not None:
                run.audit_json = json.dumps(
                    {"error": "audit failed — see server logs"})
                s.commit()


@router.post("/runs/{run_id}/ask")
def ask(run_id: str, req: AskRequest, request: Request) -> dict[str, Any]:
    if not req.question.strip():
        raise HTTPException(status_code=422, detail="empty question")
    with db.session() as s:
        run = s.get(db.Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    _enforce_run_access(run, run_id, request)  # a run's Q&A is as private as the run
    if run.status != "done" or not run.stats_json:
        raise HTTPException(status_code=501, detail=_PENDING_ASK_STATS)

    from app.api.payload import regrade_stats_for_min_trades
    from app.honesty.ask import answer_question

    stats = json.loads(run.stats_json)
    # L2 seal: a refunded run (always a refusal) is NOT re-graded down — the
    # answer stays consistent with the sealed screen and can't verbally bless
    # a run whose credit was refunded (the paywall-bypass vector get_run seals)
    if req.min_trades is not None and not db.was_refunded(run_id):
        # same re-gate the displayed payload got — answers and screen agree,
        # and the bar number itself is grounded (it rides the sample dump)
        stats = regrade_stats_for_min_trades(stats, req.min_trades)
    answer = answer_question(
        req.question, stats, retail=req.verbiage == "retail"
    )
    if answer is None:
        raise HTTPException(status_code=501, detail=_PENDING_ASK_KEY)
    return {"answer": answer, "demo": False}


@router.post("/sweep")
def sweep() -> dict[str, Any]:
    raise HTTPException(status_code=501, detail=_PENDING_SWEEP)
