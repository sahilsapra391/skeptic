/**
 * The public, signed-out surface: the landing and its satellite pages
 * (auth + legal). These share ONE theme preference — the landing's
 * market-hours default under `sk-landing-theme` — so a visitor's light/
 * dark/market choice follows them from the landing into signup, sign-in,
 * and the legal pages. The app (behind login) uses app settings instead.
 *
 * The pre-paint head script in app/layout.tsx mirrors this path set as an
 * inline regex — keep the two in sync.
 */
export function isPublicSurface(pathname: string): boolean {
  return (
    pathname === "/" ||
    /^\/(signin|signup|verify|terms|privacy|refunds)(\/|$)/.test(pathname)
  );
}
