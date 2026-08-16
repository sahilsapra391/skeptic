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
  ProvenanceEvent,
  RunPayload,
  RunSummary,
  SpecDraft,
  UnderlyingPoint,
} from "./types";
import { clearMyRuns, myRunIds, rememberRun } from "./my-runs";
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
// `swr` (stale-while-revalidate) serves an EXPIRED-but-resolved entry
// instantly and refreshes it in the background — navigation paints from
// the last known data instead of waiting on a cold proxy round-trip.
const promiseCache = new Map<string, { t: number; p: Promise<unknown>; settled: boolean }>();
const PROMISE_CACHE_MAX = 64;
// swr serves an expired entry only this far past its TTL — beyond it the
// data is too old to paint even briefly (an hour-idle tab must not flash
// hour-old bars under a "delayed ~15m" badge; review finding 2026-07-15)
const SWR_MAX_EXTRA_MS = 600_000;

function storeRequest<T>(url: string): Promise<T> {
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
  const entry = { t: Date.now(), p: p as Promise<unknown>, settled: false };
  promiseCache.set(url, entry);
  p.then(
    () => {
      entry.settled = true;
    },
    () => {
      // delete only OUR entry — a fresh/evicted replacement under the same
      // URL must survive this stale rejection (review finding 2026-07-15)
      if (promiseCache.get(url) === entry) promiseCache.delete(url);
    },
  );
  return p;
}

function cachedRequest<T>(
  url: string,
  ttlMs: number,
  fresh = false,
  swr = false,
): Promise<T> {
  const hit = promiseCache.get(url);
  if (!fresh && hit) {
    if (Date.now() - hit.t < ttlMs) return hit.p as Promise<T>;
    if (swr && hit.settled && Date.now() - hit.t < ttlMs + SWR_MAX_EXTRA_MS) {
      // serve the stale value NOW; the replacement entry is stored before
      // returning so concurrent expired readers share one refresh
      const stale = hit.p as Promise<T>;
      storeRequest<T>(url);
      return stale;
    }
  }
  return storeRequest<T>(url);
}

// the composer fetches coverage on mount; a short client cache lets the
// Data Observatory reuse that same payload and paint instantly on
// navigation instead of re-reading the lake (the backend caches 300s
// anyway — this adds no staleness a fresh request wouldn't also have)
const COVERAGE_CACHE_TTL_MS = 60_000;

