/**
 * DEMO fixtures — server-side only, used by the API route handlers when the
 * backend answers 501 (engine/parser milestones M2–M4 not built yet) or is
 * unreachable in dev.
 *
 * Every payload from this file carries `demo: true` and the UI labels it.
 * Numbers here are the approved design's illustrative content
 * (docs/design/Skeptic App.dc.html) — they are NOT computed results and are
 * never presented as such. This file is deleted the day M2–M4 land.
 */

import type {
  HonestyPanels,
  MetricTile,
  RunPayload,
  RunSummary,
  SpecDraft,
  Structure,
  Ticker,
  TradeRow,
  VerdictPayload,
} from "./types";

// ---------------------------------------------------------------- fixtures

const EQUITY_POINTS =
  "0,176 34,170 69,173 103,162 137,166 172,154 206,159 240,144 274,150 309,132 343,140 " +
  "377,122 412,129 446,112 480,118 515,98 549,106 583,90 600,94 617,86 652,93 686,78 " +
  "720,86 755,68 789,76 824,58 860,64";

const DRAWDOWN_POINTS =
  "0,6 80,6 120,20 160,6 240,6 300,32 360,6 430,6 470,16 540,6 620,6 660,40 720,6 780,13 860,6";

const MC = {
  p95: "0,80 100,58 200,36 300,18 400,6",
  p50: "0,80 100,68 200,55 300,44 400,35",
  p05: "0,80 100,84 200,86 300,89 400,92",
};

const SENSITIVITY = [
  [0.1, 0.16, 0.28, 0.44, 0.86, 0.3, 0.14, 0.1, 0.07],
  [0.12, 0.2, 0.34, 0.5, 0.92, 0.26, 0.12, 0.08, 0.06],
];

const WF_FADES = [46, 30, -18, 52, 38, -16, 44, 34, 50, -22, 40, 46];

const TRADES: TradeRow[] = [
  { d: "Jun 26 ’26", a: "CLOSE", det: "−1P 543 · opened Jun 5 · cr $3.42", pl: "+$164", plSign: "pos", n: "profit target 50%" },
  { d: "Jun 20 ’26", a: "OPEN", det: "−1P 545 · 45 DTE · Δ.30 · cr $3.38", pl: "—", plSign: "none", n: "schedule: weekly" },
  { d: "Jun 13 ’26", a: "SKIP", det: "—", pl: "—", plSign: "none", n: "zero bid on .30Δ strike", skip: true },
  { d: "Jun 6 ’26", a: "CLOSE", det: "−1P 538 · opened May 16", pl: "−$389", plSign: "neg", n: "stop: 2× credit" },
  { d: "May 30 ’26", a: "CLOSE", det: "−1P 531 · opened May 9", pl: "+$171", plSign: "pos", n: "profit target 50%" },
  { d: "May 23 ’26", a: "SKIP", det: "—", pl: "—", plSign: "none", n: "spread 14% of mid — quote quality", skip: true },
  { d: "May 16 ’26", a: "CLOSE", det: "−1P 527 · opened Apr 25", pl: "+$158", plSign: "pos", n: "21 DTE time exit" },
  { d: "May 9 ’26", a: "CLOSE", det: "−1P 522 · opened Apr 18", pl: "+$149", plSign: "pos", n: "profit target 50%" },
];

function wf(heights: number[]): HonestyPanels["wf"] {
  return heights.map((h) => ({ h: Math.abs(h), pos: h > 0 }));
}

interface FixtureBundle {
  verdict: VerdictPayload;
  mtiles: MetricTile[];
  honesty: HonestyPanels;
}

