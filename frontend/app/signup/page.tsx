import type { Metadata } from "next";

import { SubpageShell } from "@/components/landing/subpage-shell";

import { SignupForm } from "./signup-form";

export const metadata: Metadata = {
  title: "Create an account",
  alternates: { canonical: "/signup" },
};

/** Launch L1b: real signup (self-rolled accounts, owner decision
 * 2026-07-17 reversing D1). Metadata lives here; the form is client. */
export default function SignupPage() {
  return (
    <SubpageShell kicker="ACCOUNTS" title="Create your account.">
      <SignupForm />
    </SubpageShell>
  );
}
