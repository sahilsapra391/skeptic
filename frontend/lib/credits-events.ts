/**
 * Launch L2: a run debits a credit at start and refunds it at completion (on
 * a refusal / our-fault failure). Those happen WITHOUT a route change, so the
 * nav-rail balance would go stale after a submit. The run flow fires this
 * event; the account section refetches /api/me on it. Decoupled — no shared
 * store, no prop drilling across the app/landing boundary.
 */
export const CREDITS_CHANGED = "skeptic:credits-changed";

export function notifyCreditsChanged(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(CREDITS_CHANGED));
  }
}