function fadesOos(): FixtureBundle {
  return {
    verdict: {
      kind: "fades-oos",
      refusal: false,
      headline: "Edge fades out-of-sample. What’s left is thin.",
      survived: "2 OF 5 ATTACKS SURVIVED",
      band: { left: "18%", width: "42%" },
      marker: "34%",
      chips: ["OOS ✗", "walk-fwd ✓", "monte carlo ✓", "sensitivity ✗", "sample ✗"],
      evidence: [
        "Walk-forward: 9 of 12 windows positive",
        "Monte Carlo median path stays above breakeven",
      ],
      breaks: [
        "In-sample Sharpe 1.42 → 0.44 out-of-sample (−69%)",
        "11 high-VIX weeks account for 58% of all P/L — calm markets are flat-to-negative",
      ],
      caveat:
        "68 OOS trades · one volatility regime · self-collected data. Treat as suggestive, not proven.",
    },
    mtiles: [
      { v: "11.2%", l: "CAGR" },
      { v: "0.87", l: "SHARPE" },
      { v: "1.21", l: "SORTINO" },
      { v: "−18.4%", l: "MAX DD", neg: true },
      { v: "71%", l: "WIN RATE" },
      { v: "1.61", l: "P·FACTOR" },
    ],
    honesty: {
      isSharpe: "1.42",
      oosSharpe: "0.44",
      bar1: "88%",
      bar2: "27%",
      wf: wf(WF_FADES),
      notes: [
        "−69% decay — fails ✗",
        "9 / 12 windows positive ✓",
        "5th pctile −31% ⚠ · median +9%",
        "Δ .30 optimum is a cliff — neighbors lose ✗",
      ],
    },
  };
}

function survives(): FixtureBundle {
  return {
    verdict: {
      kind: "survives",
      refusal: false,
      headline:
        "Survives every statistical attack. One regime of data still caps trust at “robust.”",
      survived: "4 OF 5 ATTACKS SURVIVED",
      band: { left: "52%", width: "34%" },
      marker: "66%",
      chips: ["OOS ✓", "walk-fwd ✓", "monte carlo ✓", "sensitivity ✓", "sample ✗"],
      evidence: [
        "OOS Sharpe 1.18 vs 1.31 in-sample — only −10% decay",
        "Walk-forward: 11 of 12 windows positive",
        "Δ sweep is a plateau — neighbors hold up",
      ],
      breaks: [
        "Sample spans one volatility regime (’24–’26)",
        "Monte Carlo 5th percentile: −14% drawdown — survivable, not painless",
      ],
      caveat:
        "Strong within the record we have — but the record is short. This verdict re-runs automatically as data accrues.",
    },
    mtiles: [
      { v: "13.8%", l: "CAGR" },
      { v: "1.18", l: "SHARPE" },
      { v: "1.64", l: "SORTINO" },
      { v: "−11.2%", l: "MAX DD", neg: true },
      { v: "68%", l: "WIN RATE" },
      { v: "1.92", l: "P·FACTOR" },
    ],
    honesty: {
      isSharpe: "1.31",
      oosSharpe: "1.18",
      bar1: "90%",
      bar2: "82%",
      wf: wf([46, 30, 18, 52, 38, 16, 44, 34, 50, -22, 40, 46]),
      notes: [
        "−10% decay — holds ✓",
        "11 / 12 windows positive ✓",
        "5th pctile −14% · median +11% ✓",
        "Δ .25–.35 all profitable — plateau ✓",
      ],
    },
  };
}

function refusal(ticker: Ticker): FixtureBundle {
  return {
    verdict: {
      kind: "refusal",
      refusal: true,
      headline: `Verdict withheld. The ${ticker} options record began 2026-07-01 — days of data can’t answer this honestly.`,
      survived: "NOT EVALUATED",
      chips: [],
      evidence: [],
      breaks: [],
      caveat: "",
      refusalBody:
        "The sample spans one volatility regime and a handful of candidate trades. Any verdict now would be a guess wearing a lab coat. Raw simulation output is shown below, unblessed, for machinery-checking only.",
      refusalUnlock: "unlocks at ≥ 2 regimes or ≥ 24 months of coverage",
    },
    mtiles: [
      { v: "9.1%", l: "CAGR*" },
      { v: "0.94", l: "SHARPE*" },
      { v: "1.30", l: "SORTINO*" },
      { v: "−7.2%", l: "MAX DD*", neg: true },
      { v: "74%", l: "WIN RATE*" },
      { v: "1.70", l: "P·FACTOR*" },
    ],
    honesty: {
      isSharpe: "—",
      oosSharpe: "—",
      bar1: "0%",
      bar2: "0%",
      wf: [],
      notes: [
        "not run — sample too thin",
        "not run — sample too thin",
        "not run — sample too thin",
        "not run — sample too thin",
      ],
    },
  };
}

