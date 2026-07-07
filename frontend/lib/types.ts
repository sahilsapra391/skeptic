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
export type WindowKind = "1y" | "3y" | "5y" | "10y" | "all" | "custom";

export interface SpecDraft {
  ticker: Ticker;
  structure: Structure;
  strikeDelta: number; // whole-number delta, 5..95 in steps of 5 (.05Δ steps)
  strikeLabel?: string | null; // non-delta selection from the parser ("ATM", "5% below spot")
  dte: number; // 0..50 (0 = 0DTE, refused at run until the minute engine)
  cadence: string; // e.g. "weekly · mon" (display; cadenceSel is the editable truth)
  size: string; // e.g. "1 contract" (display; sizeValue is the editable truth)
  exit: string | null; // null = parser must ask, never guess
  fromChart: boolean;
  quote: string; // the user's words, verbatim — or the chart-teach summary
  anchor?: string; // chart mode: first pinned entry date
  trigger?: string; // chart mode: display label for the trigger
  triggerSpec?: TriggerSpec; // chart mode: the editable structured trigger
  examples?: number; // chart mode: pinned example count
  // pre-run dials (2026-07-06): the outgoing spec is rebuilt from these
  /** REQUIRED before any run — null disables RUN until the user chooses.
   * Text-supplied dates arrive as a pre-filled custom entry, still
   * needing explicit confirmation. */
  window?: { kind: WindowKind; start?: string | null; end?: string | null } | null;
  cadenceSel?: { frequency: string; day_of_week?: string | null };
  sizeMethod?: "fixed_contracts" | "risk_pct_of_equity";
  sizeValue?: number;
  capital?: number;
  clock?: "daily" | "5min";
}

/** /api/data/estimate — window options with real session counts and time
 * estimates measured from completed runs (null until the first run at a
 * clock calibrates; never an invented number). */
