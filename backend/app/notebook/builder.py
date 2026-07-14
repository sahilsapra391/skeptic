"""Run → .ipynb (parity Tier 1, execution mode (a): API-backed).

The notebook is the run's story in IVolAI's medium with Skeptic's spine:
it OPENS with how the strategy was agreed (the Chunk A provenance record
as markdown — prompt, clarifying Q&A, confirmed decision grid), shows the
stored results with their data window, walks the honesty gauntlet, and
closes with a deterministic re-execution against the deployed API that
pins the recorded per-session resolution map (never silently re-resolve —
plan do-NOT list).

Design stances, all owner-visible in the output:
  * API-backed, not self-contained: cells call the deployed API with the
    single-user bearer token from SKEPTIC_ACCESS_TOKEN — no lake
    credentials ever ride in a file, and the numbers are guaranteed to be
    the app's because the same engine serves both.
  * The token is NEVER embedded. Cells read the environment and fail with
    a plain sentence when it is missing.
  * Markdown cells carry the story SNAPSHOTTED at export time; code cells
    fetch the same run live so a re-run always shows the stored truth.
  * Every results cell carries the data window; the disclaimer opens and
    closes the file (legal rails: every results surface).
  * Deterministic export: same run row → byte-identical notebook (cell
    ids are sequential, no timestamps beyond the run's own record).
"""

from __future__ import annotations

import json
from itertools import count
from typing import Any

# nbformat 4.5 written directly — the schema is tiny and stable, and a
# hand-built dict keeps the export dependency-free server-side
NBFORMAT = 4
NBFORMAT_MINOR = 5


def _cell(kind: str, source: str, cell_id: str) -> dict[str, Any]:
    cell: dict[str, Any] = {
        "id": cell_id,
        "cell_type": kind,
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }
    if kind == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def _fence(text: str) -> str:
    """User text inside markdown — fenced so it can't inject formatting."""
    safe = text.replace("```", "``​`")
    return f"```text\n{safe}\n```"


def _provenance_md(record: dict[str, Any], grid: dict[str, Any]) -> str:
    """Section 2 of the story: how the strategy was agreed. Renders ONLY
    what the record holds — a derived record has no conversation and says
    so (the conversation is never invented, owner amendment 2026-07-14)."""
    lines: list[str] = ["## How this strategy was agreed", ""]

    origin = record.get("origin", "user")
    if origin != "user":
        note = record.get("note") or f"automatic run ({origin})"
        lines += [f"*{note}*", ""]
    if record.get("derived"):
        lines += ["*Setup story derived from the stored spec — this run "
                  "predates provenance recording, so the clarifying "
                  "conversation was never captured (and is not invented).*",
                  ""]

    prompt = (record.get("prompt") or {}).get("text")
    if prompt:
        lines += ["**The ask, verbatim:**", "", _fence(prompt), ""]
    chart = (record.get("prompt") or {}).get("chart")
    if chart and chart.get("pins"):
        pins = ", ".join(
            p["entry"] + (f" → {p['exit']}" if p.get("exit") else "")
            for p in chart["pins"]
        )
        lines += [f"*Taught on the {chart.get('ticker', '')} chart — "
                  f"pinned bars: {pins}*", ""]

    conversation = record.get("conversation") or []
    if conversation:
        lines += ["**Clarified before anything ran:**", ""]
        for ev in conversation:
            if ev.get("kind") == "question":
                lines.append(f"- **Q:** {ev.get('question', '')}")
            elif ev.get("kind") == "answer":
                lines.append(f"  - **A:** {ev.get('answer', '')}")
        lines.append("")
        truncated = record.get("truncated", {}).get("dropped_events")
        if truncated:
            lines += [f"*({truncated} further exchange(s) not shown — "
                      "size-capped at recording time.)*", ""]

    lines += ["**The decision grid that ran (from the validated spec):**", ""]
    lines += ["| decision | value |", "|---|---|"]
    for key in ("ticker", "structure", "clock", "resolution",
                "expiration_selection", "schedule", "conditions", "scale_in",
                "max_concurrent_positions", "exit", "sizing", "costs",
                "window", "initial_capital", "seed"):
        value = grid.get(key)
        if value in (None, [], {}):
            continue
        rendered = value if isinstance(value, str) else json.dumps(value)
        rendered = str(rendered).replace("|", "\\|")
        lines.append(f"| {key.replace('_', ' ')} | `{rendered}` |")
    lines.append("")

    mech = record.get("mechanics") or {}
    if mech:
        build = mech.get("build") or {}
        bits = [
            f"clock `{mech.get('clock', '?')}`",
            f"sessions `{mech.get('sessions', '?')}`",
            f"effective window `{mech.get('effective_start', '?')} → "
            f"{mech.get('effective_end', '?')}`",
        ]
        if mech.get("resolution_mix"):
            bits.append(f"resolution mix `{json.dumps(mech['resolution_mix'])}`")
        bits.append(f"engine `{mech.get('engine_s', '?')}s` · "
                    f"gauntlet `{mech.get('gauntlet_s', '?')}s`")
        if build:
            bits.append(f"build `{build.get('commit') or 'local'}` · "
                        f"spec v{build.get('spec_version', '?')} · "
                        f"fill model `{build.get('fill_model', '?')}`")
        lines += ["**Mechanics (measured, not estimated):** " + " · ".join(bits), ""]
    return "\n".join(lines)