// ------------------------------------------------------------- demo parse

const TICKERS: Ticker[] = ["SPY", "QQQ", "IWM"];

/** Keyword extractor standing in for the M4 parser. It follows the one rule
 * that can't wait for M4: a missing exit produces a question, never a
 * default. Everything else it fills is rendered as an editable dial the
 * user confirms before anything runs. */
export function demoParse(text: string): SpecDraft {
  const t = text.toLowerCase();

  const ticker = (TICKERS.find((k) => t.includes(k.toLowerCase())) ?? "SPY") as Ticker;

  let structure: Structure = "short_put";
  if (t.includes("iron condor") || t.includes("condor")) structure = "iron_condor";
  else if (t.includes("covered call")) structure = "covered_call";
  else if (t.includes("call spread") || t.includes("call credit")) structure = "call_credit_spread";
  else if (t.includes("spread")) structure = "put_credit_spread";
  else if (/\bbuy\b.*\bcall\b|\blong\b.*\bcall\b/.test(t)) structure = "long_call";
  else if (/\bbuy\b.*\bput\b|\blong\b.*\bput\b/.test(t)) structure = "long_put";

  const deltaMatch = t.match(/(\d{2})\s*[- ]?\s*delta|\.(\d{2})\s*δ|(\d{2})\s*δ/);
  const rawDelta = deltaMatch
    ? parseInt(deltaMatch[1] ?? deltaMatch[2] ?? deltaMatch[3], 10)
    : 30;
  const strikeDelta = Math.min(50, Math.max(10, Math.round(rawDelta / 5) * 5));

  // entry DTE: skip matches that belong to an exit clause ("close at /
  // exit at / roll at / or 21 DTE") — those are exits, not tenor
  const dteCandidates = Array.from(t.matchAll(/(\d{1,3})\s*(?:dte|[- ]?days?)/g));
  const exitContext = /(?:close|exit|roll|stop|or|at)\s*(?:at\s*)?$/;
  const entryDte = dteCandidates.find(
    (m) => !exitContext.test(t.slice(Math.max(0, (m.index ?? 0) - 10), m.index)),
  );
  let dte = entryDte ? parseInt(entryDte[1], 10) : 45;
  if (dte < 7 || dte > 90) dte = 45;

  let cadence = "weekly · mon";
  if (t.includes("monthly") || t.includes("every month")) cadence = "monthly";
  else if (t.includes("daily") || t.includes("every day")) cadence = "daily";
  else if (t.includes("friday")) cadence = "weekly · fri";
  else if (t.includes("monday") || t.includes("week")) cadence = "weekly · mon";

  // exit: only what the text actually says — otherwise null and the spec
  // screen asks (guardrail #3, alive even in the demo)
  const parts: string[] = [];
  const profit = t.match(
    /(\d{1,3})\s*%\s*(?:profit|gain)|close at\s*(\d{1,3})\s*%|sell at\s*\+?(\d{1,3})\s*%/,
  );
  if (profit) parts.push(`${profit[1] ?? profit[2] ?? profit[3]}% profit`);
  const timeExit = t.match(/(?:or|at|exit at|roll at)\s*(\d{1,2})\s*(?:dte|days)/);
  if (timeExit) parts.push(`${timeExit[1]} DTE`);
  if (t.includes("expir")) parts.push("hold to expiry");
  const stop =
    t.match(/stop(?:\s*(?:at|loss))?\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*(x|%)/) ??
    t.match(/(\d+(?:\.\d+)?)\s*(x)\s*(?:credit\s+stop|credit|stop)/);
  if (stop) parts.push(`stop ${stop[1]}${stop[2] === "x" ? "×" : "%"}`);
  const exit = parts.length ? parts.join(" · ") : null;

  return {
    ticker,
    structure,
    strikeDelta,
    dte,
    cadence,
    size: "1 contract",
    exit,
    fromChart: false,
    quote: text,
  };
}

