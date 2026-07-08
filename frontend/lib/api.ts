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

/** Pydantic validation refusals arrive as [{loc, msg, type}, …] — render the
 * explanation as a sentence, not raw JSON. The refusal text IS the product's
 * answer (e.g. "intraday_scan cannot combine with scale_in"); showing it
 * beats making the user decode an error array. Unknown shapes still
 * stringify so nothing is ever swallowed. */
function formatDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const parts = detail.map((e) => {
      if (e && typeof e === "object" && "msg" in e) {
        const err = e as { msg: unknown; loc?: unknown };
        // pydantic prefixes model_validator messages with "Value error, "
        const msg = String(err.msg).replace(/^Value error, /, "");
        const loc = Array.isArray(err.loc) ? err.loc.filter((p) => p !== "body").join(".") : "";
        return loc ? `${loc}: ${msg}` : msg;
      }
      return JSON.stringify(e);
    });
    return parts.join(" · ");
  }
  return JSON.stringify(detail);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { cache: "no-store", ...init });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(res.status, formatDetail(body.detail ?? body));
  }
  return body as T;
}

// ONE client-side promise cache for GET payloads, keyed by URL with a TTL
// per call site. `fresh` bypasses the read but still stores the new promise
// so concurrent followers share it. Failures are never cached. Bounded:
// paging URLs are unique and would otherwise pile up for the session.
const promiseCache = new Map<string, { t: number; p: Promise<unknown> }>();
const PROMISE_CACHE_MAX = 64;

function cachedRequest<T>(url: string, ttlMs: number, fresh = false): Promise<T> {
  const hit = promiseCache.get(url);
  if (!fresh && hit && Date.now() - hit.t < ttlMs) return hit.p as Promise<T>;
  if (promiseCache.size >= PROMISE_CACHE_MAX) {
    let oldest: string | null = null;
    let oldestT = Infinity;
    promiseCache.forEach((v, k) => {
      if (v.t < oldestT) {
        oldestT = v.t;
        oldest = k;
      }
    });
    if (oldest) promiseCache.delete(oldest);
  }
  const p = request<T>(url);
  promiseCache.set(url, { t: Date.now(), p });
  p.catch(() => promiseCache.delete(url));
  return p;
}

// the composer fetches coverage on mount; a short client cache lets the
// Data Observatory reuse that same payload and paint instantly on
// navigation instead of re-reading the lake (the backend caches 300s
// anyway — this adds no staleness a fresh request wouldn't also have)
const COVERAGE_CACHE_TTL_MS = 60_000;

export function getCoverage(): Promise<CoveragePayload> {
  return cachedRequest<CoveragePayload>("/api/data/coverage", COVERAGE_CACHE_TTL_MS);
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
  return cachedRequest<BarsPayload>(`/api/data/bars/${ticker}?${params}`, BARS_CACHE_TTL_MS);
}

/** Warm the exact request MarketChart issues on first mount (5m · 1w) for
 * ALL three tickers — chart mode then opens instantly and SPY→QQQ→IWM
 * switches land on a warm cache instead of a cold lake read. */
export function prefetchBars(): void {
  for (const t of ["SPY", "QQQ", "IWM"]) {
    getBars(t, "5m", "1w", []).catch(() => undefined);
  }
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
  untouched = true,
): Promise<{ run_id: string; demo: boolean }> {
  // an unedited parser spec runs verbatim — dial edits rebuild from the
  // dials WITH the parsed spec as base, so parser-only vocabulary
  // (ladders, intraday_scan, resolution, force-flat, time-of-day) is
  // never silently dropped by an unrelated dial edit (FX.5)
  const spec = {
    ...(untouched && parsedSpec ? parsedSpec : draftToSpec(draft, parsedSpec)),
  } as Record<string, unknown>;
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
const RUNS_CACHE_TTL_MS = 30_000;

export function listRuns(fresh = false): Promise<{ runs: RunSummary[]; demo: boolean }> {
  // `fresh` bypasses the cache — the library polls with it while a run
  // is in progress so the card flips to its verdict without a reload
  return cachedRequest<{ runs: RunSummary[]; demo: boolean }>(
    "/api/runs",
    RUNS_CACHE_TTL_MS,
    fresh,
  );
}

export function askRun(id: string, question: string): Promise<{ answer: string; demo: boolean }> {
  return request<{ answer: string; demo: boolean }>(`/api/runs/${id}/ask`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, verbiage: getSettings().verbiage }),
  });
}

/** F7: audit a run's fills against an independent vendor (on demand). */
export function auditRun(id: string): Promise<{ run_id: string; status: string }> {
  return request<{ run_id: string; status: string }>(`/api/runs/${id}/audit`, {
    method: "POST",
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
