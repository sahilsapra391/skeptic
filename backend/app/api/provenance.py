"""Run provenance (UX Chunk A): the setup story, snapshotted per run.

Three writers, one read-time deriver:

- ``creation_record`` at POST /api/backtest — the client-captured prompt,
  clarifying Q&A and confirmed draft (origin "user"), or a minimal origin
  record for automatic runs (auto_unlock / receipt), which have no
  conversation.
- ``mechanics_record`` + ``attach_mechanics`` at run completion — measured
  durations, resolution mix, effective window, and the build identity
  (deploy commit + spec_version + the fill model's product label; no
  hand-bumped ENGINE_VERSION constant — it would rot, owner 2026-07-14).
- ``derived_record`` at READ time for rows that predate the column:
  everything recoverable from stored fields (prompt from
  meta.description_raw, the decision grid from spec_json, mechanics from
  perf/stats), marked "derived". The clarifying conversation was never
  stored for those runs and is NEVER invented (owner amendment
  2026-07-14) — a derived record simply has no conversation.

Trust boundary: the creation record is client-supplied DISPLAY data. It is
size-capped, string-clamped, stored and rendered — never fed to the
engine, the verdict LLM, or grounded ask (guardrail #4 untouched). An
oversize conversation is truncated with a marker, never refused: a run is
never blocked by its own paperwork (owner 2026-07-14).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from app.api.field_labels import label_rows
from app.api.payload import FILL_MODEL
from app.api.variant import reconcile
from app.engine.types import RunResult

# generous for real use (a long clarify session is a few KB) while keeping
# the runs table honest — the listing endpoint never reads this column
MAX_RECORD_BYTES = 64_000
MAX_EVENTS = 200
MAX_EVENT_CHARS = 2_000
MAX_PROMPT_CHARS = 4_000
MAX_CONFIRMED_CHARS = 16_000
MAX_PINS = 20
MAX_OPTIONS = 8


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clip(value: Any, limit: int) -> str:
    s = str(value)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _clean_prompt(prompt: Any) -> dict[str, Any] | None:
    if not isinstance(prompt, dict):
        return None
    out: dict[str, Any] = {}
    text = prompt.get("text")
    if isinstance(text, str) and text.strip():
        out["text"] = _clip(text, MAX_PROMPT_CHARS)
    chart = prompt.get("chart")
    if isinstance(chart, dict):
        raw_pins = chart.get("pins")
        # `or ""` everywhere a field could be an explicit JSON null — dict.get
        # defaults don't fire on present-but-null keys, and str(None) would
        # store the literal text "None" as a bar time
        pins = [
            {
                "entry": _clip(p.get("entry") or "", 40),
                "exit": _clip(p["exit"], 40) if p.get("exit") else None,
            }
            for p in (raw_pins[:MAX_PINS] if isinstance(raw_pins, list) else [])
            if isinstance(p, dict)
        ]
        out["chart"] = {"ticker": _clip(chart.get("ticker") or "", 8), "pins": pins}
    return out or None


def _clean_conversation(conversation: Any) -> tuple[list[dict[str, Any]], int]:
    """Whitelisted, clamped events (chronological) + the count dropped by
    the hard event cap."""
    if not isinstance(conversation, list):
        return [], 0
    events: list[dict[str, Any]] = []
    for ev in conversation[:MAX_EVENTS]:
        if not isinstance(ev, dict) or ev.get("kind") not in ("question", "answer"):
            continue
        clean: dict[str, Any] = {"kind": ev["kind"], "id": _clip(ev.get("id") or "", 80)}
        if ev["kind"] == "question":
            clean["question"] = _clip(ev.get("question") or "", MAX_EVENT_CHARS)
            opts = ev.get("options")
            clean["options"] = (
                [_clip(o, 200) for o in opts[:MAX_OPTIONS]] if isinstance(opts, list) else []
            )
            if ev.get("asked_at"):
                clean["asked_at"] = _clip(ev["asked_at"], 40)
        else:
            clean["answer"] = _clip(ev.get("answer") or "", MAX_EVENT_CHARS)
            if ev.get("answered_at"):
                clean["answered_at"] = _clip(ev["answered_at"], 40)
        events.append(clean)
    dropped = max(len(conversation) - MAX_EVENTS, 0)
    return events, dropped


def creation_record(
    client: dict[str, Any] | None,
    origin: str,
    parent_run_id: str | None = None,
    auto_note: str | None = None,
    what_changed: list[dict[str, Any]] | None = None,
) -> str:
    """The provenance JSON written when a run row is created.

    `what_changed` is section 5 (V-13): the server-computed field-level diff
    of a user-origin variant against its parent — the SAME rows the lock
    check and the zero-edit guard read (V-162), stored at creation so no
    later reader recomputes the comparison."""
    if origin in ("auto_unlock", "receipt"):
        # automatic runs have no conversation — one origin record, note only
        note = (
            "re-ran automatically — " + (auto_note or "new data")
            if origin == "auto_unlock"
            else "5-minute replay of the original run (verdict receipt)"
        )
        return json.dumps({
            "v": 1, "origin": origin, "recorded_at": _now(),
            "parent_run_id": parent_run_id, "note": note,
        })

    record: dict[str, Any] = {"v": 1, "origin": "user", "recorded_at": _now()}
    if parent_run_id:
        record["parent_run_id"] = parent_run_id
        # V-31/V-176: sections 1-2 below are the PARENT's — its prompt, and its
        # clarifying Q&A if it had any. Marked explicitly rather than inferred,
        # so no reader (or renderer) can mistake carried history for something
        # that happened on this run. Explicit beats inferred, same reasoning as
        # V-133's window state.
        record["carried_from"] = parent_run_id
    if what_changed is not None:
        # V-208: the stored rows carry a human label so the WHAT CHANGED list and
        # the SUPERSEDED marker name fields the same way. Paths are untouched and
        # still present on every row: the label is an added caption, and a row
        # with no label renders its path, which is always correct.
        #
        # Applied HERE and not in diff_specs, because the diff is a contract
        # (V-164) read by the lock check and the zero-edit guard, and a caption
        # has no business in it. The stored provenance record is the presentation
        # artifact, so this is where presentation belongs.
        labelled, unlabeled = label_rows(what_changed)
        record["what_changed"] = labelled
        # the table's gaps report themselves rather than waiting to be noticed —
        # the V-204 posture applied to labels. Counted where the table is
        # APPLIED rather than where it renders: a browser cannot write to the
        # server's tally, and the set of gaps is identical either way.
        record["labeling"] = {"rows": len(labelled), "unlabeled": unlabeled}
        # V-222: set BEFORE the envelope below, so the byte budget accounts for
        # it. The previous version added keys after the budget was measured. The
        # cap test passed anyway, and the reason is worth naming: its fixture
        # calls creation_record with no what_changed at all, so it exercises the
        # one path where the overflow cannot occur. The assertion was right and
        # its coverage omitted the variant path entirely, which is the same
        # false-green shape as a green suite that never clicks a card.
    if not isinstance(client, dict):
        # a submitter that captured nothing (curl, an old client) — the
        # record still marks WHEN recording started, so a missing
        # conversation here is "none captured", never "predates the column"
        return json.dumps(record)

    record["source"] = client.get("source") if client.get("source") in ("text", "chart") else "text"
    prompt = _clean_prompt(client.get("prompt"))
    if prompt:
        record["prompt"] = prompt
    events, dropped = _clean_conversation(client.get("conversation"))

    confirmed = client.get("confirmed")
    if isinstance(confirmed, dict):
        if len(json.dumps(confirmed)) <= MAX_CONFIRMED_CHARS:
            record["confirmed"] = confirmed
        else:
            record["confirmed"] = {"omitted": "confirmed draft exceeded the size cap"}

    # size cap in ONE pass (never re-serialize the whole record per event —
    # that loop is quadratic in a client-supplied list): measure the envelope
    # once with a worst-case truncation marker, then keep events head-first
    # within the remaining byte budget. The head (the first questions, which
    # pair with the prompt) is the story's spine. Never a refusal.
    envelope = {**record, "conversation": [],
                "truncated": {"dropped_events": len(events) + dropped}}
    if what_changed is not None:
        # V-225: reserve the telemetry block's WORST CASE, because it is written
        # after truncation and therefore after this budget is measured.
        #
        # Moving `labeling` before the envelope (V-222) was not enough, and the
        # test that proved it is the one that finally had power: 200 events of 240
        # characters packs the budget to within ~130 bytes, and the telemetry key
        # then pushed the record 102 bytes past the cap. The first attempt at this
        # fixture used 1,900-character answers, whose leftover slack was wider
        # than the overflow and hid it — an under-powered test replacing an
        # under-powered test.
        #
        # Reserving rather than measuring, because the real counts depend on
        # `kept`, which depends on this budget. The counts are five integers each
        # bounded by MAX_EVENTS, so the worst case is exact and cheap.
        envelope["reconcile_telemetry"] = {
            "counts": dict.fromkeys(
                ("carried", "superseded", "unmatched", "suppressed", "unparseable"),
                MAX_EVENTS,
            )
        }
    budget = MAX_RECORD_BYTES - len(json.dumps(envelope).encode())
    kept: list[dict[str, Any]] = []
    used = 0
    for ev in events:
        cost = len(json.dumps(ev).encode()) + 2  # ", " separator slack
        if used + cost > budget:
            break
        kept.append(ev)
        used += cost
    dropped += len(events) - len(kept)
    record["conversation"] = kept
    if what_changed is not None and kept:
        # V-213/V-214: TELEMETRY, counts only, and the labels are deliberately
        # not stored. Nothing renders a per-exchange validity claim, and keeping
        # the matched labels out of the payload is what makes that unwritable
        # rather than merely unwired: a future reader cannot switch the markers
        # back on from stored data, because the stored data does not contain
        # them. Re-enabling would require recomputing, which is the point at
        # which someone has to re-read why it was turned off.
        #
        # Computed against `kept`, once. The previous version computed it twice,
        # before and after truncation, and the review found that the second pass
        # re-ran V-201's uniqueness over a SMALLER set of answers: a deliberate
        # suppression could flip into a confident match because the answer that
        # made it ambiguous had been truncated away. Counting once, over exactly
        # the events that were stored, has no such window.
        counts = reconcile(kept, what_changed)["counts"]
        record["reconcile_telemetry"] = {"counts": counts}
    if dropped:
        record["truncated"] = {"dropped_events": dropped}
    return json.dumps(record)


# the perf_json fields mirrored into mechanics — one tuple shared by the
# completion writer and the read-time deriver so the two can't drift
PERF_MECHANICS_KEYS = ("engine_s", "gauntlet_s", "verdict_s", "sessions", "clock")


def mechanics_record(perf: dict[str, Any], result: RunResult, spec_version: int) -> dict[str, Any]:
    """Section 4, computed at completion from measured values only."""
    return {
        **{key: perf[key] for key in PERF_MECHANICS_KEYS},
        "resolution_mix": result.resolution_mix or None,
        "effective_start": result.effective_start.isoformat(),
        "effective_end": result.effective_end.isoformat(),
        "build": {
            # the deploy commit IS the engine/fill-model version identity —
            # Railway injects it; null on local dev
            "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA"),
            "spec_version": spec_version,
            "fill_model": FILL_MODEL,
        },
    }


def attach_mechanics(
    provenance_json: str | None,
    mechanics: dict[str, Any],
    origin: str,
    parent_run_id: str | None = None,
) -> str:
    """Merge mechanics into the stored record; a missing/corrupt creation
    record degrades to a minimal one rather than losing the mechanics."""
    record: Any = None
    if provenance_json:
        try:
            record = json.loads(provenance_json)
        except (ValueError, TypeError):
            record = None
    if not isinstance(record, dict):
        record = {"v": 1, "origin": origin, "recorded_at": _now()}
        if parent_run_id:
            record["parent_run_id"] = parent_run_id
    record["mechanics"] = mechanics
    return json.dumps(record)


def derived_boxes(spec_doc: dict[str, Any]) -> dict[str, Any]:
    """The decision grid from the stored spec — the spec IS the confirmed
    truth for old runs, just not the draft object the user clicked through
    (hence the "derived" flag on the section)."""
    position = spec_doc.get("position") or {}
    entry = spec_doc.get("entry") or {}
    exit_rules = spec_doc.get("exit") or {}
    backtest = spec_doc.get("backtest") or {}
    return {
        "ticker": (spec_doc.get("underlying") or {}).get("ticker"),
        "structure": position.get("structure"),
        "legs": position.get("legs"),
        "expiration_selection": position.get("expiration_selection"),
        "schedule": entry.get("schedule"),
        "conditions": entry.get("conditions") or [],
        "scale_in": entry.get("scale_in"),
        "intraday_scan": entry.get("intraday_scan"),
        "max_concurrent_positions": entry.get("max_concurrent_positions"),
        "exit": {k: v for k, v in exit_rules.items() if v not in (None, [])},
        "sizing": spec_doc.get("sizing"),
        "costs": spec_doc.get("costs"),
        "window": {"start": backtest.get("start"), "end": backtest.get("end")},
        "clock": backtest.get("clock", "daily"),
        "resolution": backtest.get("resolution"),
        "initial_capital": backtest.get("initial_capital"),
        "seed": backtest.get("seed"),
    }


def derived_record(
    spec_doc: dict[str, Any],
    perf_doc: dict[str, Any] | None,
    stats_doc: dict[str, Any] | None,
    origin: str | None,
    parent_run_id: str | None,
) -> dict[str, Any]:
    """Read-time provenance for runs stored before the column existed.

    Derive-don't-fabricate: only fields that exist in stored data appear.
    No conversation key, ever — it was never stored for these runs.
    """
    record: dict[str, Any] = {
        "v": 1,
        "derived": True,
        "origin": origin or "user",
        "note": ("derived at read time from the stored spec and run stats; "
                 "conversation not captured (predates provenance recording)"),
    }
    if parent_run_id:
        record["parent_run_id"] = parent_run_id

    raw = (spec_doc.get("meta") or {}).get("description_raw")
    if isinstance(raw, str) and raw.strip():
        record["prompt"] = {"text": raw}

    record["confirmed"] = {"derived": True, "boxes": derived_boxes(spec_doc)}

    mech: dict[str, Any] = {}
    if isinstance(perf_doc, dict):
        for key in PERF_MECHANICS_KEYS:
            if perf_doc.get(key) is not None:
                mech[key] = perf_doc[key]
    stats = stats_doc if isinstance(stats_doc, dict) else {}
    report = stats.get("honesty_report") or {}
    for key in ("effective_start", "effective_end"):
        if report.get(key):
            mech[key] = report[key]
    if stats.get("resolutionMix"):
        mech["resolution_mix"] = stats["resolutionMix"]
    if mech:
        record["mechanics"] = mech
    return record
