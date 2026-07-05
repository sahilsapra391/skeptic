"""ENGINE-V3 D3d (Loop C): calibrate the daily fill model against the
5-minute NBBO record — evidence first, then a REVIEWED PR, never a hot
patch.

WHAT IS MEASURED. The daily engine fills at the EOD chain's close quote:
mid ± slippage_half_spread_fraction × half-spread (buys toward ask, sells
toward bid — guardrail #1). For every contract-date present in BOTH the
winning EOD chain AND the intraday slice's closing bar (true NBBO,
ivol_5min sessions only), we ask: how far from the TRUE closing mid does
that daily fill land, in units of the TRUE half-spread?

    excess_buy  = (fill_daily_buy  − nbbo_mid) / nbbo_half_spread
    excess_sell = (nbbo_mid − fill_daily_sell) / nbbo_half_spread

The model intends to concede half the half-spread (f = 0.5). If EOD
quotes were perfect closing NBBO, the median excess would BE f. Stale or
wide EOD marks push it off; the measured median is the truth the default
must answer to. Calibration targets the BASE fraction only — the OI-
scaled thin-liquidity slip and commission are separate, unchanged layers.

DECISION RULE (owner amendments 2+3, docs/HONESTY.md):
- median excess ABOVE f + bar → daily fills are MORE punitive than
  intended → aligning means LOWERING the default → OPTIMISM-INCREASING:
  requires the HIGHER evidence bar (more contract-days, bigger
  divergence) and the PR title carries the "OPTIMISM-INCREASING:" prefix.
- median excess BELOW f − bar → daily fills are CHEAPER than reality →
  raising the default is a CONSERVATIVE fix and opens at the base bar.
- inside the bar, or below the sample floor → no proposal; the evidence
  doc says so and the pass re-runs next week on more data.

The proposal edits the REAL defaults (app/models/spec.py + the schema) —
no config indirection (owner amendment 2). The parser prompt's example
value and the frontend Settings default are deliberately NOT auto-edited
(each has its own review gate); the PR body lists them as follow-ups.

Aggregates only are written anywhere — never chain data rows.
"""

from __future__ import annotations

import argparse
import logging
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import time as dtime
from pathlib import Path

from app.config import load_local_env

load_local_env()

from app.models.spec import Costs  # noqa: E402

log = logging.getLogger("calibrate")

# ---------------------------------------------------------------- thresholds
# Reviewed constants (standing guardrail: never changed at runtime).
CAL_BASE_MIN_N = 500  # contract-days before a CONSERVATIVE proposal
CAL_BASE_BAR = 0.25  # |median excess − f| in true-half-spread units
CAL_OPTIMISTIC_MIN_N = 2_000  # optimism-increasing needs 4× the sample …
CAL_OPTIMISTIC_BAR = 0.50  # … and 2× the divergence (owner amendment 3)
CAL_CLOSE_BAR_MIN = dtime(15, 45)  # a session's "close" NBBO must be ≥15:45 ET
CAL_ROUND = 0.05  # proposed defaults land on a 0.05 grid

# Anchors for the auto-PR's edits — exact strings; a miss aborts loudly.
SPEC_PY = Path(__file__).resolve().parents[1] / "app" / "models" / "spec.py"
SCHEMA = Path(__file__).resolve().parents[2] / "docs" / "strategy-spec.schema.json"
CAL_STATE_KEY = "state/calibration_latest.json"


@dataclass
class Calibration:
    """Aggregate result of one calibration pass. Distribution stats only —
    no per-contract rows leave the process (legal rail)."""

    measured_at: str
    f_current: float
    sessions_shared: int = 0
    sessions_used: int = 0
    sessions_skipped_no_short_tenor: int = 0  # EOD chain has no slice-tenor contracts
    sessions_skipped_source: int = 0  # non-NBBO intraday sessions (never calibrate)
    sessions_skipped_early: int = 0  # last bar before 15:45 ET
    n: int = 0  # contract-days × sides
    excess: list[float] = field(default_factory=list)  # in-memory only
    spread_ratio: list[float] = field(default_factory=list)  # eod half / nbbo half
    first_shared: str | None = None
    last_shared: str | None = None

    # -- distribution views (what the evidence doc and state JSON carry) --
    def stats(self) -> dict[str, object]:
        def dist(xs: list[float]) -> dict[str, float] | None:
            if not xs:
                return None
            qs = statistics.quantiles(xs, n=4) if len(xs) >= 4 else [xs[0], xs[0], xs[0]]
            return {
                "median": round(statistics.median(xs), 4),
                "p25": round(qs[0], 4),
                "p75": round(qs[2], 4),
            }

        return {
            "measured_at": self.measured_at,
            "f_current": self.f_current,
            "window": {"first": self.first_shared, "last": self.last_shared},
            "sessions": {
                "shared": self.sessions_shared,
                "used": self.sessions_used,
                "skipped_no_short_tenor": self.sessions_skipped_no_short_tenor,
                "skipped_non_nbbo": self.sessions_skipped_source,
                "skipped_early_close_bar": self.sessions_skipped_early,
            },
            "n": self.n,
            "excess": dist(self.excess),
            "spread_ratio": dist(self.spread_ratio),
        }