def _reproducibility_md(payload: dict[str, Any], grid: dict[str, Any]) -> str:
    lines = [
        "## Reproducibility contract",
        "",
        "The re-execution cell at the bottom asks the server to re-run this "
        "exact spec with the **same seed**, over the **recorded effective "
        "window**, and — for intraday runs — with the **recorded per-session "
        "bar resolution pinned**. A fresh run made in the app may legitimately "
        "differ: the lake deepens nightly and sessions can upgrade to finer "
        "bars (that is the product's self-improvement contract). A *replay* "
        "never silently re-resolves — it either reproduces the recorded run "
        "or names what diverged.",
        "",
        f"Seed: `{grid.get('seed')}`",
        "",
    ]
    runs = payload.get("resolutionRuns")
    if runs:
        lines += ["Recorded per-session resolution (pinned by the replay):", "",
                  "| first | last | sessions | resolution |", "|---|---|---|---|"]
        for r in runs:
            lines.append(f"| {r.get('first')} | {r.get('last')} | "
                         f"{r.get('sessions')} | {r.get('resolution')} |")
        lines.append("")
    elif payload.get("clock") not in (None, "daily"):
        lines += ["*This run carries no per-session resolution record "
                  "(pre-FX.1) — the replay pins the window and seed only.*", ""]
    return "\n".join(lines)


DISCLAIMER = ("*Personal research only — not financial advice. Backtests "
              "overstate live performance; nothing here is a recommendation "
              "to trade anything.*")

_SETUP_CODE = '''\
# ── setup: the deployed Skeptic API serves every number in this notebook ──
import os

import pandas as pd
import requests

API = os.environ.get("SKEPTIC_API", {api_base!r}).rstrip("/")
TOKEN = os.environ.get("SKEPTIC_ACCESS_TOKEN")
RUN_ID = {run_id!r}

if not TOKEN:
    raise SystemExit(
        "Set SKEPTIC_ACCESS_TOKEN in the environment first — the API is "
        "token-gated and this notebook never embeds credentials."
    )


def api(path, method="GET", **kwargs):
    r = requests.request(method, f"{{API}}{{path}}",
                         headers={{"Authorization": f"Bearer {{TOKEN}}"}},
                         timeout=120, **kwargs)
    r.raise_for_status()
    return r.json()


run = api(f"/api/runs/{{RUN_ID}}")
print(run["name"])
print(run["meta"])
'''

_HEADLINE_CODE = '''\
# ── headline stats, exactly as the app shows them. Their data window is
#    named in the meta line printed above (guardrail: every surface that
#    shows results shows the window they were computed on) ──
pd.DataFrame([{t["l"]: t["v"] for t in run["mtiles"]}])
'''

