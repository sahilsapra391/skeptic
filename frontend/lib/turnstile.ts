/**
 * Cloudflare Turnstile config (launch L4 anon armor). The site key is
 * build-time inlined; without it the human check is skipped end to end
 * (the backend skips verification too), so the anon run works in dev and
 * pre-launch. Setting the key turns on the challenge — the widget mounts,
 * the token rides the backtest request, the backend verifies it.
 */
export const TURNSTILE_SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "";

export function turnstileConfigured(): boolean {
  return TURNSTILE_SITE_KEY.length > 0;
}
