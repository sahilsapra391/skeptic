/**
 * Shared types: the spec IR surface the UI edits, run payloads, and the
 * coverage payload from /api/data/coverage. The full StrategySpec JSON
 * mirrors docs/strategy-spec.schema.json (kept in lockstep with
 * backend/app/models/spec.py).
 */

export type Ticker = "SPY" | "QQQ" | "IWM";

export type Structure =
  | "short_put"
  | "put_credit_spread"
  | "call_credit_spread"
  | "iron_condor"
  | "covered_call"
  | "long_call"
  | "long_put";

export const STRUCTURE_LABEL: Record<Structure, string> = {
  short_put: "short put",
  put_credit_spread: "put credit spread",
  call_credit_spread: "call credit spread",
  iron_condor: "iron condor",
  covered_call: "covered call",
  long_call: "long call",
  long_put: "long put",
};

/** Structured entry trigger for chart-taught strategies — maps 1:1 onto a
 * spec `Condition`, so what the user edits is what the engine evaluates. */
export interface TriggerSpec {
  indicator: string; // schema Indicator value
  operator: string; // schema Operator value
  value: number;
  period?: number;
}

/** Editable display draft the composer/spec flow works with before a run. */
export interface SpecDraft {
  ticker: Ticker;
  structure: Structure;
  strikeDelta: number; // whole-number delta, 5..95 in steps of 5 (.05Δ steps)
  strikeLabel?: string | null; // non-delta selection from the parser ("ATM", "5% below spot")
  dte: number; // 0..50 (0 = 0DTE, refused at run until the minute engine)
  cadence: string; // e.g. "weekly · mon"
  size: string; // e.g. "1 contract"
  exit: string | null; // null = parser must ask, never guess
  fromChart: boolean;
  quote: string; // the user's words, verbatim — or the chart-teach summary
  anchor?: string; // chart mode: first pinned entry date
  trigger?: string; // chart mode: display label for the trigger
  triggerSpec?: TriggerSpec; // chart mode: the editable structured trigger
  examples?: number; // chart mode: pinned example count
}

export type VerdictKind = "fades-oos" | "survives" | "refusal" | "graded";

export interface VerdictPayload {
  kind: VerdictKind;
  refusal: boolean;
  headline: string;
  survived: string;
  band?: { left: string; width: string };
  marker?: string;
  chips: string[];
  evidence: string[];
  breaks: string[];
  caveat: string;
  refusalBody?: string;
  refusalUnlock?: string;
}

export interface MetricTile {
  v: string;
  l: string;
  /** true = this is a loss-side P/L number (max drawdown) — pl-neg token */
  neg?: boolean;
}

export interface WalkForwardWindow {
  h: number; // bar height px (design scale, max 56)
  pos: boolean; // profitable window? P/L tokens apply here
  t?: string; // real runs: "Jan 3 ’24 → Mar 5 ’24 · +2.1% · 6 trades"
}

export interface SensitivityCell {
  label: string; // the swept value, e.g. ".24Δ"
  sharpe: string; // "0.72" or "—"
  o: number; // heat opacity
}

export interface SensitivityRow {
  name: string;
  cls: string; // "plateau" | "cliff" | ""
  base: number; // index of the as-specced column
  cells: SensitivityCell[];
}

export interface HonestyPanels {
  isSharpe: string;
  oosSharpe: string;
  bar1: string; // IS bar width, e.g. "88%"
  bar2: string;
  wf: WalkForwardWindow[];
  notes: [string, string, string, string];
}

export interface TradeRow {
  d: string;
  a: "OPEN" | "CLOSE" | "SKIP";
  det: string;
  pl: string;
  plSign: "pos" | "neg" | "none";
  n: string;
  skip?: boolean;
}

export const GAUNTLET_STAGES: { t: string; n: string }[] = [
  { t: "Backtest", n: "fills at bid/ask + slippage, never mid" },
  { t: "In-sample / out-of-sample split", n: "70 / 30 chronological" },
  { t: "Walk-forward", n: "rolling windows" },
  { t: "Monte Carlo", n: "1,000 resamples of trade order" },
  { t: "Sensitivity sweep", n: "Δ 15 → 45 · DTE 30 → 60" },
  { t: "Verdict", n: "grounded in the numbers above" },
];

