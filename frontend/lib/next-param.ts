/**
 * The ?next= redirect target the account gate stamps on /signup and
 * /signin. Same-origin paths only: an absolute or scheme-relative value
 * here would be an open redirect. Read at success time from
 * window.location (client components only).
 */
export function nextTarget(): string {
  if (typeof window === "undefined") return "/new";
  const next = new URLSearchParams(window.location.search).get("next");
  if (next && next.startsWith("/") && !next.startsWith("//")) return next;
  return "/new";
}
