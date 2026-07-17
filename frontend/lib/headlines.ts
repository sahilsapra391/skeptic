/**
 * The rotating hero promise — a different phrasing of the same idea each
 * visit. Shared by the app's New Analysis screen and the landing hero so
 * both cycle the same line (sequential, persisted under `skeptic-headline`,
 * so it always changes on reload).
 */
export const HEADLINES = [
  "Describe a strategy. I'll try to break it.",
  "Bring your thesis. I'll bring the evidence.",
  "Pitch me a trade. I'll play the skeptic.",
  "Bring me your best idea. I'll stress-test it.",
  "Describe a strategy. Let's see what survives.",
  "Got an edge? Prove it against the data.",
  "Tell me the trade. I'll tell you where it breaks.",
  "Describe a strategy. The data gets the last word.",
  "Show me a winner. I'll check if it was luck.",
  "Your idea versus six years of market data. Go.",
];

const KEY = "skeptic-headline";
const CANONICAL = "Pitch me a trade. I'll play the skeptic.";

/** This visit's headline WITHOUT advancing — safe for a useState seed so
 * the client's first paint is already correct (no flash). SSR returns the
 * canonical line. */
export function peekHeadline(): string {
  if (typeof window === "undefined") return CANONICAL;
  try {
    const i = Number(localStorage.getItem(KEY) ?? "0") % HEADLINES.length;
    return HEADLINES[i];
  } catch {
    return CANONICAL;
  }
}

/** Advance the persisted counter so the next reload shows a different one.
 * Call once on mount, after peekHeadline seeded the current view. */
export function bumpHeadline(): void {
  if (typeof window === "undefined") return;
  try {
    const i = Number(localStorage.getItem(KEY) ?? "0") % HEADLINES.length;
    localStorage.setItem(KEY, String((i + 1) % HEADLINES.length));
  } catch {
    /* private mode */
  }
}
