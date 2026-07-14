/**
 * Same-origin API proxy (TECH-SPEC §9): forwards /api/* to the FastAPI
 * backend, attaching the bearer token server-side so it never ships to the
 * browser.
 *
 * Demo fallback: the run pipeline (parse/backtest/runs/ask) answers 501
 * until milestones M2–M4 exist. When that happens (or the backend is down in
 * dev) and SKEPTIC_DEMO_FALLBACK != "0", those routes — and only those —
 * fall back to labeled demo fixtures. Data routes (/api/data/*, /api/health)
 * NEVER fall back: coverage is real or absent, never invented.
 */

import { NextRequest, NextResponse } from "next/server";

import { createDemoRun, demoAskAnswer, demoParse, getDemoRun, listDemoRuns } from "@/lib/demo";
import type { SpecDraft } from "@/lib/types";

export const dynamic = "force-dynamic";

const BACKEND = process.env.SKEPTIC_API_URL ?? "http://localhost:8000";

function demoEnabled(): boolean {
  return process.env.SKEPTIC_DEMO_FALLBACK !== "0";
}

async function forward(req: NextRequest, path: string[], body: string | null) {
  const url = `${BACKEND}/api/${path.join("/")}${req.nextUrl.search}`;
  const headers: Record<string, string> = {};
  const contentType = req.headers.get("content-type");
  if (contentType) headers["content-type"] = contentType;
  const token = process.env.SKEPTIC_ACCESS_TOKEN;
  if (token) headers.authorization = `Bearer ${token}`;
  // LLM round-trips get long leashes: the parser's upstream call is allowed
  // 60s backend-side (clarify-loop re-parses on DeepSeek regularly pass 30s)
  // and grounded Q&A runs a validation retry — a proxy that aborts under the
  // backend's own budget reports a healthy engine as "unreachable"
  const llmRoute = path[0] === "parse" || path[2] === "ask";
  const timeout = llmRoute ? 100_000 : 30_000;
  return fetch(url, {
    method: req.method,
    headers,
    body,
    cache: "no-store",
    signal: AbortSignal.timeout(timeout),
  });
}

function isRunPipeline(path: string[]): boolean {
  return path[0] === "parse" || path[0] === "backtest" || path[0] === "runs" || path[0] === "sweep";
}

function demoEligible(path: string[]): boolean {
  if (!isRunPipeline(path)) return false;
  // ask on a REAL run must surface the backend's honest 501 (missing key or
  // stats bundle), never a canned demo answer
  if (path[0] === "runs" && path[2] === "ask" && !path[1]?.startsWith("demo-")) return false;
  return true;
}

function demoResponse(req: NextRequest, path: string[], body: string | null): NextResponse {
  if (path[0] === "parse" && req.method === "POST") {
    const { text } = JSON.parse(body ?? "{}") as { text?: string };
    if (!text?.trim()) {
      return NextResponse.json({ detail: "empty strategy text" }, { status: 422 });
    }
    return NextResponse.json({ status: "spec", demo: true, draft: demoParse(text) });
  }
  if (path[0] === "backtest" && req.method === "POST") {
    // the confirmed draft now rides inside provenance (Chunk A); the
    // top-level key remains readable for older bodies
    const { draft, provenance } = JSON.parse(body ?? "{}") as {
      draft?: SpecDraft;
      provenance?: { confirmed?: { draft?: SpecDraft } };
    };
    const confirmed = provenance?.confirmed?.draft ?? draft;
    if (!confirmed) return NextResponse.json({ detail: "missing draft" }, { status: 422 });
    return NextResponse.json({ run_id: createDemoRun(confirmed), demo: true });
  }
  if (path[0] === "runs" && path.length === 1 && req.method === "GET") {
    return NextResponse.json({ runs: listDemoRuns(), demo: true });
  }
  if (path[0] === "runs" && path.length === 2 && req.method === "GET") {
    // demo payloads only for demo ids — a REAL run id never gets fixture data
    if (!path[1].startsWith("demo-")) {
      return NextResponse.json({ detail: "run not found" }, { status: 404 });
    }
    const run = getDemoRun(path[1]);
    if (!run) return NextResponse.json({ detail: "run not found" }, { status: 404 });
    return NextResponse.json(run);
  }
  if (path[0] === "runs" && path[2] === "ask" && req.method === "POST") {
    return NextResponse.json({ answer: demoAskAnswer(), demo: true });
  }
  return NextResponse.json(
    { detail: "not available until milestones M2–M4 (docs/BUILD-PLAN.md)" },
    { status: 501 },
  );
}

async function handle(req: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path;
  const body = req.method === "GET" || req.method === "HEAD" ? null : await req.text();

  try {
    const upstream = await forward(req, path, body);
    if (upstream.status === 501 && demoEligible(path) && demoEnabled()) {
      return demoResponse(req, path, body);
    }
    const payload = await upstream.text();
    return new NextResponse(payload, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch (err) {
    // backend unreachable — run pipeline may demo; data routes stay honest
    if (demoEligible(path) && demoEnabled()) {
      return demoResponse(req, path, body);
    }
    // a timed-out request is NOT an unreachable backend — the engine was
    // healthy and still working when the proxy gave up; say that honestly
    const timedOut =
      err instanceof Error && (err.name === "TimeoutError" || err.name === "AbortError");
    if (timedOut) {
      return NextResponse.json(
        { detail: "the engine took too long to answer — it's still up; try again" },
        { status: 504 },
      );
    }
    // the dev hint only makes sense against a local backend; in prod the
    // usual cause is a redeploy window — say so instead of leaking dev docs
    const local = /localhost|127\.0\.0\.1/.test(BACKEND);
    return NextResponse.json(
      {
        detail: local
          ? `backend unreachable at ${BACKEND} — start it with: cd backend && uv run uvicorn app.main:app`
          : "the engine is unreachable — it may be redeploying; try again in a minute",
      },
      { status: 502 },
    );
  }
}

export { handle as GET, handle as POST };
