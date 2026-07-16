/**
 * Launch L1: Clerk session middleware (owner decision D1 = managed auth).
 *
 * Inert without NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY — the single-user
 * deployment ships zero behavior change until the Clerk env vars are set,
 * and unsetting them is the rollback. No route is protected here: pages
 * stay public, the middleware only makes the session readable so the
 * /api proxy can mint the x-skeptic-session identity header.
 */

import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

const CLERK_ENABLED = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

export default CLERK_ENABLED
  ? clerkMiddleware()
  : function middleware() {
      return NextResponse.next();
    };

export const config = {
  matcher: [
    // everything except static assets…
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // …and always the API proxy, where the session header is attached
    "/(api|trpc)(.*)",
  ],
};
