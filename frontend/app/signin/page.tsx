import type { Metadata } from "next";

import { SubpageShell } from "@/components/landing/subpage-shell";

import { SigninForm } from "./signin-form";

export const metadata: Metadata = {
  title: "Sign in",
  alternates: { canonical: "/signin" },
};

/** Launch L1b: real sign-in (self-rolled accounts). Metadata lives here;
 * the form is client. */
export default function SigninPage() {
  return (
    <SubpageShell kicker="ACCOUNTS" title="Sign in.">
      <SigninForm />
    </SubpageShell>
  );
}
