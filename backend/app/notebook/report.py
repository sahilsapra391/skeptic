"""Run → self-contained HTML report (the share-with-anyone export).

The notebook (builder.py) is the EXECUTABLE artifact — reproduce proof,
live cells, analyst medium. This is the readable one: a single HTML file
anyone can open, on-brand, print-to-PDF clean (owner ask 2026-07-14:
"a format people actually know how to use"). Same story, same order:
how the strategy was agreed → the numbers with their window → the
honesty gauntlet → the verdict → the reproducibility appendix.

Rules the document keeps:
  * Static truth. Everything is rendered from the STORED run at export
    time — no live calls, so the file stands alone and never drifts from
    what the app showed.
  * Every user-derived string is HTML-escaped (the prompt and answers
    are client-captured display data — provenance.py's trust boundary).
  * Three voices only (owner typography directive): Newsreader for the
    headline moments, Archivo for prose, IBM Plex Mono for data. Loaded
    from Google Fonts with system fallbacks — offline/print degrades,
    never substitutes a fourth family.
  * Charts follow the app's monochrome ink language: single-series
    neutral marks (contrast-validated ≥3:1 on the light surface; the
    categorical-palette checks don't apply to a lone gray mark by the
    validator's own scope note). No P/L color near the verdict.
  * The disclaimer opens and closes the file (legal rails), and every
    results section carries the data window (guardrail #6).
  * Deterministic: same run row → byte-identical HTML.
"""

from __future__ import annotations

import html
import json
from typing import Any

_INK = "#16181d"
_INK2 = "#34383f"
_INK3 = "#5a616b"
_INK4 = "#8a919b"
_GRID = "#e7e8e6"
_SURFACE = "#fcfcfb"

_CSS = f"""
:root {{ color-scheme: light; }}
* {{ box-sizing: border-box; margin: 0; }}
body {{
  font-family: Archivo, system-ui, -apple-system, sans-serif;
  color: {_INK}; background: {_SURFACE};
  max-width: 820px; margin: 0 auto; padding: 48px 24px 64px;
  line-height: 1.55; font-size: 15px;
}}
h1, h2, .verdict-headline {{
  font-family: Newsreader, Georgia, serif; font-weight: 500;
  line-height: 1.15;
}}
h1 {{ font-size: 34px; margin: 6px 0 4px; }}
h2 {{ font-size: 22px; margin: 40px 0 12px; }}
.mono, td.num, .meta, .tile b, table {{
  font-family: "IBM Plex Mono", ui-monospace, Menlo, monospace;
}}
.meta {{ color: {_INK3}; font-size: 12px; }}
.disclaimer {{
  color: {_INK3}; font-size: 12.5px; font-style: italic;
  border-top: 1px solid {_GRID}; border-bottom: 1px solid {_GRID};
  padding: 8px 0; margin: 14px 0;
}}
.quote {{
  border-left: 3px solid {_GRID}; padding: 6px 14px; margin: 10px 0;
  white-space: pre-wrap; overflow-wrap: anywhere;
}}
.qa {{ margin: 10px 0 10px 6px; }}
.qa dt {{ font-weight: 600; margin-top: 8px; }}
.qa dd {{ margin: 2px 0 0 18px; color: {_INK2}; }}
table {{ border-collapse: collapse; width: 100%; font-size: 12.5px; margin: 10px 0; }}
th {{ text-align: left; color: {_INK3}; font-weight: 500;
     border-bottom: 1px solid {_GRID}; padding: 4px 8px 4px 0; }}
td {{ border-bottom: 1px solid {_GRID}; padding: 4px 8px 4px 0;
     vertical-align: top; overflow-wrap: anywhere; }}
td.key {{ white-space: nowrap; color: {_INK3}; width: 1%;
         padding-right: 18px; }}
td.num {{ text-align: right; white-space: nowrap; }}
.tiles {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 14px 0; }}
.tile {{
  border: 1px solid {_GRID}; padding: 10px 14px; min-width: 108px;
}}
.tile b {{ display: block; font-size: 19px; font-weight: 500; }}
.tile span {{ font-size: 10.5px; color: {_INK3}; letter-spacing: 0.06em; }}
.note {{ color: {_INK2}; font-size: 13.5px; }}
ul {{ padding-left: 20px; }}
li {{ margin: 3px 0; }}
figure {{ margin: 16px 0; }}
figcaption {{ font-size: 12px; color: {_INK3}; margin-top: 4px;
             font-family: "IBM Plex Mono", ui-monospace, monospace; }}
svg {{ width: 100%; height: auto; display: block; }}
.verdict-headline {{ font-size: 26px; margin: 10px 0; }}
.footer {{ margin-top: 48px; }}
@media print {{
  body {{ padding: 0; max-width: none; font-size: 12.5px; }}
  h2 {{ break-after: avoid; }}
  section, figure, table {{ break-inside: avoid; }}
  a {{ color: inherit; text-decoration: none; }}
}}
"""

_FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
          '<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@4'
          '00;600&family=IBM+Plex+Mono:wght@400;500&family=Newsreader:opsz,wgh'
          't@6..72,500&display=swap" rel="stylesheet">')

DISCLAIMER = ("Personal research only — not financial advice. Backtests "
              "overstate live performance; nothing here is a recommendation "
              "to trade anything.")


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _series_svg(series: list[dict[str, Any]] | None, *, kind: str,
                caption: str) -> str:
    """Single-series inline SVG in the app's monochrome ink language:
    2px line (or baseline-anchored area for drawdown), three recessive
    gridlines, min/max + first/last labels in mono ink — one axis, no
    legend (a single series is named by its caption). Points are the
    payload's _downsample rows: {"t": iso_date, "v": value}."""
    if not series or len(series) < 2:
        return ""
    values = [float(p["v"]) for p in series]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    width, height, pad_l, pad_r, pad_y = 800, 220, 8, 8, 18
    inner_w, inner_h = width - pad_l - pad_r, height - 2 * pad_y
    n = len(values)

    def x(i: int) -> float:
        return pad_l + inner_w * i / (n - 1)

    def y(v: float) -> float:
        return pad_y + inner_h * (1 - (v - lo) / span)

    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    grid = "".join(
        f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{gy:.1f}" y2="{gy:.1f}" '
        f'stroke="{_GRID}" stroke-width="1"/>'
        for gy in (pad_y, pad_y + inner_h / 2, pad_y + inner_h)
    )
    if kind == "area":
        base = y(min(0.0, lo) if lo < 0 else lo)
        mark = (f'<path d="M {points.split(" ")[0]} L '
                + " L ".join(points.split(" ")[1:])
                + f' L {x(n - 1):.1f},{base:.1f} L {x(0):.1f},{base:.1f} Z" '
                  f'fill="{_INK4}" fill-opacity="0.28" stroke="{_INK2}" '
                  'stroke-width="1.5"/>')
    else:
        mark = (f'<polyline points="{points}" fill="none" stroke="{_INK2}" '
                'stroke-width="2" stroke-linejoin="round"/>')
    label_style = (f'font-family="IBM Plex Mono, monospace" font-size="11" '
                   f'fill="{_INK3}"')
    labels = (
        f'<text x="{pad_l}" y="{pad_y - 5}" {label_style}>{hi:,.0f}</text>'
        f'<text x="{pad_l}" y="{height - 3}" {label_style}>{lo:,.0f}</text>'
        f'<text x="{width / 2:.0f}" y="{height - 3}" text-anchor="middle" '
        f'{label_style}>{_esc(series[0]["t"])} → {_esc(series[-1]["t"])}</text>'
    )
    return (f'<figure><svg viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="{_esc(caption)}"><title>{_esc(caption)}</title>'
            f"{grid}{mark}{labels}</svg>"
            f"<figcaption>{_esc(caption)}</figcaption></figure>")