_EQUITY_CODE = '''\
# ── equity and drawdown, computed on the window named above.
#    Series rows are {"t": iso_date, "v": value} ──
import matplotlib.pyplot as plt

eq = pd.DataFrame(run["equitySeries"])
dd = pd.DataFrame(run["drawdownSeries"])
print(len(eq), "equity points ·", len(dd), "drawdown points ·",
      "last equity", eq["v"].iloc[-1])
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True,
                               height_ratios=[3, 1])
ax1.plot(pd.to_datetime(eq["t"]), eq["v"])
ax1.set_title(f'{run["name"]} — equity (stored run)')
ax2.fill_between(pd.to_datetime(dd["t"]), dd["v"], 0, alpha=0.4)
ax2.set_title("drawdown")
plt.tight_layout()
'''

_TRADES_CODE = '''\
# ── the complete trade log is inspectable; skips carry reasons ──
trades = pd.DataFrame(run["trades"])
print(run["tradeHeader"])
if run.get("skipReasons"):
    print("skip reasons:", run["skipReasons"])
trades.head(15)
'''

_FILLS_CODE = '''\
# ── fill provenance: which quote record priced each fill, and how deep ──
print("fill sources:", run.get("fillSources"))
liq = run.get("liquidity")
if liq:
    for key, value in liq.items():
        print(f"{key}: {value}")
else:
    print("no liquidity profile on this run")
'''

_AGREEMENT_CODE = '''\
# ── cross-source agreement (F7): how well independent vendors agree on
#    the sessions this run touched — reported, never blended into a score ──
dc = run.get("dataConfidence")
if dc is None:
    print("no cross-source confidence block on this run")
else:
    pairs = dc.get("pairs") if isinstance(dc, dict) else None
    if isinstance(pairs, list) and pairs:
        print("per-pair data agreement on touched sessions:")
        print(pd.DataFrame(pairs).to_string(index=False))
    else:
        print(dc)
'''

_LADDER_CODE = '''\
# ── scale-in depth attribution (D5b): P&L by ladder depth, and whether
#    the deep adds themselves are net negative — both views tie out ──
ld = run.get("ladderDepth")
if ld is None:
    print("not a scale-in run — no depth table")
else:
    print("per-tier (baskets grouped by max depth reached):")
    print(pd.DataFrame(ld.get("tiers", [])).to_string(index=False))
    print()
    print("marginal per rung (P&L attributable to fills added AT the depth):")
    print(pd.DataFrame(ld.get("rungs", [])).to_string(index=False))
'''

_HONESTY_CODE = '''\
# ── the anti-overfitting gauntlet, verbatim from the stored run ──
h = run.get("honesty") or {}
print("in-sample sharpe:", h.get("isSharpe"),
      "· out-of-sample:", h.get("oosSharpe"))
for note in h.get("notes", []):
    print("-", note)
print()
print("Monte Carlo terminal equity:", run.get("mcTerm"))
sens = run.get("sensitivityDetail") or []
if sens:
    print()
    print("sensitivity sweep cells (sharpe per nudged value):")
    for row in sens:
        cells = " ".join(f'{c["label"]}={c["sharpe"]}' for c in row["cells"])
        print(f'  {row["name"]}: {cells}')
'''

_VERDICT_CODE = '''\
# ── the verdict — grounded: every number in it exists in the stats ──
v = run["verdict"]
for key in ("headline", "survived", "evidence", "breaks", "caveat"):
    if v.get(key):
        print(v[key])
print()
print("Not financial advice. Backtests overstate live performance.")
'''