@dataclass
class Decision:
    proposal: bool
    direction: str  # "none" | "conservative" | "optimism_increasing"
    f_new: float | None
    reason: str


def measure(ticker: str = "SPY") -> Calibration:
    """Walk every session shared by the EOD chain record and the intraday
    slice; compare the daily model's close fills against the true closing
    NBBO for every contract present in both."""
    from app.data.chains import load_market_store
    from app.data.intraday import load_intraday_store

    f = Costs().slippage_half_spread_fraction
    cal = Calibration(measured_at=datetime.now(UTC).isoformat(), f_current=f)

    store = load_market_store(ticker)
    intraday = load_intraday_store(ticker)
    shared = [d for d in intraday.sessions() if d in store.chains]
    cal.sessions_shared = len(shared)
    if shared:
        cal.first_shared, cal.last_shared = str(shared[0]), str(shared[-1])

    slice_cap = intraday.slice_max_trading_dte
    for day in shared:
        eod = store.chains[day]
        # cheap pre-filter on the in-memory chain: without a contract the
        # intraday slice could even HOLD (≤ cap trading-DTE ≈ cap+2 calendar
        # days), there is nothing to compare — skip without touching the
        # intraday store. On the dolthub-era record (no <11 DTE expirations)
        # this skips ~every historical session, which keeps the weekly
        # Actions pass from cold-loading thousands of session slices.
        if not any((k.expiration - day).days <= slice_cap + 2 for k in eod):
            cal.sessions_skipped_no_short_tenor += 1
            continue
        slc = intraday.slice_for(day)
        if slc is None or slc.quote_source != "ivol_5min":
            cal.sessions_skipped_source += 1
            continue
        if not slc.bars or slc.bars[-1].time() < CAL_CLOSE_BAR_MIN:
            cal.sessions_skipped_early += 1
            continue
        nbbo = slc.quotes.get(slc.bars[-1], {})
        used_any = False
        for key, qn in nbbo.items():
            qe = eod.get(key)
            if qe is None:
                continue
            if not _valid(qn.bid, qn.ask) or not _valid(qe.bid, qe.ask):
                continue
            b_n, a_n = float(qn.bid), float(qn.ask)  # type: ignore[arg-type]
            b_e, a_e = float(qe.bid), float(qe.ask)  # type: ignore[arg-type]
            mid_n, half_n = (b_n + a_n) / 2, (a_n - b_n) / 2
            mid_e, half_e = (b_e + a_e) / 2, (a_e - b_e) / 2
            if half_n <= 0:
                continue
            fill_buy = mid_e + f * half_e
            fill_sell = mid_e - f * half_e
            cal.excess.append((fill_buy - mid_n) / half_n)
            cal.excess.append((mid_n - fill_sell) / half_n)
            cal.spread_ratio.append(half_e / half_n)
            cal.n += 2
            used_any = True
        if used_any:
            cal.sessions_used += 1
    return cal


def _valid(bid: float | None, ask: float | None) -> bool:
    return bid is not None and ask is not None and bid > 0 and ask >= bid