def _rows(pairs: list[tuple[str, Any]]) -> str:
    """Label/value rows: labels never wrap (shrink-to-fit key column),
    values wrap anywhere — a long JSON value must fold, never force a
    horizontal scrollbar (browser-verified failure mode)."""
    out = []
    for label, value in pairs:
        if value in (None, "", [], {}):
            continue
        rendered = value if isinstance(value, str) else json.dumps(value)
        out.append(f"<tr><td class=\"key\">{_esc(label)}</td>"
                   f"<td>{_esc(rendered)}</td></tr>")
    return "".join(out)


def _provenance_html(record: dict[str, Any], grid: dict[str, Any]) -> str:
    parts = ["<h2>How this strategy was agreed</h2>"]
    origin = record.get("origin", "user")
    if origin != "user":
        note = record.get("note") or f"automatic run ({origin})"
        parts.append(f'<p class="note"><em>{_esc(note)}</em></p>')
    if record.get("derived"):
        parts.append('<p class="note"><em>Setup story derived from the '
                     "stored spec — this run predates provenance recording, "
                     "so the clarifying conversation was never captured "
                     "(and is not invented).</em></p>")
    prompt = (record.get("prompt") or {}).get("text")
    if prompt:
        parts.append("<p><strong>The ask, verbatim:</strong></p>")
        parts.append(f'<div class="quote mono">{_esc(prompt)}</div>')
    conversation = record.get("conversation") or []
    if conversation:
        parts.append("<p><strong>Clarified before anything ran:</strong></p>")
        qa = ['<dl class="qa">']
        for ev in conversation:
            if ev.get("kind") == "question":
                qa.append(f"<dt>Q: {_esc(ev.get('question', ''))}</dt>")
            elif ev.get("kind") == "answer":
                qa.append(f"<dd>A: {_esc(ev.get('answer', ''))}</dd>")
        qa.append("</dl>")
        parts.append("".join(qa))
        dropped = record.get("truncated", {}).get("dropped_events")
        if dropped:
            parts.append(f'<p class="note"><em>({_esc(dropped)} further '
                         "exchange(s) not shown — size-capped at recording "
                         "time.)</em></p>")
    parts.append("<p><strong>The decision grid that ran (from the "
                 "validated spec):</strong></p>")
    grid_pairs = [(key.replace("_", " "), grid.get(key))
                  for key in ("ticker", "structure", "clock", "resolution",
                              "expiration_selection", "schedule",
                              "conditions", "scale_in",
                              "max_concurrent_positions", "exit", "sizing",
                              "costs", "window", "initial_capital", "seed")]
    parts.append(f"<table><tbody>{_rows(grid_pairs)}</tbody></table>")
    mech = record.get("mechanics") or {}
    if mech:
        build = mech.get("build") or {}
        parts.append("<p><strong>Mechanics (measured, not estimated):"
                     "</strong></p>")
        parts.append("<table><tbody>" + _rows([
            ("clock", mech.get("clock")),
            ("sessions", mech.get("sessions")),
            ("effective window",
             f"{mech.get('effective_start', '?')} → "
             f"{mech.get('effective_end', '?')}"),
            ("resolution mix", mech.get("resolution_mix")),
            ("engine / gauntlet",
             f"{mech.get('engine_s', '?')}s / {mech.get('gauntlet_s', '?')}s"),
            ("build", build.get("commit") or "local"),
            ("spec version", build.get("spec_version")),
            ("fill model", build.get("fill_model")),
        ]) + "</tbody></table>")
    return "".join(parts)