_REPRODUCE_CODE = '''\
# ── deterministic re-execution: same spec, same seed, recorded window,
#    recorded per-session resolution PINNED server-side ──
import time

kick = api(f"/api/runs/{RUN_ID}/reproduce", method="POST")
print(kick)
# poll until the server's own 30-minute staleness window would declare
# the job dead anyway — the two deadlines deliberately agree
deadline = time.time() + 30 * 60
report = api(f"/api/runs/{RUN_ID}/reproduce")
while report.get("status") == "reproducing" and time.time() < deadline:
    time.sleep(6)
    report = api(f"/api/runs/{RUN_ID}/reproduce")
if report.get("status") == "reproducing":
    raise SystemExit("reproduce still running — re-run this cell in a minute")
if report.get("error"):
    print("REPRODUCE REFUSED / FAILED:")
    print(report["error"])
else:
    rows = report.get("compared", [])
    print(pd.DataFrame(rows))
    print()
    print("match within tolerance:", report.get("match"))
    if report.get("unevaluable"):
        print("not comparable (never recorded on the stored run):",
              report["unevaluable"])
    if report.get("resolution_divergence"):
        print("resolution divergence (disclosed, never silent):",
              report["resolution_divergence"])
    print("build then:", report.get("build_then"),
          "· build now:", report.get("build_now"))
'''


def build_notebook(
    *,
    run_id: str,
    name: str,
    payload: dict[str, Any],
    provenance: dict[str, Any],
    grid: dict[str, Any],
    api_base: str,
    sweep_notes: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble the .ipynb dict. `grid` is the decision grid derived from
    the VALIDATED spec (app.api.provenance.derived_boxes) — the spec is
    what ran, so the table can never disagree with the engine."""
    cells: list[dict[str, Any]] = []
    counter = count()  # one flat sequence — the id carries no type info,
    # cell_type already does (review finding: a typed prefix over a shared
    # counter reads as per-type numbering and its gaps look like bugs)

    def md(source: str) -> None:
        cells.append(_cell("markdown", source, f"cell-{next(counter)}"))

    def code(source: str) -> None:
        cells.append(_cell("code", source, f"cell-{next(counter)}"))

    md(f"# {name}\n\n"
       f"Skeptic research notebook · run `{run_id}`\n\n{DISCLAIMER}")
    md(_provenance_md(provenance, grid))
    md(_reproducibility_md(payload, grid))
    md("## The numbers\n\nEverything below is fetched live from the same "
       "API the app uses — the stored run is the single source of truth.")
    code(_SETUP_CODE.format(api_base=api_base, run_id=run_id))
    code(_HEADLINE_CODE)
    code(_EQUITY_CODE)
    code(_TRADES_CODE)
    code(_FILLS_CODE)
    code(_AGREEMENT_CODE)
    if payload.get("ladderDepth"):
        code(_LADDER_CODE)
    gauntlet_md = (
        "## The honesty gauntlet\n\n"
        "Out-of-sample split, walk-forward, Monte Carlo (seeded — same seed, "
        "same fan), threshold sensitivity with its coverage disclosure, and "
        "the deflated Sharpe's multiple-testing tax. Thin samples are never "
        "blessed: below the evidence bar the verdict is capped at "
        "*insufficient evidence* no matter how good the numbers look."
    )
    if sweep_notes:
        gauntlet_md += (
            "\n\n**Sweep coverage, disclosed** (what the sensitivity stage "
            "did and did NOT probe — absence is never a free pass):\n\n"
            + "\n".join(f"- {note}" for note in sweep_notes)
        )
    md(gauntlet_md)
    code(_HONESTY_CODE)
    code(_VERDICT_CODE)
    md("## Prove it again\n\n"
       "The cell below re-executes the run server-side under the "
       "reproducibility contract above and compares headline stats within "
       "tolerance. A mismatch is REPORTED with its likely cause (lake "
       "drift, build change), never papered over.")
    code(_REPRODUCE_CODE)
    md(f"---\n\n{DISCLAIMER}\n\nGenerated by Skeptic — the honesty layer "
       "is the product.")

    return {
        "nbformat": NBFORMAT,
        "nbformat_minor": NBFORMAT_MINOR,
        "metadata": {
            "language_info": {"name": "python"},
            "skeptic": {"run_id": run_id, "kind": "run-export", "v": 1},
        },
        "cells": cells,
    }