def decide(cal: Calibration) -> Decision:
    """The documented decision rule — asymmetric by direction (amendment 3)."""
    if not cal.excess:
        return Decision(False, "none", None, "no overlapping contract-days measured")
    m = statistics.median(cal.excess)
    f = cal.f_current
    div = m - f
    if abs(div) < CAL_BASE_BAR:
        return Decision(
            False, "none", None,
            f"median excess {m:.3f} within ±{CAL_BASE_BAR} of intended {f} — aligned",
        )
    if div < 0:  # daily fills cheaper than reality → raising f is conservative
        if cal.n < CAL_BASE_MIN_N:
            return Decision(
                False, "none", None,
                f"conservative divergence ({m:.3f} vs {f}) but n={cal.n} "
                f"< {CAL_BASE_MIN_N} — waiting for more shared history",
            )
        return Decision(True, "conservative", _rescale(f, m), f"daily fills concede only "
                        f"{m:.3f} true half-spreads vs intended {f} — model too optimistic")
    # div > 0: daily fills MORE punitive → lowering f is optimism-increasing
    if cal.n < CAL_OPTIMISTIC_MIN_N or div < CAL_OPTIMISTIC_BAR:
        return Decision(
            False, "none", None,
            f"optimism-increasing divergence ({m:.3f} vs {f}) below the higher bar "
            f"(needs n≥{CAL_OPTIMISTIC_MIN_N} and ≥{CAL_OPTIMISTIC_BAR}; "
            f"n={cal.n}, div={div:.3f}) — never a silent nudge toward rosier numbers",
        )
    return Decision(True, "optimism_increasing", _rescale(f, m), f"daily fills concede "
                    f"{m:.3f} true half-spreads vs intended {f} — model overcharges; "
                    f"higher evidence bar met")


def _rescale(f: float, measured: float) -> float:
    """New default so the model's cost lands where it intends: scale f by
    intended/measured, snap to the 0.05 grid, keep inside (0, 1]."""
    raw = f * (f / measured)
    snapped = round(raw / CAL_ROUND) * CAL_ROUND
    return max(CAL_ROUND, min(1.0, round(snapped, 2)))


