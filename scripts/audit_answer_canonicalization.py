#!/usr/bin/env python
"""V-206: can value-matching actually work on the answers we have stored?

READ ONLY. This script does not write.

V-200 replaced PR-A2's label table with value matching: a carried exchange is
SUPERSEDED when its recorded answer, canonicalized, equals the `parent` value of
a changed diff row. That mechanism rests on two empirical facts nobody had
measured, and this measures them before the reconciler is built:

  PARSE RATE       what fraction of recorded answers canonicalize at all.
                   An answer that cannot be canonicalized can never match, so
                   this is the ceiling on how often SUPERSEDED can ever fire.

  ANCHOR RATE      how many canonicalized answers equal ANY scalar value in
                   their own run's stored spec. This is the number that matters
                   and parse rate flatters: an answer that canonicalizes but
                   matches nothing in the spec it produced can never match a
                   diff row either, because a diff row's `parent` value IS a
                   spec value. Measurable today without any variant existing.

  COLLISION        how many runs carry two exchanges whose answers canonicalize
  EXPOSURE         to the SAME token. V-201 requires a UNIQUE match, so every
                   such pair is a guaranteed suppression: both render STILL
                   HOLDS no matter what changed. This is the cost of the
                   never-guess rule, stated as a number instead of a worry.

WHAT THIS CANNOT TELL YOU (V-63, blind spots inline rather than in a doc read
alongside): no variants exist in production yet, so there are no real diff rows
to match against and the actual SUPERSEDED hit rate is unmeasurable today. Parse
rate is an upper bound on it and nothing more. A high parse rate does not mean
answers will match the fields that change; it means they are eligible to.

DATABASE_URL contract: read from the environment, required, never written to a
file and never echoed. Postgres targets open a READ ONLY transaction; SQLite
targets open with mode=ro. There is no write path in this file at all.

Usage:
    DATABASE_URL=... uv run --project backend python scripts/audit_answer_canonicalization.py
    ... --json      machine-readable, same numbers
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.variant import canonical_token  # noqa: E402  (the ONE normalizer, V-202/V-163)


def _pair(events: list[dict]) -> list[dict]:
    """Mirrors frontend/components/results/how-built.tsx pairConversation
    exactly: an answer attaches to the first OPEN question sharing its id, and
    otherwise stands alone. Mirrored rather than reinvented because a different
    pairing here would measure a population the UI never renders."""
    out: list[dict] = []
    for ev in events:
        if ev.get("kind") == "question":
            out.append({"question": ev})
            continue
        open_x = next(
            (x for x in out if x.get("question", {}).get("id") == ev.get("id") and "answer" not in x),
            None,
        )
        if open_x is not None:
            open_x["answer"] = ev
        else:
            out.append({"answer": ev})
    return out


def _spec_tokens(spec_json: str | None) -> set[str]:
    """Every scalar in a stored spec, through the SAME normalizer the answers
    go through. A diff row's `parent` is one of these, so an answer that
    matches none of them can never match a row."""
    if not spec_json:
        return set()
    try:
        spec = json.loads(spec_json)
    except (TypeError, ValueError):
        return set()
    out: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        else:
            token = canonical_token(node)
            if token is not None:
                out.add(token)

    walk(spec)
    return out


def _why_unanchored(token: str, spec_tokens: set[str]) -> str:
    """Diagnosis, not a fix. Says WHICH normalization gap a miss would need,
    so the decision to widen the canonical space is made on counts rather than
    on a guess about what users type."""
    try:
        number = float(token)
    except ValueError:
        for st in spec_tokens:
            if token in st or st in token:
                return "string, substring of a spec value"
        return "string, absent from the spec"
    for scaled, name in (
        (number / 100, "numeric, would anchor if scaled /100 (delta 30 vs 0.30)"),
        (number * 100, "numeric, would anchor if scaled *100"),
    ):
        cand = str(int(scaled)) if scaled == int(scaled) else repr(round(scaled, 10))
        if cand in spec_tokens:
            return name
    return "numeric, absent from the spec at any scale"


def _rows(url: str):
    """Yields (provenance_json, spec_json). Read-only by construction."""
    if url.startswith("sqlite"):
        import sqlite3

        path = url.split("sqlite:///", 1)[-1]
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            yield from conn.execute("select provenance_json, spec_json from runs")
        finally:
            conn.close()
        return

    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        for row in conn.execute(text("select provenance_json, spec_json from runs")):
            yield row[0], row[1]
    engine.dispose()


def main() -> int:
    as_json = "--json" in sys.argv
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required. Refusing to guess a target.", file=sys.stderr)
        return 2

    if not as_json:
        print("READ ONLY. This script does not write.")
        target = "local SQLite" if url.startswith("sqlite") else url.split("@", 1)[-1].split("/", 1)[0]
        print(f"target: {target}\n")

    total = with_prov = with_conv = 0
    exchanges = answers = canonicalized = 0
    kinds: Counter[str] = Counter()
    runs_with_collision = 0
    collision_pairs = 0
    anchored = 0
    misses: Counter[str] = Counter()

    for prov, spec_json in _rows(url):
        total += 1
        if not prov:
            continue
        try:
            record = json.loads(prov)
        except (TypeError, ValueError):
            continue
        with_prov += 1
        events = record.get("conversation")
        if not isinstance(events, list) or not events:
            continue
        with_conv += 1

        spec_tokens = _spec_tokens(spec_json)
        tokens_this_run: Counter[str] = Counter()
        for pair in _pair(events):
            exchanges += 1
            answer = (pair.get("answer") or {}).get("answer")
            if not answer:
                continue
            answers += 1
            token = canonical_token(answer)
            if token is None:
                kinds["unparseable"] += 1
                continue
            canonicalized += 1
            tokens_this_run[token] += 1
            if token in spec_tokens:
                anchored += 1
            else:
                misses[_why_unanchored(token, spec_tokens)] += 1
            if token in ("true", "false"):
                kinds["boolean"] += 1
            else:
                try:
                    float(token)
                    kinds["numeric"] += 1
                except ValueError:
                    kinds["string"] += 1

        dupes = sum(c - 1 for c in tokens_this_run.values() if c > 1)
        if dupes:
            runs_with_collision += 1
            collision_pairs += dupes

    result = {
        "runs_total": total,
        "runs_with_provenance": with_prov,
        "runs_with_conversation": with_conv,
        "exchanges": exchanges,
        "answers_recorded": answers,
        "answers_canonicalized": canonicalized,
        "parse_rate": round(canonicalized / answers, 4) if answers else None,
        "answers_anchored_in_own_spec": anchored,
        "anchor_rate": round(anchored / answers, 4) if answers else None,
        "unanchored_reasons": dict(misses),
        "token_kinds": dict(kinds),
        "runs_with_collision": runs_with_collision,
        "collision_pairs": collision_pairs,
        "collision_rate_over_runs_with_conversation": (
            round(runs_with_collision / with_conv, 4) if with_conv else None
        ),
    }

    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    # denominators BEFORE the count (V-66): "0 collisions" is unreadable
    # without knowing how many runs could have had one.
    print(f"{total:>6}  runs in the database")
    print(f"{with_prov:>6}  with a provenance record")
    print(f"{with_conv:>6}  carrying a conversation  <- the eligible set")
    print()
    print(f"{exchanges:>6}  exchanges (paired as the UI pairs them)")
    print(f"{answers:>6}  with a recorded answer")
    print()
    if answers:
        pct = 100 * canonicalized / answers
        print(f"PARSE RATE      {canonicalized}/{answers} = {pct:.1f}% of answers canonicalize")
        for kind, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
            print(f"                  {n:>5}  {kind}")
    else:
        print("PARSE RATE      no recorded answers to measure")
    print()
    if answers:
        apct = 100 * anchored / answers
        print(f"ANCHOR RATE     {anchored}/{answers} = {apct:.1f}% of answers equal a value")
        print("                in their own run's spec, so could ever match a diff row")
        for why, n in sorted(misses.items(), key=lambda kv: -kv[1]):
            print(f"                  {n:>5}  unanchored: {why}")
    print()
    if with_conv:
        print(
            f"COLLISION       {runs_with_collision}/{with_conv} runs carry two exchanges "
            f"canonicalizing alike"
        )
        print(f"EXPOSURE        {collision_pairs} colliding pairs in total")
        print("                each is a guaranteed STILL HOLDS under V-201's unique-match rule")
    else:
        print("COLLISION       no conversations to measure")
    print()
    print("Blind spot: no variants exist yet, so the real SUPERSEDED hit rate cannot")
    print("be measured. Parse rate is its ceiling, not its estimate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
