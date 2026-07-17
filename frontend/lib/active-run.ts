/**
 * The one anonymous run this browser currently has in flight or freshly
 * finished (launch L4). The landing surfaces it via ActiveRunBanner so a
 * run keeps going in the background when the popup is closed, and its
 * results are reachable afterward — instead of vanishing (owner
 * 2026-07-17). Separate from `skeptic-my-runs` (the claim-flow / library
 * breadcrumb); this is just the pointer the banner watches.
 */

const KEY = "skeptic-active-run";

export function getActiveRun(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(KEY) || null;
  } catch {
    return null;
  }
}

export function setActiveRun(id: string): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(KEY, id);
  } catch {
    /* private mode — the run still runs server-side, just untracked here */
  }
}

export function clearActiveRun(): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* private mode */
  }
}
