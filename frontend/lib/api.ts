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

export function getBars(
  ticker: string,
  interval: ChartInterval,
  window: ChartWindow,
  indicators: string[],
): Promise<BarsPayload> {
  const params = new URLSearchParams({ interval, window, indicators: indicators.join(",") });
  return request<BarsPayload>(`/api/data/bars/${ticker}?${params}`);
}

export function parseText(text: string): Promise<ParseResult> {
  return request<ParseResult>("/api/parse", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
  });
}

export function startBacktest(draft: SpecDraft): Promise<{ run_id: string; demo: boolean }> {
  return request<{ run_id: string; demo: boolean }>("/api/backtest", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ spec: draftToSpec(draft), draft }),
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
    body: JSON.stringify({ question }),
  });
}

export function getHealth(): Promise<{
  status: string;
  r2_configured: boolean;
  engine: string;
  parser: string;
}> {
  return request("/api/health");
}
