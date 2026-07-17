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

import { fetchMe, logout, type MePayload } from "@/lib/api";

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

export function AccountSection({ open }: { open: boolean }) {
  const pathname = usePathname();
  // undefined = first check still in flight (render nothing rather than
  // flash "Sign in" at a signed-in user); null = signed out
  const [me, setMe] = useState<MePayload | null | undefined>(undefined);
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    let alive = true;
    fetchMe()
      .then((m) => alive && setMe(m))
      .catch(() => alive && setMe(null));
    return () => {
      alive = false;
    };
  }, [pathname]);

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
      <div
        title={me.email}
        className="flex h-[38px] w-[38px] items-center justify-center text-ink-3"
      >
        <PersonIcon />
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
        onClick={signOut}
        disabled={signingOut}
        className="flex h-[30px] w-full items-center rounded-[10px] px-2.5 text-[12.5px] font-semibold text-ink-4 hover:bg-raised-2 hover:text-ink disabled:opacity-60"
      >
        {signingOut ? "Signing out…" : "Sign out"}
      </button>
    </div>
  );
}