def _honesty_html(payload: dict[str, Any],
                  sweep_notes: list[str] | None) -> str:
    parts = ["<h2>The honesty gauntlet</h2>",
             '<p class="note">Out-of-sample split, walk-forward, seeded '
             "Monte Carlo, threshold sensitivity with its coverage "
             "disclosure, and the deflated Sharpe's multiple-testing tax. "
             "Thin samples are never blessed: below the evidence bar the "
             "verdict is capped at <em>insufficient evidence</em> no matter "
             "how good the numbers look.</p>"]
    h = payload.get("honesty") or {}
    stat_bits = []
    if h.get("isSharpe") is not None:
        stat_bits.append(("in-sample sharpe", h.get("isSharpe")))
    if h.get("oosSharpe") is not None:
        stat_bits.append(("out-of-sample sharpe", h.get("oosSharpe")))
    mc = payload.get("mcTerm") or {}
    for key, label in (("p95", "MC terminal p95"), ("p50", "MC terminal p50"),
                       ("p05", "MC terminal p05")):
        if mc.get(key) is not None:
            stat_bits.append((label, mc[key]))
    if stat_bits:
        parts.append(f"<table><tbody>{_rows(stat_bits)}</tbody></table>")
    notes = h.get("notes") or []
    if notes:
        parts.append("<ul>" + "".join(f"<li>{_esc(n)}</li>" for n in notes)
                     + "</ul>")
    sens = payload.get("sensitivityDetail") or []
    if sens:
        parts.append("<p><strong>Sensitivity sweep cells (sharpe per "
                     "nudged value):</strong></p>")
        rows = []
        for row in sens:
            cells = " · ".join(f'{c.get("label")}={c.get("sharpe")}'
                               for c in row.get("cells", []))
            rows.append((str(row.get("name", "")), cells))
        parts.append(f"<table><tbody>{_rows(rows)}</tbody></table>")
    if sweep_notes:
        parts.append("<p><strong>Sweep coverage, disclosed</strong> "
                     '<span class="note">(what the sensitivity stage did '
                     "and did not probe — absence is never a free "
                     "pass):</span></p>")
        parts.append("<ul>" + "".join(f"<li>{_esc(n)}</li>"
                                      for n in sweep_notes) + "</ul>")
    return "".join(parts)