export function getCoverage(fresh = false): Promise<CoveragePayload> {
  // fresh=true is the Observatory's live poll — it bypasses the client
  // cache read (otherwise every tick re-serves the same snapshot)
  return cachedRequest<CoveragePayload>("/api/data/coverage", COVERAGE_CACHE_TTL_MS, fresh);
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

// client cache so the hero can warm the chart's first fetch before the
// user opens chart mode — the switch then renders instantly. 5 minutes:
// the bars are ~15-min delayed anyway (the UI badge says so), so client
// staleness inside that window adds no dishonesty a fresh request
// wouldn't also have; the 15s live poll bypasses via `fresh`.
const BARS_CACHE_TTL_MS = 300_000;

export function getBars(
  ticker: string,
  interval: ChartInterval,
  window: ChartWindow,
  indicators: string[],
  opts?: { before?: string; limit?: number; tail?: boolean; fresh?: boolean },
): Promise<BarsPayload> {
  const params = new URLSearchParams({ interval, window, indicators: indicators.join(",") });
  if (opts?.before) params.set("before", opts.before);
  if (opts?.limit) params.set("limit", String(opts.limit));
  // tail=0 skips the backend's blocking live-tail fetch — the first paint
  // reads the cached lake instantly and the tail arrives in a follow-up
  if (opts?.tail === false) params.set("tail", "0");
  return cachedRequest<BarsPayload>(
    `/api/data/bars/${ticker}?${params}`,
    BARS_CACHE_TTL_MS,
    opts?.fresh ?? false,
    true,
  );
}

/** Warm the exact request MarketChart issues on first mount (5m · 1w,
 * lake-only) for ALL three tickers — chart mode then opens instantly and
 * SPY→QQQ→IWM switches land on a warm cache instead of a cold lake read. */
export function prefetchBars(): void {
  for (const t of ["SPY", "QQQ", "IWM"]) {
    getBars(t, "5m", "1w", [], { tail: false }).catch(() => undefined);
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

/** V-08: everything the spec screen needs to reopen a stored run as a variant.
 * Server-side projection — the client never rebuilds a parent's spec itself. */
export interface VariantDraftPayload {
  parent: { id: string; rootId: string; ordinal: number | null };
  draft: SpecDraft | null;
  spec: Record<string, unknown> | null;
  tier: "a" | "b" | "c";
  locked: string[];
  reasons: Record<string, string>;
  lockedPaths?: string[];
  prompt?: { text?: string } | null;
  conversation?: ProvenanceEvent[];
}

export function getVariantDraft(runId: string): Promise<VariantDraftPayload> {
  return request<VariantDraftPayload>(`/api/runs/${runId}/variant`);
}

export type StartResult = {
  run_id: string;
  demo: boolean;
  // present only on the anonymous free-run path (launch L4 armor)
  queuePosition?: number;
  trialConstraint?: string;
};

export function startBacktest(
  draft: SpecDraft,
  parsedSpec?: Record<string, unknown> | null,
  untouched = true,
  conversation: ProvenanceEvent[] = [],
  // the human-check token from the anon popup — rides the request so the
  // backend can siteverify it. null when Turnstile isn't configured (dev /
  // pre-launch) or the caller is a signed-in account (backend skips the
  // check entirely for those).
  turnstileToken: string | null = null,
): Promise<StartResult> {
  // an unedited parser spec runs verbatim — dial edits rebuild from the
  // dials WITH the parsed spec as base, so parser-only vocabulary
  // (ladders, intraday_scan, resolution, force-flat, time-of-day) is
  // never silently dropped by an unrelated dial edit (FX.5)
  const spec = {
    ...(untouched && parsedSpec ? parsedSpec : draftToSpec(draft, parsedSpec)),
  } as Record<string, unknown>;
  // V-36: costs come from the CONFIRMED spec, never from Settings at submit.
  // Settings seed draft.costs once at parse time (run-flow), before the user
  // confirms; after that they are inert. A confirmed spec that runtime can
  // still overwrite is a suggestion, not a contract. Applied on the verbatim
  // branch too, because the FILLS tile showed these to the user either way.
  //
  // Absent costs THROW rather than falling back, matching windowToDates above:
  // a silent fallback here would run the backtest on fill assumptions the user
  // never saw, which is the exact failure this change exists to remove. Every
  // draft is stamped by confirmDefaults the moment it exists, so reaching this
  // means a new draft-construction path forgot to.
  if (!draft.costs) {
    throw new Error(
      "confirmed fill costs are unset. The spec screen must ask, never default.",
    );
  }
  spec.costs = { ...draft.costs };
  // pre-run dials apply to EVERY run too (2026-07-06): the confirmed data
  // window (required), contracts, cadence and capital — like costs, they
  // override even a verbatim parsed spec, because the screen showed them
  const dates = windowToDates(draft);
  spec.backtest = {
    ...(spec.backtest as Record<string, unknown>),
    start: dates.start,
    end: dates.end,
    ...(draft.capital != null ? { initial_capital: draft.capital } : {}),
    ...(draft.seed != null ? { seed: draft.seed } : {}),
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
  // Chunk A: the setup story rides the run request and is stored on the run
  // row (display-only server-side — never fed to the engine or the verdict).
  // `confirmed.draft` replaces the old top-level `draft` key, which no
  // backend code ever read.
  const provenance = {
    v: 1,
    source: draft.fromChart ? "chart" : "text",
    prompt: {
      text: draft.quote,
      ...(draft.chartContext ? { chart: draft.chartContext } : {}),
    },
    conversation,
    confirmed: { draft, costs: spec.costs, untouched },
  };
  return request<StartResult>("/api/backtest", {
    method: "POST",
    headers: { "content-type": "application/json" },
    // the evidence bar (Settings) gates this run's verdict server-side;
    // turnstile_token is the anon human-check (ignored for signed-in callers)
    body: JSON.stringify({
      spec,
      provenance,
      min_trades: getSettings().minTrades,
      turnstile_token: turnstileToken,
    }),
  }).then((res) => {
    // pre-accounts: this browser's runs ride the curated listing via
    // include=, and signup later re-parents exactly this list (claim flow).
    // A demo-fallback run lives only in the proxy's memory and won't
    // survive a later view (serverless instances differ) — remembering it
    // would 404 on view AND wrongly burn the device's one free run, so
    // skip it (owner-reported bug 2026-07-17).
    if (!res.demo) rememberRun(res.run_id);
    return res;
  });
}

export function getRun(id: string): Promise<RunPayload> {
  // the current evidence bar rides every read — saved runs re-grade
  // server-side when the bar moved since they ran (both directions)
  return request<RunPayload>(`/api/runs/${id}?min_trades=${getSettings().minTrades}`);
}

// the sidebar requests the library on every navigation — cache briefly so
// a click-around doesn't hammer the runs database
const RUNS_CACHE_TTL_MS = 30_000;

export function listRuns(fresh = false): Promise<{ runs: RunSummary[]; demo: boolean }> {
  // `fresh` bypasses the cache — the library polls with it while a run
  // is in progress so the card flips to its verdict without a reload.
  // swr: an expired listing paints instantly and refreshes behind — the
  // nav rail calls this on every navigation.
  // Pre-accounts curation (launch L4): the server lists the two pinned
  // examples; this browser's own runs ride along explicitly — the same
  // id list the claim flow re-parents at signup.
  // sorted: the ids are recency-ordered in storage, and an order-sensitive
  // key would mint a fresh cache entry (cold nav-rail paint) after every run
  const mine = myRunIds();
  const url = mine.length
    ? `/api/runs?include=${encodeURIComponent([...mine].sort().join(","))}`
    : "/api/runs";
  return cachedRequest<{ runs: RunSummary[]; demo: boolean }>(
    url,
    RUNS_CACHE_TTL_MS,
    fresh,
    true,
  );
}

export function askRun(id: string, question: string): Promise<{ answer: string; demo: boolean }> {
  // the evidence bar rides along so grounded answers describe the SAME
  // verdict the screen shows when a saved run was re-graded at read time
  const { verbiage, minTrades } = getSettings();
  return request<{ answer: string; demo: boolean }>(`/api/runs/${id}/ask`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, verbiage, min_trades: minTrades }),
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

/** Parity Tier 1: the completed run as an executable .ipynb. Returns the
 * exact text the backend built — parsing and re-stringifying it here would
 * reformat the notebook, which is why this can't ride request<T> (it
 * always json()s the body). The HTML report needs no fetch helper: it's
 * served inline and the menu links straight at the proxy path. */
export async function fetchNotebook(id: string): Promise<string> {
  const res = await fetch(`/api/runs/${id}/notebook`, { cache: "no-store" });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    const detail = formatDetail(body.detail ?? body);
    // an HTML or empty error body (edge proxy 502) formats to "{}" —
    // name the status instead of showing the user a brace pair
    throw new ApiError(
      res.status,
      detail === "{}" ? `export failed (HTTP ${res.status})` : detail,
    );
  }
  return res.text();
}

export function getHealth(): Promise<{
  status: string;
  r2_configured: boolean;
  engine: string;
  parser: string;
  db?: string;
  verdict_llm?: string;
  ask?: string;
  // narration (verdict · ask · health) and the parser. One model powers both
  // since 2026-08-14, but each keeps its own env override, so Settings reads
  // BOTH and says so when a prod override has pulled them apart.
  model?: string;
  parser_model?: string;
  min_trades?: number;
}> {
  return request("/api/health");
}

/** Launch L1: the signed-in account — email + credit balance (computed
 * server-side from the append-only ledger; there is no stored balance).
 * 401 (signed out) surfaces as ApiError. */
export type MePayload = {
  email: string;
  credits: number;
  verified: boolean;
  createdAt: string | null;
  admin: boolean; // launch L5: on the SKEPTIC_ADMIN_EMAILS allowlist
};

export function fetchMe(): Promise<MePayload> {
  return request<MePayload>("/api/me");
}

/** Launch L5 admin surface. Award (or claw back, negative) a user's credits —
 * the web equivalent of scripts/grant_credits.py. 404 for non-admins. */
export function adminGrantCredits(
  email: string,
  credits: number,
): Promise<{ email: string; before: number; after: number; delta: number }> {
  return request("/api/admin/grant-credits", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, credits }),
  });
}