// --------------------------------------------------------------- run store

interface StoredRun {
  createdAt: number;
  payload: RunPayload;
}

const globalStore = globalThis as unknown as { __skepticDemoRuns?: Map<string, StoredRun> };
const runs: Map<string, StoredRun> = (globalStore.__skepticDemoRuns ??= new Map());

const STAGE_MS = 700;
const N_STAGES = 6;

function bundleFor(draft: SpecDraft): FixtureBundle {
  // Honest demo routing: SPY has 6.5y of chains in the lake, so it may show
  // a full (demo) verdict. QQQ/IWM chains began 2026-07-01 — the only honest
  // demo verdict is the refusal state.
  if (draft.ticker !== "SPY") return refusal(draft.ticker);
  return draft.structure === "put_credit_spread" ? survives() : fadesOos();
}

function runName(draft: SpecDraft): string {
  if (draft.fromChart) {
    const n = draft.examples ?? 1;
    return `${draft.ticker} pullback short put — taught by ${n} pinned example${n === 1 ? "" : "s"}`;
  }
  const structure = draft.structure.replace(/_/g, " ");
  return `${draft.ticker} .${draft.strikeDelta}Δ ${draft.cadence.startsWith("weekly") ? "weekly " : ""}${structure}`;
}

export function createDemoRun(draft: SpecDraft): string {
  const id = `demo-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`;
  const bundle = bundleFor(draft);
  const window =
    draft.ticker === "SPY" ? "Jan ’20 → Jul ’26" : "Jul 1 ’26 → Jul ’26";
  const payload: RunPayload = {
    id,
    demo: true,
    status: "running",
    stage: 0,
    name: runName(draft),
    meta: `${draft.ticker} · ${draft.structure.replace(/_/g, " ")} · run Jul 2 ’26 · effective window ${window} (bounded by coverage)`,
    spec: draft,
    verdict: bundle.verdict,
    mtiles: bundle.mtiles,
    equityPoints: EQUITY_POINTS,
    drawdownPoints: DRAWDOWN_POINTS,
    oosShadeX: 600,
    honesty: bundle.honesty,
    mc: MC,
    sensitivity: SENSITIVITY,
    tradeHeader: "Trade log — 412 filled · 37 skipped, with reasons",
    trades: TRADES,
  };
  runs.set(id, { createdAt: Date.now(), payload });
  return id;
}

export function getDemoRun(id: string): RunPayload | null {
  const seeded = SEEDED.find((s) => s.id === id);
  if (seeded) return seeded.payload;
  const stored = runs.get(id);
  if (!stored) return null;
  const stage = Math.min(N_STAGES, Math.floor((Date.now() - stored.createdAt) / STAGE_MS));
  return { ...stored.payload, stage, status: stage >= N_STAGES ? "done" : "running" };
}

export function demoAskAnswer(): string {
  return "Worst month: Apr ’25, −6.8% — the dip you anchored on. Remove it and CAGR drops 11.2% → 8.9%; the verdict does not change. Computed from this run’s trade log only — no new numbers were invented.";
}

// ------------------------------------------------------- seeded library

function seededRun(
  id: string,
  name: string,
  meta: string,
  draft: SpecDraft,
  bundle: FixtureBundle,
): { id: string; payload: RunPayload } {
  return {
    id,
    payload: {
      id,
      demo: true,
      status: "done",
      stage: N_STAGES,
      name,
      meta,
      spec: draft,
      verdict: bundle.verdict,
      mtiles: bundle.mtiles,
      equityPoints: EQUITY_POINTS,
      drawdownPoints: DRAWDOWN_POINTS,
      oosShadeX: 600,
      honesty: bundle.honesty,
      mc: MC,
      sensitivity: SENSITIVITY,
      tradeHeader: "Trade log — 412 filled · 37 skipped, with reasons",
      trades: TRADES,
    },
  };
}

