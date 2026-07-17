/**
 * The runs THIS browser started (launch L4, pre-accounts). The public
 * listing shows only the two pinned examples; a visitor's own runs ride
 * along via `include=` so their work never vanishes from the library or
 * the nav rail. This list is ALSO the claim flow's input: when accounts
 * land, signup re-parents exactly these ids to the new account (owner
 * 2026-07-17 — "the run they do from the landing page should show up in
 * their account if they choose to create it").
 */

const KEY = "skeptic-my-runs";
const CAP = 50; // matches the server's include cap

export function myRunIds(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export function rememberRun(id: string): void {
  if (typeof window === "undefined") return;
  try {
    const ids = [id, ...myRunIds().filter((x) => x !== id)].slice(0, CAP);
    localStorage.setItem(KEY, JSON.stringify(ids));
  } catch {
    /* private mode — the run still exists server-side */
  }
}

/** Drop a single id — used when a remembered run turns out to be a phantom
 * (a 404 on view: a demo-fallback run that didn't persist, or a lost run).
 * Removing it releases the device's one-free-run gate so the visitor isn't
 * stuck "used" with nothing to show for it (owner 2026-07-17). */
export function forgetRun(id: string): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(KEY, JSON.stringify(myRunIds().filter((x) => x !== id)));
  } catch {
    /* private mode */
  }
}

/** Signup claimed these ids server-side — ownership is DB truth now, and a
 * stale breadcrumb would keep riding `include=` (and a future signup from
 * this browser would try to re-claim runs that already have an owner). */
export function clearMyRuns(): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* private mode */
  }
}