export interface EstimatePayload {
  ticker: string;
  clock: string;
  first_session: string | null;
  options: { key: WindowKind; sessions: number; est_seconds: number | null }[];
  basis: { measured_runs: number; note: string };
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

/** greek series carry honest gaps: v is null on days the lake had no greek */
export interface NullableSeriesPoint {
  t: string;
  v: number | null;
}

export interface GreeksSeries {
  delta: NullableSeriesPoint[];
  gamma: NullableSeriesPoint[];
  theta: NullableSeriesPoint[];
  vega: NullableSeriesPoint[];
}

export interface LiquidityProfile {
  mode: string;
  max_spread_pct: number;
  min_open_interest: number;
  min_volume: number;
  option_leg_fills: number;
  median_spread_pct: number | null;
  penalized_share: number | null;
  stressed_share: number | null;
  unknown_liquidity_share: number | null;
  skipped_illiquid: number;
  material: boolean;
  note: string | null;
}

export interface ConcentrationBlock {
  meaningful: boolean;
  note: string | null;
  top_days: number;
  top_share: number | null;
  gamma_coincidence: number | null;
  flagged: boolean;
}

/** D5b: scale-in depth attribution (present only on ladder runs). P/L
 * red/green is allowed on this DATA panel, never on the verdict. */
export interface LadderDepthTier {
  depth: number;
  threshold: string;
  baskets: number;
  winRate: string;
  contracts: number;
  totalPl: string;
  avgPl: string;
  plSign: "pos" | "neg" | "none";
  pctProfit: string;
  pctLoss: string;
  barPct: number;
}

export interface LadderDepthRung {
  label: string;
  addContracts: number;
  fires: number;
  contracts: number;
  marginalPl: string;
  plSign: "pos" | "neg" | "none";
  netNeg: boolean;
  barPct: number;
}

export interface LadderDepthBlock {
  baskets: number;
  realizedTotal: string;
  realizedSign: "pos" | "neg" | "none";
  deepestNetNegative: boolean;
  tiers: LadderDepthTier[];
  rungs: LadderDepthRung[];
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
  /** D1d: per-day aggregate exposure of open positions (null = honest gap) */
  greeksSeries?: GreeksSeries;
  liquidity?: LiquidityProfile | null;
  concentration?: ConcentrationBlock | null;
  /** D5b: scale-in depth attribution (null on non-ladder runs) */
  ladderDepth?: LadderDepthBlock | null;
  /** D2b: the declared clock + per-fill quote provenance
   * (eod_chain / ivol_5min / cboe_minute → leg-fill counts) */
  clock?: string;
  fillSources?: Record<string, number>;
  /** FX.1: per-session bar-resolution record (null on daily / pre-v4 runs).
   * A results surface showing a finest run must show the mix (guardrail #6). */
  resolutionMode?: string | null;
  resolutionMix?: Record<string, number> | null;
  resolutionRuns?:
    | { first: string; last: string; sessions: number; resolution: string }[]
    | null;
  /** FX.2: every skipped entry attempt counted by reason (null pre-FX.2) */
  skipReasons?: Record<string, number> | null;
  /** D3c: 5-min replay receipts (merged at read time; stored trust untouched) */
  receipts?: {
    replay_run_id: string;
    created_at: string;
    daily_sharpe: number | null;
    five_min_sharpe: number | null;
    daily_return: number | null;
    five_min_return: number | null;
    fill_sources: Record<string, number>;
    worse: boolean;
  }[];
  replayEligible?: boolean;
  oosShadeX: number; // viewBox x where OOS shading starts (0..860)
  oosSplitDate?: string; // ISO date where the OOS window begins
  honesty: HonestyPanels;
  mc: { p95: string; p50: string; p05: string };
  mcTerm?: { p95: string; p50: string; p05: string }; // terminal $ per band
  sensitivity: number[][]; // opacity grid (5 cols per param for real runs)
  sensitivityRows?: string[]; // real runs: swept parameter names
  sensitivityDetail?: SensitivityRow[]; // real runs: cell-level sweep data
  recommendations?: string[]; // grounded improvements computed from this run
  // while running: real stats from finished stages. New runs carry both
  // voices; runs stored before the split are plain strings.
  previews?: (string | { pro: string; retail: string })[];
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
  quoteRetail?: string | null; // retail-register headline, when the run has one
  kind: VerdictKind;
  band?: { left: string; width: string };
  marker?: string;
  status?: "running"; // gauntlet still in progress — no verdict fields yet
  stage?: number; // 0-based current gauntlet stage while running
  /** D3b: automatic upgrade lineage — provenance is never blurred */
  upgradeOf?: string | null; // the refused run this one supersedes
  autoNote?: string | null; // "re-ran automatically — N new sessions"
  supersededBy?: string | null; // set on the OLD refusal once upgraded
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
  /** D1d: per-source field completeness + monthly spread, from the engine's
   * local chain cache — null until the first engine load builds it */
  chain_quality?: Record<
    Ticker,
    {
      rows: number;
      sources: Record<string, { rows: number; fields: Record<string, number> }>;
      monthly_median_spread_pct?: { month: string; v: number }[];
    } | null
  >;
  ivol_analytics?: Record<
    Ticker,
    {
      ivx: { years: number; first: string; last: string } | null;
      hv: { years: number; first: string; last: string } | null;
    }
  >;
  /** D3d: weekly demand ranking (build_priorities.py) — the Observatory's
   * "collection wants" line; null until the first weekly pass writes it */
  collection_priorities?: {
    generated_at: string;
    priorities: { rank: number; want: string; why: string; score: number }[];
  } | null;
  /** F0 (ENGINE-V4): per-session resolution mix from the collector-built
   * resolution map (state/resolution_map/) — null per ticker until the
   * nightly ledger has built it. clock = finest decision clock; quote =
   * best fill-grade source by quality (ivol_5min > cboe_2min > eod_only). */
  resolution_mix?: Record<
    Ticker,
    {
      sessions: number;
      clock: Record<string, number>;
      quote: Record<string, number>;
      minute_window: CoverageRange | null;
      five_min_window: CoverageRange | null;
      timeline: TimelineRun[];
    } | null
  >;
  /** F0: new-source coverage windows (state/source_coverage.json) — a
   * pass-through R2 artifact, so every field is optional: an older-schema
   * artifact must degrade gracefully, never crash the page */
  new_sources?: {
    generated_at?: string;
    uw_daily?: Record<string, Record<string, CoverageRange>>;
    uw_minute?: Record<Ticker, CoverageRange | null>;
    uw_tape?: Record<Ticker, CoverageRange | null>;
    ivs?: Record<Ticker, CoverageRange | null>;
    massive?: Record<Ticker, { agg_symbols: number }>;
  } | null;
}

/** One compressed run of consecutive sessions sharing (clock, quote). */
export interface TimelineRun {
  first: string;
  last: string;
  sessions: number;
  clock: string;
  quote: string;
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
  /** honest freshness label for a live tail: "live · IEX tail" (real-time) or
   * "delayed ~15m · CBOE recorder" — never claims real-time for delayed data */
  live_label?: string | null;
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