const SEEDED = [
  seededRun(
    "demo-spread",
    "SPY 25Δ put credit spread",
    "SPY · put credit spread · run Jun 28 ’26 · effective window Jan ’20 → Jun ’26 (bounded by coverage)",
    {
      ticker: "SPY",
      structure: "put_credit_spread",
      strikeDelta: 25,
      dte: 45,
      cadence: "weekly · mon",
      size: "1 contract",
      exit: "50% profit",
      fromChart: false,
      quote: "sell a 25-delta put spread on SPY, $5 wide, 45 DTE, close at 50% profit",
    },
    survives(),
  ),
  seededRun(
    "demo-shortput",
    "SPY 30Δ weekly short put",
    "SPY · short put · run Jul 2 ’26 · effective window Jan ’20 → Jul ’26 (bounded by coverage)",
    {
      ticker: "SPY",
      structure: "short_put",
      strikeDelta: 30,
      dte: 45,
      cadence: "weekly · mon",
      size: "1 contract",
      exit: "50% profit · 21 DTE",
      fromChart: false,
      quote: "sell a 30-delta put on SPY every week, close at 50% profit or 21 days",
    },
    fadesOos(),
  ),
  seededRun(
    "demo-condor",
    "IWM 16Δ iron condor",
    "IWM · iron condor · run Jun 24 ’26 · effective window Jul 1 ’26 → Jul ’26 (bounded by coverage)",
    {
      ticker: "IWM",
      structure: "iron_condor",
      strikeDelta: 15,
      dte: 45,
      cadence: "monthly",
      size: "1 contract",
      exit: "21 DTE · stop 2×",
      fromChart: false,
      quote: "iron condor on IWM at 16 delta, 45 DTE, exit at 21 DTE or 2x credit stop",
    },
    refusal("IWM"),
  ),
];

export function listDemoRuns(): RunSummary[] {
  const librarySummaries: RunSummary[] = [
    {
      id: "demo-spread",
      demo: true,
      name: "SPY 25Δ put credit spread",
      meta: "Jun 28 ’26 · spread · 4/5 survived",
      quote: "“Survives every attack; one regime caps trust at robust.”",
      kind: "survives",
      band: { left: "52%", width: "34%" },
      marker: "66%",
    },
    {
      id: "demo-shortput",
      demo: true,
      name: "SPY 30Δ weekly short put",
      meta: "Jul 2 ’26 · short put · 2/5 survived",
      quote: "“Edge fades out-of-sample. What’s left is thin.”",
      kind: "fades-oos",
      band: { left: "18%", width: "42%" },
      marker: "34%",
    },
    {
      id: "demo-condor",
      demo: true,
      name: "IWM 16Δ iron condor",
      meta: "Jun 24 ’26 · condor · withheld",
      quote: "“Verdict withheld — days of IWM data can’t answer this honestly.”",
      kind: "refusal",
    },
  ];
  const created = Array.from(runs.entries())
    .filter(([, r]) => Date.now() - r.createdAt > N_STAGES * STAGE_MS)
    .sort((a, b) => b[1].createdAt - a[1].createdAt)
    .map(([id, r]) => ({
      id,
      demo: true,
      name: r.payload.name,
      meta: `Jul 2 ’26 · ${r.payload.spec?.structure.replace(/_/g, " ") ?? ""} · ${
        r.payload.verdict.refusal
          ? "withheld"
          : r.payload.verdict.survived.startsWith("4")
            ? "4/5 survived"
            : "2/5 survived"
      }`,
      quote: `“${r.payload.verdict.headline}”`,
      kind: r.payload.verdict.kind,
      band: r.payload.verdict.band,
      marker: r.payload.verdict.marker,
    }));
  return [...created, ...librarySummaries];
}