export type AdminMetrics = {
  accounts: { total: number; verified: number; signups_7d: number };
  runs: { total: number; by_status: Record<string, number>; signed_in: number; last_7d: number };
  credits: {
    spent: number;
    signup_granted: number;
    purchased: number;
    admin_adjusted: number;
    refunded: number;
    charged_back: number;
    outstanding: number;
  };
  revenue: { purchases: number; chargebacks: number; gross_usd: number; net_usd: number };
  anon_trials: { total: number; today: number };
};

export function adminMetrics(): Promise<AdminMetrics> {
  return request<AdminMetrics>("/api/admin/metrics");
}

/** Launch L1b (self-rolled auth): what signup/login return. The session
 * itself never touches client code — it rides an httpOnly cookie set by
 * the backend and relayed through the proxy. */
export type AuthAccount = {
  email: string;
  credits: number;
  verified: boolean;
  claimedRuns?: number;
  verificationSent?: boolean;
};

export function signup(
  email: string,
  password: string,
  // the Turnstile human-check token; null when Turnstile isn't configured
  // (dev / pre-launch) — the backend skips the check in the same case
  turnstileToken: string | null = null,
): Promise<AuthAccount> {
  // the claim flow: this browser's pre-account runs (the skeptic-my-runs
  // breadcrumb) ride the signup and re-parent server-side; on success the
  // breadcrumb clears — ownership is DB truth from here on, and a stale
  // list would try to re-claim already-owned runs on a future signup
  return request<AuthAccount>("/api/auth/signup", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      email,
      password,
      claim_run_ids: myRunIds(),
      turnstile_token: turnstileToken,
    }),
  }).then((account) => {
    clearMyRuns();
    return account;
  });
}

export function login(email: string, password: string): Promise<AuthAccount> {
  return request<AuthAccount>("/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

export function logout(): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>("/api/auth/logout", { method: "POST" });
}

/** Launch L3: start a Stripe Checkout for the signed-in account and return
 * the hosted URL to redirect the browser to. Credits are granted by the
 * signature-verified webhook AFTER payment — never by the return redirect. */
export function startCheckout(): Promise<{ url: string }> {
  return request<{ url: string }>("/api/checkout", { method: "POST" });
}

export function verifyEmail(token: string): Promise<AuthAccount> {
  return request<AuthAccount>("/api/auth/verify", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ token }),
  });
}

export function resendVerification(): Promise<{
  ok: boolean;
  verified: boolean;
  verificationSent?: boolean;
}> {
  return request("/api/auth/resend-verification", { method: "POST" });
}
