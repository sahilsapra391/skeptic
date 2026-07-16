"use client";

/**
 * Launch L1: the nav-rail account slot. Renders nothing until the Clerk
 * publishable key exists (build-time inlined), so the pre-launch app is
 * pixel-identical. Signed out → modal sign-in; signed in → Clerk user
 * button + the credit balance (data → Plex Mono, per the typography rule).
 */

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { SignInButton, UserButton, useUser } from "@clerk/nextjs";
import clsx from "clsx";

import { fetchMe, type MePayload } from "@/lib/api";

const CLERK_ENABLED = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

export function AccountSection({ open }: { open: boolean }) {
  if (!CLERK_ENABLED) return null;
  return <AccountSectionInner open={open} />;
}

// hooks live below the enabled-check so useUser never runs outside
// ClerkProvider (the provider is only mounted when the key exists)
function AccountSectionInner({ open }: { open: boolean }) {
  const { isSignedIn } = useUser();
  const pathname = usePathname();
  const [me, setMe] = useState<MePayload | null>(null);

  // refresh on navigation too: once runs debit credits (L2), the balance
  // must not go stale after a submit
  useEffect(() => {
    if (!isSignedIn) {
      setMe(null);
      return;
    }
    let alive = true;
    fetchMe()
      .then((m) => alive && setMe(m))
      .catch(() => alive && setMe(null));
    return () => {
      alive = false;
    };
  }, [isSignedIn, pathname]);

  if (!isSignedIn) {
    return (
      <SignInButton mode="modal">
        <button
          title="Sign in"
          className={clsx(
            "flex h-[38px] items-center rounded-[10px] text-ink-3 hover:bg-raised-2 hover:text-ink",
            open ? "w-full gap-3 px-2.5" : "w-[38px] justify-center",
          )}
        >
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
          {open && <span className="truncate text-[13px] font-semibold">Sign in</span>}
        </button>
      </SignInButton>
    );
  }

  return (
    <div
      className={clsx(
        "flex h-[38px] items-center",
        open ? "w-full gap-2.5 px-2.5" : "w-[38px] justify-center",
      )}
    >
      <UserButton />
      {open && me && (
        <span
          title="Backtest credits"
          className="truncate font-mono text-[11.5px] font-medium text-ink-3"
        >
          {me.credits} {me.credits === 1 ? "credit" : "credits"}
        </span>
      )}
    </div>
  );
}
