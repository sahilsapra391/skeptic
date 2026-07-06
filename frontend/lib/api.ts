/**
 * Typed client for the same-origin /api/* route handlers (which proxy to the
 * FastAPI backend and add the bearer token server-side — TECH-SPEC §9).
 */

import type {
  BarsPayload,
  ChartInterval,
  ChartWindow,
  CoveragePayload,
  EstimatePayload,
  ParseResult,
  RunPayload,
  RunSummary,
  SpecDraft,
  UnderlyingPoint,
} from "./types";
import { getSettings } from "./settings";
import { draftToSpec } from "./spec";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(`${status}: ${detail}`);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { cache: "no-store", ...init });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail =
      typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    throw new ApiError(res.status, detail);
  }
  return body as T;
}

export function getCoverage(): Promise<CoveragePayload> {
  return request<CoveragePayload>("/api/data/coverage");
}

/** Pre-run window options: real session counts + measured time estimates. */
export function getEstimate(ticker: string, clock: string): Promise<EstimatePayload> {
  return request<EstimatePayload>(
    `/api/data/estimate?ticker=${encodeURIComponent(ticker)}&clock=${encodeURIComponent(clock)}`,
  );
}

export function getUnderlying(ticker: string, days = 240): Promise<{ series: UnderlyingPoint[] }> {
  return request<{ series: UnderlyingPoint[] }>(`/api/data/underlying/${ticker}?days=${days}`);
}

// short-TTL in-flight cache so the hero can warm the chart's first fetch
// before the user opens chart mode — the switch then renders instantly
const barsCache = new Map<string, { t: number; p: Promise<BarsPayload> }>();
const BARS_CACHE_TTL_MS = 60_000;

export function getBars(
  ticker: string,
  interval: ChartInterval,
  window: ChartWindow,
  indicators: string[],
  opts?: { before?: string; limit?: number },
): Promise<BarsPayload> {
  const params = new URLSearchParams({ interval, window, indicators: indicators.join(",") });
  if (opts?.before) params.set("before", opts.before);
  if (opts?.limit) params.set("limit", String(opts.limit));
  const url = `/api/data/bars/${ticker}?${params}`;
  const hit = barsCache.get(url);
  if (hit && Date.now() - hit.t < BARS_CACHE_TTL_MS) return hit.p;
  const p = request<BarsPayload>(url);
  barsCache.set(url, { t: Date.now(), p });
  p.catch(() => barsCache.delete(url)); // never cache a failure
  return p;
}

/** Warm the exact request MarketChart issues on first mount (SPY · 5m · 1w). */
export function prefetchBars(): void {
  getBars("SPY", "5m", "1w", []).catch(() => undefined);
}

export function parseText(
  text: string,
  answers?: Record<string, string>,
): Promise<ParseResult> {
  return request<ParseResult>("/api/parse", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text, answers }),
  });
}

/** The confirmed window → backtest start/end dates. Throws when the user
 * has not confirmed a window — RUN must be impossible without one. */
export function windowToDates(draft: SpecDraft): { start: string | null; end: string | null } {
  const w = draft.window;
  if (!w) throw new Error("data window is unset — the spec screen must ask, never default");
  if (w.kind === "custom") return { start: w.start ?? null, end: w.end ?? null };
  if (w.kind === "all") return { start: null, end: null };
  const years = { "1y": 1, "3y": 3, "5y": 5, "10y": 10 }[w.kind];
  const start = new Date();
  start.setFullYear(start.getFullYear() - years);
  return { start: start.toISOString().slice(0, 10), end: null };
}

export function startBacktest(
  draft: SpecDraft,
  parsedSpec?: Record<string, unknown> | null,
): Promise<{ run_id: string; demo: boolean }> {
  // an unedited parser spec runs verbatim — dial edits rebuild from the dials
  const spec = { ...(parsedSpec ?? draftToSpec(draft)) } as Record<string, unknown>;
  // cost settings apply to EVERY run — the edit in Settings is the edit here
  const { commission, slippage } = getSettings();
  spec.costs = {
    commission_per_contract: commission,
    slippage_half_spread_fraction: slippage,
  };
  // pre-run dials apply to EVERY run too (2026-07-06): the confirmed data
  // window (required), contracts, cadence and capital — like costs, they
  // override even a verbatim parsed spec, because the screen showed them
  const dates = windowToDates(draft);
  spec.backtest = {
    ...(spec.backtest as Record<string, unknown>),
    start: dates.start,
    end: dates.end,
    ...(draft.capital != null ? { initial_capital: draft.capital } : {}),
  };
  if (draft.sizeValue != null && draft.sizeMethod) {
    spec.sizing = { method: draft.sizeMethod, value: draft.sizeValue };
  }
  if (draft.cadenceSel) {
    const entry = { ...(spec.entry as Record<string, unknown>) };
    const prev = (entry.schedule ?? {}) as Record<string, unknown>;
    entry.schedule = {
      ...prev, // keep time_of_day / day_of_month the parser set
      frequency: draft.cadenceSel.frequency,
      day_of_week:
        draft.cadenceSel.frequency === "weekly"
          ? (draft.cadenceSel.day_of_week ?? "monday")
          : null,
    };
    spec.entry = entry;
  }
  return request<{ run_id: string; demo: boolean }>("/api/backtest", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ spec, draft }),
  });
}

export function getRun(id: string): Promise<RunPayload> {
  return request<RunPayload>(`/api/runs/${id}`);
}

// the sidebar requests the library on every navigation — cache briefly so
// a click-around doesn't hammer the runs database
let runsCache: { t: number; p: Promise<{ runs: RunSummary[]; demo: boolean }> } | null = null;
const RUNS_CACHE_TTL_MS = 30_000;

export function listRuns(fresh = false): Promise<{ runs: RunSummary[]; demo: boolean }> {
  // `fresh` bypasses the cache — the library polls with it while a run
  // is in progress so the card flips to its verdict without a reload
  if (!fresh && runsCache && Date.now() - runsCache.t < RUNS_CACHE_TTL_MS) return runsCache.p;
  const p = request<{ runs: RunSummary[]; demo: boolean }>("/api/runs");
  runsCache = { t: Date.now(), p };
  p.catch(() => {
    runsCache = null;
  });
  return p;
}

export function askRun(id: string, question: string): Promise<{ answer: string; demo: boolean }> {
  return request<{ answer: string; demo: boolean }>(`/api/runs/${id}/ask`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, verbiage: getSettings().verbiage }),
  });
}

/** D3c: replay a daily run at the 5-minute clock (verdict receipt). */
export function replayRun(id: string): Promise<{ run_id: string; parent: string }> {
  return request<{ run_id: string; parent: string }>(`/api/runs/${id}/replay`, {
    method: "POST",
  });
}

export function getHealth(): Promise<{
  status: string;
  r2_configured: boolean;
  engine: string;
  parser: string;
  db?: string;
  verdict_llm?: string;
  ask?: string;
  model?: string;
  min_trades?: number;
}> {
  return request("/api/health");
}
