"use client";

/**
 * Launch L1b: the real signup form (self-rolled accounts). The pre-account
 * runs this browser made ride the request as claim_run_ids (lib/api signup
 * attaches them and clears the breadcrumb on success). Errors surface the
 * backend's own words — 409 additionally offers the sign-in door. Success
 * honors ?next= (the account gate stamps it), falling back to /new.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

import { ApiError, signup, type AuthAccount } from "@/lib/api";
import { nextTarget } from "@/lib/next-param";
import { turnstileConfigured } from "@/lib/turnstile";
import { TurnstileWidget } from "@/components/landing/turnstile-widget";

const FIELD =
  "w-full rounded-[10px] border border-line-hover bg-panel px-3.5 py-2.5 text-[14.5px] " +
  "text-ink transition-colors focus:border-trust focus:outline-none focus:ring-2 focus:ring-trust/25";
const LABEL = "mb-1.5 block text-[12.5px] font-semibold text-ink-3";

export function SignupForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [done, setDone] = useState<AuthAccount | null>(null);
  // the current query string, for cross-links that must carry ?next= —
  // read in an effect (window is absent during prerender)
  const [search, setSearch] = useState("");
  useEffect(() => setSearch(window.location.search), []);

  // the Turnstile human-check token (null until solved). Kept in a ref so its
  // arrival doesn't re-render the form; a bump on resetKey re-solves after the
  // token is consumed (a 403 retry).
  const turnstileTokenRef = useRef<string | null>(null);
  const [turnstileReset, setTurnstileReset] = useState(0);
  const onTurnstileVerify = useCallback((token: string | null) => {
    turnstileTokenRef.current = token;
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    // the human check must have produced a token before we submit — the
    // widget solves in the background, so this only bites on an instant submit
    if (turnstileConfigured() && !turnstileTokenRef.current) {
      setError(new ApiError(0, "just finishing a quick human check, try again in a second"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setDone(await signup(email, password, turnstileTokenRef.current));
    } catch (err) {
      // a failed human check (403) consumes the token — re-mint one for the retry
      if (err instanceof ApiError && err.status === 403) {
        turnstileTokenRef.current = null;
        setTurnstileReset((n) => n + 1);
      }
      setError(
        err instanceof ApiError
          ? err
          : new ApiError(0, "the server could not be reached — try again"),
      );
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    const claimed = done.claimedRuns ?? 0;
    return (
      <div>
        <p className="text-[15px] font-semibold text-ink">
          Account created
          {claimed > 0 &&
            ` — ${claimed} ${claimed === 1 ? "run" : "runs"} came with you`}
          .
        </p>
        <p className="mt-3">
          {done.verificationSent
            ? "Verification email sent — check your inbox."
            : "Verification email pending — the sender isn't configured yet; you can keep working."}
        </p>
        <Link
          href={nextTarget()}
          className="mt-6 inline-block rounded-[10px] bg-trust px-5 py-2.5 text-[14px] font-bold text-on-accent"
        >
          Continue →
        </Link>
      </div>
    );
  }

  return (
    <form onSubmit={submit} noValidate>
      <p>
        5 free backtests, a run library, and any runs this browser already
        made come with you. Email and password, nothing else.
      </p>
      <div className="mt-6">
        <label htmlFor="signup-email" className={LABEL}>
          Email
        </label>
        <input
          id="signup-email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className={FIELD}
        />
      </div>
      <div className="mt-4">
        <label htmlFor="signup-password" className={LABEL}>
          Password
        </label>
        <input
          id="signup-password"
          type="password"
          autoComplete="new-password"
          required
          minLength={10}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className={FIELD}
        />
        <p className="mt-1.5 font-mono text-[11px] text-ink-4">At least 10 characters.</p>
      </div>
      {error && (
        <p className="mt-4 text-[13px] text-warn" role="alert">
          {error.detail}
          {error.status === 409 && (
            <>
              {" "}
              <Link href={`/signin${search}`} className="font-semibold underline">
                Sign in instead →
              </Link>
            </>
          )}
        </p>
      )}
      {/* the human check — invisible unless Cloudflare challenges; renders
          nothing when NEXT_PUBLIC_TURNSTILE_SITE_KEY is unset (dev) */}
      <div className="mt-5">
        <TurnstileWidget onVerify={onTurnstileVerify} resetKey={turnstileReset} />
      </div>
      <button
        type="submit"
        disabled={busy}
        className="mt-6 inline-block rounded-[10px] bg-trust px-5 py-2.5 text-[14px] font-bold text-on-accent disabled:opacity-60"
      >
        {busy ? "Creating account…" : "Create account"}
      </button>
      <p className="mt-8 text-[13px] text-ink-4">
        Already have an account?{" "}
        <Link href={`/signin${search}`} className="font-semibold text-ink-2 underline">
          Sign in
        </Link>
      </p>
      <p className="mt-10 font-mono text-[11px] text-ink-4">
        Research tool, not financial advice.
      </p>
    </form>
  );
}
