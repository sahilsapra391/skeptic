"use client";

/**
 * Launch L1b: the app is account-gated (owner). This is the SOFT gate — a
 * UX courtesy that walks a signed-out visitor to /signup instead of letting
 * them type a strategy the API will refuse. The HARD gate is the backend
 * path matrix (app/auth): nothing here grants or denies access, so children
 * render normally while the check is in flight and on any non-401 outcome
 * (a backend blip must not lock people out of surfaces the API still
 * serves).
 */

import { useEffect } from "react";

import { ApiError, fetchMe } from "@/lib/api";

export function AccountGate({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    let alive = true;
    fetchMe().catch((err) => {
      if (!alive) return;
      if (err instanceof ApiError && err.status === 401) {
        location.replace("/signup?next=" + encodeURIComponent(location.pathname));
      }
    });
    return () => {
      alive = false;
    };
  }, []);
  return <>{children}</>;
}
