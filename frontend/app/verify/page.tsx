import type { Metadata } from "next";

import { SubpageShell } from "@/components/landing/subpage-shell";

import { VerifyClient } from "./verify-client";

// noindex: a token-bearing utility page. Nothing to rank, and crawled
// tokens would just burn as "used or expired"
export const metadata: Metadata = {
  title: "Verify email",
  alternates: { canonical: "/verify" },
  robots: { index: false, follow: false },
};

export default function VerifyPage() {
  return (
    <SubpageShell kicker="ACCOUNTS" title="Email verification.">
      <VerifyClient />
    </SubpageShell>
  );
}