def evidence_markdown(cal: Calibration, decision: Decision, today: str) -> str:
    from typing import cast

    s = cal.stats()
    placeholder: dict[str, object] = {"median": "—", "p25": "—", "p75": "—"}
    excess = cast(dict[str, object], s["excess"]) if s["excess"] else placeholder
    ratio = cast(dict[str, object], s["spread_ratio"]) if s["spread_ratio"] else placeholder
    lines = [
        f"# Fill-model calibration — {today}",
        "",
        "Weekly evidence pass (ENGINE-V3 D3d). Daily-model close fills vs the",
        "true closing NBBO (last ≥15:45 ET bar of the ivol_5min record), for",
        "every contract-date present in BOTH records. Aggregates only — no",
        "chain rows are reproduced here (legal rail).",
        "",
        f"- current default `slippage_half_spread_fraction`: **{cal.f_current}**",
        f"- shared sessions: {cal.sessions_shared} "
        f"(used {cal.sessions_used}, no short tenor on the EOD chain "
        f"{cal.sessions_skipped_no_short_tenor}, non-NBBO {cal.sessions_skipped_source}, "
        f"early close-bar {cal.sessions_skipped_early}) · window "
        f"{cal.first_shared or '—'} → {cal.last_shared or '—'}",
        f"- contract-day sides measured: **n = {cal.n}**",
        f"- excess (true half-spreads conceded by the daily fill): "
        f"median **{excess['median']}**, p25 {excess['p25']}, p75 {excess['p75']}",
        f"- EOD/NBBO half-spread ratio: median {ratio['median']}, "
        f"p25 {ratio['p25']}, p75 {ratio['p75']}",
        "",
        "## Decision",
        "",
        f"**{'PROPOSAL: ' + decision.direction if decision.proposal else 'No change.'}** "
        f"{decision.reason}.",
    ]
    if decision.proposal:
        lines += [
            "",
            f"Proposed default: `{decision.f_new}` (rescaled {cal.f_current} × "
            f"{cal.f_current}/median, snapped to {CAL_ROUND}).",
        ]
    lines += [
        "",
        "## Thresholds (reviewed constants, scripts/calibrate_fill_model.py)",
        "",
        f"- conservative direction: n ≥ {CAL_BASE_MIN_N}, |divergence| ≥ {CAL_BASE_BAR}",
        f"- optimism-increasing: n ≥ {CAL_OPTIMISTIC_MIN_N}, divergence ≥ "
        f"{CAL_OPTIMISTIC_BAR}, PR title prefixed `OPTIMISM-INCREASING:` "
        "(owner amendment 3)",
        "",
        "Known context: the historical EOD chains (dolthub era) carry no <11 DTE",
        "expirations, and the intraday slice is 0–2 trading-DTE — contract",
        "overlap begins with the Yahoo 0–60 DTE capture (2026-07-01). n grows",
        "with every session the capture banks; this pass re-runs weekly.",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------- auto-PR path
def _edit_defaults(f_new: float) -> None:
    """Anchored, exact-string edits to the REAL defaults. A missing anchor
    aborts before anything is written."""
    spec_old = "slippage_half_spread_fraction: float = Field(default=0.5, gt=0, le=1)"
    spec_new = f"slippage_half_spread_fraction: float = Field(default={f_new}, gt=0, le=1)"
    schema_old = '"exclusiveMinimum": 0,\n          "maximum": 1,\n          "default": 0.5,'
    schema_new = (
        f'"exclusiveMinimum": 0,\n          "maximum": 1,\n          "default": {f_new},'
    )
    spec_text = SPEC_PY.read_text()
    schema_text = SCHEMA.read_text()
    if spec_old not in spec_text or schema_old not in schema_text:
        raise SystemExit("calibration anchors not found — defaults moved; update anchors")
    SPEC_PY.write_text(spec_text.replace(spec_old, spec_new, 1))
    SCHEMA.write_text(schema_text.replace(schema_old, schema_new, 1))


def open_pr(cal: Calibration, decision: Decision, today: str) -> None:
    assert decision.proposal and decision.f_new is not None
    branch = f"calibration/{today}"
    prefix = "OPTIMISM-INCREASING: " if decision.direction == "optimism_increasing" else ""
    title = (
        f"{prefix}calibration {today}: slippage_half_spread_fraction "
        f"{cal.f_current} → {decision.f_new}"
    )
    doc_rel = f"docs/calibration/{today}.md"
    doc_path = SCHEMA.parent / "calibration" / f"{today}.md"
    doc_path.parent.mkdir(exist_ok=True)
    doc_path.write_text(evidence_markdown(cal, decision, today))
    _edit_defaults(decision.f_new)
    body = (
        f"Automated weekly calibration proposal (ENGINE-V3 D3d). Evidence: `{doc_rel}` "
        f"(in this PR).\n\n{decision.reason}.\n\n"
        "Edits the REAL defaults only (app/models/spec.py + docs/strategy-spec.schema.json"
        " — owner amendment 2). Human follow-ups on merge, each behind its own gate:\n"
        "- parser prompt example value (requires parser-eval re-ACCEPT)\n"
        "- frontend Settings default (frontend/lib/settings.ts)\n\n"
        "Merging is the review — nothing changed until this lands.\n\n"
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    )
    def run(*args: str) -> None:
        subprocess.run(args, check=True)

    run("git", "checkout", "-b", branch)
    run("git", "add", str(SPEC_PY), str(SCHEMA), str(doc_path))
    run("git", "commit", "-m", title)
    run("git", "push", "-u", "origin", branch)
    run("gh", "pr", "create", "--base", "main", "--title", title, "--body", body)
    log.info("calibration PR opened: %s", title)


def _write_state(cal: Calibration, decision: Decision) -> None:
    """Aggregates to R2 so the Observatory and the priorities pass can read
    the latest measurement without re-walking the lake."""
    from app.data import r2

    payload = {
        **cal.stats(),
        "decision": {
            "proposal": decision.proposal,
            "direction": decision.direction,
            "f_new": decision.f_new,
            "reason": decision.reason,
        },
    }
    s3 = r2.r2_client()
    r2.put_json(s3, CAL_STATE_KEY, payload)
    today = datetime.now(UTC).date().isoformat()
    r2.put_json(s3, f"state/calibration/{today}.json", payload)


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ticker", default="SPY")
    ap.add_argument("--execute", action="store_true",
                    help="write R2 state and open the proposal PR when warranted "
                         "(default: dry-run — print the evidence, touch nothing)")
    ap.add_argument("--write-doc", action="store_true",
                    help="write docs/calibration/<today>.md locally (evidence artifact)")
    args = ap.parse_args()

    cal = measure(args.ticker)
    decision = decide(cal)
    today = datetime.now(UTC).date().isoformat()
    doc = evidence_markdown(cal, decision, today)
    print(doc)

    if args.write_doc:
        out = SCHEMA.parent / "calibration" / f"{today}.md"
        out.parent.mkdir(exist_ok=True)
        out.write_text(doc)
        log.info("evidence written: %s", out)
    if args.execute:
        _write_state(cal, decision)
        if decision.proposal:
            open_pr(cal, decision, today)
        else:
            log.info("no proposal — %s", decision.reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
