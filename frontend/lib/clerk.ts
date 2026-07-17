/**
 * Launch L1: the single source of truth for "is Clerk configured?" —
 * middleware, layout, the API proxy, and the nav account slot all key off
 * this one constant so the app can never end up half-enabled (review
 * finding: the expression was previously copied into four files).
 *
 * Build-time inlined (NEXT_PUBLIC_*): unset ⇒ every Clerk surface is
 * compiled out and the app is the exact single-user build. Unsetting the
 * var IS the rollback.
 */
export const CLERK_ENABLED = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