def build_report(
    *,
    run_id: str,
    name: str,
    payload: dict[str, Any],
    provenance: dict[str, Any],
    grid: dict[str, Any],
    sweep_notes: list[str] | None = None,
) -> str:
    """Assemble the standalone HTML document."""
    parts: list[str] = []
    parts.append(f"<h1>{_esc(name)}</h1>")
    parts.append(f'<p class="meta">Skeptic research report · run '
                 f"{_esc(run_id)}</p>")
    parts.append(f'<p class="meta">{_esc(payload.get("meta") or "")}</p>')
    parts.append(f'<p class="disclaimer">{_esc(DISCLAIMER)}</p>')

    parts.append(f"<section>{_provenance_html(provenance, grid)}</section>")

    parts.append("<section><h2>The numbers</h2>")
    window_note = ("Computed on the effective window named in the meta "
                   "line above — a surface that shows results shows the "
                   "data they were computed on.")
    parts.append(f'<p class="note">{_esc(window_note)}</p>')
    tiles = "".join(
        f'<div class="tile"><b>{_esc(t.get("v", "—"))}</b>'
        f'<span>{_esc(t.get("l", ""))}</span></div>'
        for t in payload.get("mtiles") or []
    )
    if tiles:
        parts.append(f'<div class="tiles">{tiles}</div>')
    parts.append(_series_svg(payload.get("equitySeries"), kind="line",
                             caption="Equity — stored run"))
    parts.append(_series_svg(payload.get("drawdownSeries"), kind="area",
                             caption="Drawdown"))
    parts.append("</section>")

    parts.append("<section><h2>Trades and fills</h2>")
    parts.append(f'<p class="mono note">{_esc(payload.get("tradeHeader") or "")}</p>')
    if payload.get("skipReasons"):
        parts.append("<table><tbody>"
                     + _rows(sorted(payload["skipReasons"].items()))
                     + "</tbody></table>")
    if payload.get("fillSources"):
        parts.append("<p><strong>Fill sources</strong> "
                     '<span class="note">(which quote record priced each '
                     "leg fill):</span></p>")
        parts.append("<table><tbody>"
                     + _rows(sorted(payload["fillSources"].items()))
                     + "</tbody></table>")
    liq = payload.get("liquidity")
    if liq:
        parts.append("<p><strong>Liquidity profile:</strong></p>")
        parts.append(f"<table><tbody>{_rows(sorted(liq.items()))}"
                     "</tbody></table>")
    dc = payload.get("dataConfidence")
    if isinstance(dc, dict) and dc.get("pairs"):
        parts.append("<p><strong>Cross-source agreement on touched "
                     "sessions</strong> <span class=\"note\">(reported, "
                     "never blended into a score):</span></p>")
        pair_rows = [(str(p.get("pair", p.get("label", "?"))),
                      json.dumps({k: v for k, v in p.items()
                                  if k not in ("pair", "label")}))
                     for p in dc["pairs"]]
        parts.append(f"<table><tbody>{_rows(pair_rows)}</tbody></table>")
    ld = payload.get("ladderDepth")
    if ld:
        parts.append("<p><strong>Scale-in depth attribution (P&amp;L by "
                     "ladder depth — both views tie out):</strong></p>")
        for label, key in (("per-tier", "tiers"), ("marginal per rung", "rungs")):
            rows = ld.get(key) or []
            if rows:
                parts.append(f'<p class="note">{label}:</p><table><tbody>'
                             + _rows([(json.dumps(r), "") for r in rows])
                             + "</tbody></table>")
    parts.append("</section>")

    parts.append(f"<section>{_honesty_html(payload, sweep_notes)}</section>")

    verdict = payload.get("verdict") or {}
    parts.append("<section><h2>The verdict</h2>")
    if verdict.get("headline"):
        parts.append(f'<p class="verdict-headline">'
                     f'{_esc(verdict["headline"])}</p>')
    for key in ("survived", "evidence", "breaks", "caveat"):
        if verdict.get(key):
            parts.append(f"<p>{_esc(verdict[key])}</p>")
    parts.append("</section>")

    parts.append("<section><h2>Reproducibility appendix</h2>")
    parts.append('<p class="note">This run re-executes deterministically: '
                 "same spec, same seed, the recorded effective window, and "
                 "— for intraday runs — the recorded per-session bar "
                 "resolution pinned. The executable proof lives in the "
                 "notebook export (same run, .ipynb); a fresh app run may "
                 "legitimately differ as the lake deepens — a replay never "
                 "silently re-resolves.</p>")
    parts.append("<table><tbody>" + _rows([
        ("seed", grid.get("seed")),
        ("clock", payload.get("clock")),
        ("resolution mode", payload.get("resolutionMode")),
    ]) + "</tbody></table>")
    runs = payload.get("resolutionRuns")
    if runs:
        parts.append("<p><strong>Recorded per-session resolution "
                     "(pinned by a replay):</strong></p><table><thead>"
                     "<tr><th>first</th><th>last</th><th>sessions</th>"
                     "<th>resolution</th></tr></thead><tbody>")
        for r in runs:
            parts.append(
                f"<tr><td>{_esc(r.get('first'))}</td>"
                f"<td>{_esc(r.get('last'))}</td>"
                f"<td class=\"num\">{_esc(r.get('sessions'))}</td>"
                f"<td>{_esc(r.get('resolution'))}</td></tr>")
        parts.append("</tbody></table>")
    parts.append("</section>")

    parts.append(f'<div class="footer"><p class="disclaimer">'
                 f"{_esc(DISCLAIMER)}</p>"
                 '<p class="meta">Generated by Skeptic — the honesty layer '
                 "is the product.</p></div>")

    body = "".join(parts)
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width, "
            f"initial-scale=1\"><title>{_esc(name)} — Skeptic</title>"
            f"{_FONTS}<style>{_CSS}</style></head><body>{body}</body></html>")
