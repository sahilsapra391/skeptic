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

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

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
            s.commit()
    except Exception as exc:
        log.exception("run %s failed", run_id)
        with db.session() as s:
            run = s.get(db.Run, run_id)
            if run is not None:
                run.status = "error"
                run.error = f"{type(exc).__name__}: {exc}"
                s.add(db.RunEvent(run_id=run_id, stage=run.stage, label="failed"))
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
def backtest(req: BacktestRequest, tasks: BackgroundTasks) -> dict[str, Any]:
    try:
        spec = StrategySpec.model_validate(req.spec)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc
    if req.seed is not None:
        spec.backtest.seed = req.seed

    if req.origin not in VALID_ORIGINS:
        raise HTTPException(status_code=422, detail=f"unknown origin {req.origin!r}")

    # the evidence bar: the caller's setting; automatic re-runs without one
    # inherit their parent's so an unlock never moves its own goalposts
    if req.min_trades is not None:
        min_trades = req.min_trades
    elif req.origin in ("auto_unlock", "receipt"):
        min_trades = _inherit_min_trades(req.parent_run_id)
    else:
        min_trades = MIN_TRADES

    run_id = uuid.uuid4().hex[:12]
    note = (req.auto_note or "")[:AUTO_NOTE_MAX] or None
    with db.session() as s:
        s.add(
            db.Run(
                id=run_id,
                status="queued",
                stage=0,
                seed=spec.backtest.seed,
                spec_json=spec.model_dump_json(),
                origin=req.origin,
                parent_run_id=req.parent_run_id,
                provenance_json=creation_record(
                    req.provenance, req.origin, req.parent_run_id, note
                ),
            )
        )
        s.commit()
    tasks.add_task(_execute_run, run_id, note, min_trades)
    return {"run_id": run_id, "demo": False, "status": "queued"}


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
    scope: str = Query(default="examples"),
    include: str = Query(default="", max_length=1200),
) -> dict[str, Any]:
    """Library listing. Reads ONLY the small summary column — pulling 50
    full payloads (equity series and all) per listing is how a database
    transfer quota dies. Queued/running runs get an ephemeral summary so
    navigating away from the progress screen never 'loses' a run.

    Pre-accounts curation (owner 2026-07-17): the DEFAULT listing is the
    two pinned example runs, explicitly badged — the dev-era history must
    not appear as anyone's library. `include` adds the caller's OWN run ids
    (the client remembers what it started; the accounts chunk re-parents
    exactly this list — the claim flow). scope=all keeps the full listing
    for the owner and stays until the accounts chunk gates it on principal
    (nightly automation reads the DB directly and is unaffected)."""
    with db.session() as s:
        query = s.query(
            db.Run.id,
            db.Run.created_at,
            db.Run.status,
            db.Run.stage,
            db.Run.summary_json,
            db.Run.spec_json,
        )
        examples_only = scope != "all"
        examples = set(example_run_ids())  # once — not per row (env parse)
        if examples_only:
            own = tuple(x.strip() for x in include.split(",") if x.strip())[:50]
            query = query.filter(db.Run.id.in_(tuple(examples) + own))
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
        for run_id, created_at, status, stage, summary_json, spec_json in rows:
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
                    }
                )
                continue
            if summary_json:
                summary = json.loads(summary_json)
                if run_id in examples:
                    summary["example"] = True
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


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    min_trades: int | None = Query(default=None, ge=1, le=10_000),
) -> dict[str, Any]:
    with db.session() as s:
        run = s.get(db.Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
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
        if min_trades is not None and run.stats_json:
            from app.api.payload import regrade_for_min_trades

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


@router.post("/runs/{run_id}/replay")
def replay_run(run_id: str, tasks: BackgroundTasks) -> dict[str, Any]:
    """On-demand verdict receipt (D3c, owner amendment 1): replay THIS
    daily run at the 5-minute clock, right now. The receipt attaches to
    the original when the replay completes; the stored verdict is never
    rewritten."""
    from app.api.replay import build_replay_spec, replay_eligible_spec

    with db.session() as s:
        run = s.get(db.Run, run_id)
        if run is None or run.status != "done" or not run.spec_json:
            raise HTTPException(status_code=404, detail="no completed run to replay")
        spec_dict = json.loads(run.spec_json)
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
def ask(run_id: str, req: AskRequest) -> dict[str, Any]:
    if not req.question.strip():
        raise HTTPException(status_code=422, detail="empty question")
    with db.session() as s:
        run = s.get(db.Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    if run.status != "done" or not run.stats_json:
        raise HTTPException(status_code=501, detail=_PENDING_ASK_STATS)

    from app.api.payload import regrade_stats_for_min_trades
    from app.honesty.ask import answer_question

    stats = json.loads(run.stats_json)
    if req.min_trades is not None:
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
