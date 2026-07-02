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

/** Editable display draft the composer/spec flow works with before a run. */
export interface SpecDraft {
  ticker: Ticker;
  structure: Structure;
  strikeDelta: number; // whole-number delta, 10..50 in steps of 5
  dte: number; // 7..90 in steps of 5
  cadence: string; // e.g. "weekly · mon"
  size: string; // e.g. "1 contract"
  exit: string | null; // null = parser must ask, never guess
  fromChart: boolean;
  quote: string; // the user's words, verbatim — or the chart-teach summary
  anchor?: string; // chart mode: first pinned entry date
  trigger?: string; // chart mode: inferred entry trigger
  examples?: number; // chart mode: pinned example count
}

export type VerdictKind = "fades-oos" | "survives" | "refusal";

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

export interface RunPayload {
  id: string;
  demo: boolean;
  status: "running" | "done";
  stage: number; // 0..6, meaningful while running
  name: string;
  meta: string;
  spec: SpecDraft | null;
  verdict: VerdictPayload;
  mtiles: MetricTile[];
  equityPoints: string;
  drawdownPoints: string;
  oosShadeX: number; // viewBox x where OOS shading starts (0..860)
  honesty: HonestyPanels;
  mc: { p95: string; p50: string; p05: string };
  sensitivity: number[][]; // opacity grid, rows x 9
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

export interface ParseResult {
  status: "spec";
  demo: boolean;
  draft: SpecDraft;
}
