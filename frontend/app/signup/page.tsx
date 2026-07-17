import Link from "next/link";
import type { Metadata } from "next";

import { SubpageShell } from "@/components/landing/subpage-shell";

export const metadata: Metadata = { title: "Create an account — Skeptic" };

/**
 * Honest placeholder until the in-house accounts chunk lands (email +
 * password + verification, self-rolled — owner decision 2026-07-17).
 * No fake form: a field that collects nothing would be theater.
 */
export default function SignupPage() {
  return (
    <SubpageShell kicker="ACCOUNTS" title="Accounts are almost here.">
      <p>
        An account gets you 5 free backtests, keeps your runs in a library,
        and carries your anonymous trial run with you. Sign-up opens with the
        next update — email and password, nothing else.
      </p>
      <p className="mt-4">
        Your first backtest doesn&apos;t need one:
      </p>
      <Link
        href="/"
        className="mt-6 inline-block rounded-[10px] bg-trust px-5 py-2.5 text-[14px] font-bold text-on-accent"
      >
        Run your free backtest →
      </Link>
      <p className="mt-10 font-mono text-[11px] text-ink-4">
        Research tool, not financial advice.
      </p>
    </SubpageShell>
  );
}
