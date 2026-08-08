import { NextResponse, type NextRequest } from "next/server";

/**
 * Launch L1b: the EDGE half of the account gate. Signed-out visitors used to
 * get the app surface painted at them for a beat before AccountGate's
 * /api/me round-trip came back 401 and hard-navigated to /signin (owner
 * 2026-08-08: clicking "day N of the record" in the landing footer flashed
 * the whole Observatory first). A client effect cannot avoid that — the HTML
 * is already on the wire. This redirects before any of it renders.
 *
 * PRESENCE, not validity. The session cookie is httpOnly and same-origin
 * (the backend sets it, the proxy relays it), so the edge can see whether
 * one exists but not whether it resolves to a person. No cookie means
 * definitely signed out, which is the reported case and the only one this
 * refuses. A cookie that is expired or forged still passes here and is
 * settled by the two gates that CAN settle it: AccountGate's /api/me check,
 * and the backend path matrix (app/auth) that is the real boundary.
 *
 * Deliberately no /api/me call from the edge. It would put a blocking
 * backend round-trip on every app navigation and make a backend blip look
 * like a logout, which is exactly what AccountGate's non-401 pass-through
 * was written to avoid.
 */

const SESSION_COOKIE = "skeptic_session";

export function middleware(req: NextRequest) {
  if (req.cookies.has(SESSION_COOKIE)) return NextResponse.next();
  const url = req.nextUrl.clone();
  url.pathname = "/signin";
  url.search = "";
  // same shape AccountGate stamps, so /signin's nextTarget() (which rejects
  // "//host" and "/\host") reads it identically whichever gate fired
  url.searchParams.set("next", req.nextUrl.pathname + req.nextUrl.search);
  return NextResponse.redirect(url);
}

/**
 * The app/(app) route group, spelled out. A broad "everything except the
 * public pages" matcher would silently swallow each new public route
 * someone adds; this fails visibly instead (a new app route simply is not
 * gated at the edge until it is listed, and AccountGate still covers it).
 * Both forms per entry: ":path*" alone would not match the bare segment on
 * every Next version.
 */
export const config = {
  matcher: [
    "/new",
    "/new/:path*",
    "/runs",
    "/runs/:path*",
    "/library",
    "/library/:path*",
    "/data",
    "/data/:path*",
    "/settings",
    "/settings/:path*",
    "/admin",
    "/admin/:path*",
  ],
};