export interface SeriesPoint {
  t: string;
  v: number;
}

export interface RunPayload {
  id: string;
  demo: boolean;
  status: "running" | "done" | "error";
  stage: number; // 0..6, meaningful while running
  error?: string;
  name: string;
  meta: string;
  spec: SpecDraft | null;
  verdict: VerdictPayload;
  mtiles: MetricTile[];
  equityPoints: string;
  drawdownPoints: string;
  /** real runs ship raw series; the client shapes them into the charts */
  equitySeries?: SeriesPoint[];
  drawdownSeries?: SeriesPoint[];
  oosShadeX: number; // viewBox x where OOS shading starts (0..860)
  oosSplitDate?: string; // ISO date where the OOS window begins
  honesty: HonestyPanels;
  mc: { p95: string; p50: string; p05: string };
  mcTerm?: { p95: string; p50: string; p05: string }; // terminal $ per band
  sensitivity: number[][]; // opacity grid (5 cols per param for real runs)
  sensitivityRows?: string[]; // real runs: swept parameter names
  sensitivityDetail?: SensitivityRow[]; // real runs: cell-level sweep data
  recommendations?: string[]; // grounded improvements computed from this run
  previews?: string[]; // while running: real stats from finished stages
  /** retail-register text — same numbers, everyday words */
  retail?: {
    headline: string;
    survived: string;
    evidence: string[];
    breaks: string[];
    caveat: string;
    refusalBody?: string;
    refusalUnlock?: string;
    notes: [string, string, string, string];
    recommendations: string[];
  } | null;
  tradeHeader: string;
  trades: TradeRow[];
  askAnswer?: string;
}

export interface RunSummary {
  id: string;
  demo: boolean;
  name: string;
  meta: string;
  quote: string;
  kind: VerdictKind;
  band?: { left: string; width: string };
  marker?: string;
}

export interface CoverageRange {
  sessions: number;
  first: string;
  last: string;
  quarantined?: number;
  last_snapshot_ts?: string;
}

export interface CoveragePayload {
  generated_at: string;
  record_days: number;
  record_latest: string | null;
  chains: Record<Ticker, CoverageRange | null>;
  eod: Record<string, Record<Ticker, CoverageRange | null>>;
  minute_bars: Record<Ticker, CoverageRange | null>;
  intraday: Record<string, Record<Ticker, CoverageRange | null>>;
  underlying: Record<string, { rows: number; first: string; last: string } | null>;
  quality: Record<string, unknown>;
  dolthub: {
    verified_sessions: number;
    quarantined: number;
    archive_gaps: number;
    commit: string | null;
  };
  blind_spots: { id: string; text: string }[];
  sources_status: Record<string, boolean | string>;
}

export interface UnderlyingPoint {
  date: string;
  close: number;
}

export type ChartInterval =
  | "1m"
  | "2m"
  | "3m"
  | "5m"
  | "15m"
  | "30m"
  | "1h"
  | "4h"
  | "1d"
  | "1w";

export type ChartWindow = "1d" | "1w" | "1mo" | "3mo" | "ytd" | "1y" | "5y" | "all";

export interface Bar {
  t: string; // ISO with offset (ET)
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export type IndicatorSeries = (number | null)[] | Record<string, (number | null)[]>;

export interface BarsPayload {
  ticker: Ticker;
  interval: ChartInterval;
  window: ChartWindow;
  live: boolean;
  source: string;
  as_of: string | null;
  has_more: boolean;
  bars: Bar[];
  indicators: Record<string, IndicatorSeries>;
}

export interface ParseQuestion {
  id: string;
  question: string;
  options: string[];
}

export type ParseResult =
  | { status: "spec"; demo: boolean; draft: SpecDraft; spec?: Record<string, unknown> }
  | { status: "questions"; demo: boolean; questions: ParseQuestion[] };
