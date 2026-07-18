"use client";

/**
 * Launch L1b: the nav-rail account slot, self-rolled (no Clerk). Identity
 * is whatever /api/me says — the httpOnly session cookie rides the request
 * on its own, so there is no client-side auth state to drift from the
 * server's. Refetched on navigation: once runs debit credits (L2), the
 * balance must not go stale after a submit. Credits are data → Plex Mono
 * (typography rule).
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";

import { ApiError, fetchMe, logout, startCheckout, type MePayload } from "@/lib/api";
import { CREDITS_CHANGED, notifyCreditsChanged } from "@/lib/credits-events";

function PersonIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      className="flex-none"
    >
      <circle cx="10" cy="7" r="3.2" />
      <path d="M4 16.5c1.2-2.6 3.4-4 6-4s4.8 1.4 6 4" strokeLinecap="round" />
    </svg>
  );
}

function SignOutIcon() {
  // door frame + arrow leaving to the right (a standard log-out glyph),
  // sized + weighted to sit alongside the top-rail nav icons
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="flex-none"
    >
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5" />
      <path d="M21 12H9" />
    </svg>
  );
}

export function AccountSection({ open }: { open: boolean }) {
  const pathname = usePathname();
  // undefined = first check still in flight (render nothing rather than
  // flash "Sign in" at a signed-in user); null = signed out
  const [me, setMe] = useState<MePayload | null | undefined>(undefined);
  const [signingOut, setSigningOut] = useState(false);
  // launch L3: "Add credits" → Stripe Checkout. buying while redirecting;
  // buyMsg carries the honest "coming soon" when Stripe isn't configured yet.
  const [buying, setBuying] = useState(false);
  const [buyMsg, setBuyMsg] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const refresh = () =>
      fetchMe()
        .then((m) => alive && setMe(m))
        .catch(() => alive && setMe(null));
    refresh();
    // a run debits (start) and may refund (completion) without a route
    // change — refresh the balance when the run flow signals it
    window.addEventListener(CREDITS_CHANGED, refresh);
    return () => {
      alive = false;
      window.removeEventListener(CREDITS_CHANGED, refresh);
    };
  }, [pathname]);

  // returning from a completed Checkout — the webhook grants credits async,
  // so nudge the balance a few times until the purchase lands
  useEffect(() => {
    if (!/[?&]purchase=success/.test(window.location.search)) return;
    const timers = [1500, 4000, 8000].map((ms) =>
      window.setTimeout(notifyCreditsChanged, ms),
    );
    return () => timers.forEach(clearTimeout);
  }, []);

  const buyCredits = () => {
    setBuying(true);
    setBuyMsg(null);
    startCheckout()
      .then(({ url }) => location.assign(url)) // hand off to Stripe's hosted page
      .catch((e) => {
        setBuying(false);
        // 503 before the owner wires Stripe keys → the honest backend message
        setBuyMsg(
          e instanceof ApiError && e.status === 503
            ? e.detail
            : "couldn't start checkout, try again",
        );
      });
  };

  if (me === undefined) return null;

  if (me === null) {
    return (
      <Link
        href="/signin"
        title="Sign in"
        className={clsx(
          "flex h-[38px] items-center rounded-[10px] text-ink-3 hover:bg-raised-2 hover:text-ink",
          open ? "w-full gap-3 px-2.5" : "w-[38px] justify-center",
        )}
      >
        <PersonIcon />
        {open && <span className="truncate text-[13px] font-semibold">Sign in</span>}
      </Link>
    );
  }

  const signOut = () => {
    setSigningOut(true);
    // even if revocation errors (backend blip), leave the app — the session
    // cookie is httpOnly and the next /api/me decides the truth
    logout()
      .catch(() => undefined)
      .finally(() => location.assign("/"));
  };

  if (!open) {
    return (
      <div className="flex flex-col items-center gap-1.5">
        <div
          title={me.email}
          className="flex h-[38px] w-[38px] items-center justify-center text-ink-3"
        >
          <PersonIcon />
        </div>
        {/* the sign-out icon stays reachable even when the rail is collapsed */}
        <button
          onClick={signOut}
          disabled={signingOut}
          title="Sign out"
          aria-label="Sign out"
          className="flex h-[38px] w-[38px] items-center justify-center rounded-[10px] text-ink-4 hover:bg-raised-2 hover:text-ink disabled:opacity-60"
        >
          <SignOutIcon />
        </button>
      </div>
    );
  }

  return (
    <div className="flex w-full flex-col gap-[2px]">
      <div className="flex items-center gap-3 px-2.5 py-1.5 text-ink-3">
        <PersonIcon />
        <div className="min-w-0">
          <div className="truncate text-[12.5px] font-semibold text-ink-2" title={me.email}>
            {me.email}
          </div>
          <div className="truncate font-mono text-[11px] font-medium" title="Backtest credits">
            {me.credits} {me.credits === 1 ? "credit" : "credits"}
          </div>
        </div>
      </div>
      <button
        onClick={buyCredits}
        disabled={buying}
        className="flex h-[30px] w-full items-center rounded-[10px] px-2.5 text-[12.5px] font-semibold text-trust hover:bg-trust/10 disabled:opacity-60"
      >
        {buying ? "Opening checkout…" : "Add credits — $10 / 50"}
      </button>
      {buyMsg && (
        <p className="px-2.5 pb-0.5 font-mono text-[10.5px] leading-[1.5] text-ink-4">{buyMsg}</p>
      )}
      {me.admin && (
        <Link
          href="/admin"
          className="flex h-[30px] w-full items-center rounded-[10px] px-2.5 text-[12.5px] font-semibold text-ink-3 hover:bg-raised-2 hover:text-ink"
        >
          Admin
        </Link>
      )}
      <button
        onClick={signOut}
        disabled={signingOut}
        className="flex h-[34px] w-full items-center gap-2.5 rounded-[10px] px-2.5 text-[12.5px] font-semibold text-ink-4 hover:bg-raised-2 hover:text-ink disabled:opacity-60"
      >
        <SignOutIcon />
        {signingOut ? "Signing out…" : "Sign out"}
      </button>
    </div>
  );
}
