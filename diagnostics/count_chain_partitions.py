"""On-lake confirmation for SEVENTEEN.md §5: count the distinct options
chain `date=` partitions per source in R2, plus the dolthub quarantine and
backfill frontier state. Needs R2 creds in the environment.

    uv run --project backend python diagnostics/count_chain_partitions.py [TICKER ...]

Prints only DATES and COUNTS — never chain rows (guardrail: never log chain
data rows).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.data import r2  # noqa: E402

SOURCES = ("ivolatility", "alphavantage", "yahoo", "dolthub")


def report(ticker: str) -> None:
    s3 = r2.r2_client()
    print(f"\n=== {ticker} — options chain date partitions in R2 ===")
    union: set[str] = set()
    per_source: dict[str, list[str]] = {}
    for src in SOURCES:
        dates = r2.list_chain_dates(s3, src, ticker)
        per_source[src] = dates
        union |= set(dates)
        span = f"{dates[0]} .. {dates[-1]}" if dates else "(none)"
        print(f"  {src:14s}: {len(dates):5d} dates   {span}")

    verified = set(r2.get_json(s3, "state/dolthub_backfill.json", {}).get("done", []))
    dolthub_live = [d for d in per_source["dolthub"] if d in verified]
    print(f"  dolthub verified (quarantine 'done'): {len(dolthub_live)} of "
          f"{len(per_source['dolthub'])} present")

    # loader precedence union, honoring the dolthub quarantine (mirrors chains._chain_keys)
    effective = (set(per_source["ivolatility"]) | set(per_source["alphavantage"])
                 | set(per_source["yahoo"]) | set(dolthub_live))
    print(f"  --> DISTINCT chain dates the engine would load: {len(effective)}")
    if effective:
        ordered = sorted(effective)
        print(f"      span {ordered[0]} .. {ordered[-1]}")

    frontier = r2.get_json(s3, "state/backfill_frontier.json", None)
    if frontier is not None:
        print(f"  backfill_frontier.json: {frontier}")

    verdict = "CONFIRMS SEVENTEEN root cause" if len(effective) <= 40 else "larger than expected — re-open SEVENTEEN.md"
    print(f"  [{verdict}]")


def main() -> None:
    tickers = sys.argv[1:] or ["SPY", "QQQ", "IWM"]
    if not r2.r2_configured():
        print("R2 not configured — set R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / "
              "R2_SECRET_ACCESS_KEY / R2_BUCKET (see collector/.env).")
        raise SystemExit(1)
    for t in tickers:
        report(t)


if __name__ == "__main__":
    main()
