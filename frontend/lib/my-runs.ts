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
