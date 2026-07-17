import Link from "next/link";
import type { Metadata } from "next";

import { SubpageShell } from "@/components/landing/subpage-shell";

export const metadata: Metadata = { title: "Sign in — Skeptic" };

/** Honest placeholder until the in-house accounts chunk lands. */
export default function SigninPage() {
  return (
    <SubpageShell kicker="ACCOUNTS" title="Sign-in opens with accounts.">
      <p>
        Accounts (email + password) arrive with the next update. Until then
        the composer is open — your first backtest is free, no account, no
        card.
      </p>
      <Link
        href="/"
        className="mt-6 inline-block rounded-[10px] bg-trust px-5 py-2.5 text-[14px] font-bold text-on-accent"
      >
        Back to the composer →
      </Link>
    </SubpageShell>
  );
}
