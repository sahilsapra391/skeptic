"use client";

/**
 * Launch L1b: the app is account-gated (owner). This is the SOFT gate, a
 * UX courtesy that walks a signed-out visitor to /signin instead of letting
 * them type a strategy the API will refuse (owner: an app URL is most often
 * an existing account coming back; the form links signup for the rest).
 * The HARD gate is the backend
 * path matrix (app/auth): nothing here grants or denies access, so children
 * render normally while the check is in flight and on any non-401 outcome
 * (a backend blip must not lock people out of surfaces the API still
 * serves).
 *
 * Since 2026-08-08 this is the SECOND soft layer, not the first. middleware.ts
 * turns away anyone with no session cookie at the edge, before a byte of app
 * HTML ships, which is what stopped the Observatory flashing on the way to
 * /signin. What reaches here therefore holds SOME cookie; this settles the
 * ones that no longer resolve to a person (expired, revoked, forged). That
 * case still renders for the length of the /api/me round-trip, and should:
 * a returning session going stale is not worth a blocking edge fetch on
 * every navigation.
 */

import { useEffect } from "react";

import { ApiError, fetchMe } from "@/lib/api";

export function AccountGate({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    let alive = true;
    fetchMe().catch((err) => {
      if (!alive) return;
      if (err instanceof ApiError && err.status === 401) {
        location.replace("/signin?next=" + encodeURIComponent(location.pathname));
      }
    });
    return () => {
      alive = false;
    };
  }, []);
  return <>{children}</>;
}
