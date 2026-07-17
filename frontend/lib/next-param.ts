/**
 * The ?next= redirect target the account gate stamps on /signup and
 * /signin. Same-origin paths only: an absolute or scheme-relative value
 * here would be an open redirect. Read at success time from
 * window.location (client components only).
 */
export function nextTarget(): string {
  if (typeof window === "undefined") return "/new";
  const next = new URLSearchParams(window.location.search).get("next");
  // a site-absolute path only: "/" alone, or "/" + a non-slash-non-
  // backslash char. Rejecting "//host" AND "/\host" matters — browsers
  // normalize the backslash to a protocol-relative URL, so a bare
  // !startsWith("//") guard is an open redirect (review finding).
  if (next && (next === "/" || /^\/[^/\\]/.test(next))) return next;
  return "/new";
}
