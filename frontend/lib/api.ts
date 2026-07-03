/**
 * Typed client for the same-origin /api/* route handlers (which proxy to the
 * FastAPI backend and add the bearer token server-side — TECH-SPEC §9).
 */

import type {
  BarsPayload,
  ChartInterval,
  ChartWindow,
  CoveragePayload,
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
  return request<{ run_id: string; demo: boolean }>("/api/backtest", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ spec, draft }),
  });
}

export function getRun(id: string): Promise<RunPayload> {
  return request<RunPayload>(`/api/runs/${id}`);
}

export function listRuns(): Promise<{ runs: RunSummary[]; demo: boolean }> {
  return request<{ runs: RunSummary[]; demo: boolean }>("/api/runs");
}

export function askRun(id: string, question: string): Promise<{ answer: string; demo: boolean }> {
  return request<{ answer: string; demo: boolean }>(`/api/runs/${id}/ask`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, verbiage: getSettings().verbiage }),
  });
}

export function getHealth(): Promise<{
  status: string;
  r2_configured: boolean;
  engine: string;
  parser: string;
  verdict_llm?: string;
  ask?: string;
  model?: string;
  min_trades?: number;
}> {
  return request("/api/health");
}
