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
import { auth } from "@clerk/nextjs/server";

import { createDemoRun, demoAskAnswer, demoParse, getDemoRun, listDemoRuns } from "@/lib/demo";
import type { SpecDraft } from "@/lib/types";

export const dynamic = "force-dynamic";

const BACKEND = process.env.SKEPTIC_API_URL ?? "http://localhost:8000";
// launch L1: no key, no accounts — the proxy sends exactly what it sent
// before Clerk existed
const CLERK_ENABLED = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

function demoEnabled(): boolean {
  return process.env.SKEPTIC_DEMO_FALLBACK !== "0";
}

async function forward(req: NextRequest, path: string[], body: string | null) {
  const url = `${BACKEND}/api/${path.join("/")}${req.nextUrl.search}`;
  const headers: Record<string, string> = {};
  const contentType = req.headers.get("content-type");
  if (contentType) headers["content-type"] = contentType;
  // two credentials, two headers (launch L1): the Authorization bearer is
  // the GATE (the service token, semantics unchanged since single-user);
  // x-skeptic-session is the IDENTITY — the signed-in user's short-lived
  // Clerk JWT, verified server-side against the instance JWKS. The token
  // itself never ships to the browser; it's minted here, server-side.
  const token = process.env.SKEPTIC_ACCESS_TOKEN;
  if (token) headers.authorization = `Bearer ${token}`;
  if (CLERK_ENABLED) {
    try {
      const { getToken } = await auth();
      const session = await getToken();
      if (session) headers["x-skeptic-session"] = session;
    } catch {
      // no middleware context (build-time render) — treat as signed out
    }
  }
  // the client's real IP, for the backend's per-IP rate keys (L4 armor);
  // Vercel stamps it on the inbound request
  const xff = req.headers.get("x-forwarded-for");
  if (xff) headers["x-forwarded-for"] = xff;
  // LLM round-trips get long leashes: the parser bounds its whole attempt
  // loop to 90s wall-clock (PARSE_BUDGET_SECONDS, backend parse.py — clarify
  // -loop re-parses on DeepSeek regularly pass 30s) and grounded Q&A runs a
  // validation retry — the proxy's leash must stay ABOVE those budgets, or it
  // aborts a healthy engine mid-work and reports it as a 504
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
  // run exports (notebook/report) exist only on the backend — the demo
  // fallback would mask "backend down" as "not built yet"
  if (path[0] === "runs" && (path[2] === "notebook" || path[2] === "report")) return false;
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
    const headers: Record<string, string> = {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
    };
    // run exports ride on these: the filename for saves, and the report's
    // locked-down CSP (defense-in-depth the proxy must not strip)
    for (const h of ["content-disposition", "content-security-policy"]) {
      const v = upstream.headers.get(h);
      if (v) headers[h] = v;
    }
    return new NextResponse(payload, {
      status: upstream.status,
      headers,
    });
  } catch (err) {
    // a timed-out request is NOT an unreachable backend — the engine was
    // healthy and still working when the proxy gave up; say that honestly,
    // checked BEFORE the demo fallback so a slow engine is never papered
    // over with demo fixtures
    const timedOut =
      err instanceof Error && (err.name === "TimeoutError" || err.name === "AbortError");
    if (timedOut) {
      return NextResponse.json(
        { detail: "the engine took too long to answer — it's still up; try again" },
        { status: 504 },
      );
    }
    // backend unreachable — run pipeline may demo; data routes stay honest
    if (demoEligible(path) && demoEnabled()) {
      return demoResponse(req, path, body);
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
