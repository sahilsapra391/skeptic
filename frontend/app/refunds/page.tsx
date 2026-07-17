import type { Metadata } from "next";

import { SubpageShell } from "@/components/landing/subpage-shell";

export const metadata: Metadata = { title: "Refund Policy", alternates: { canonical: "/refunds" } };

/** Placeholder — the owner supplies the final text before launch (PRD H).
 * Credit semantics reflect the owner's Jul 16 override: refusals refund. */
export default function RefundsPage() {
  return (
    <SubpageShell kicker="LEGAL" title="Refund Policy">
      <p>The full Refund Policy is being finalized and will be published
      here before public launch.</p>
      <p className="mt-4">
        The short version: a credit is spent only on a graded verdict. If a
        run fails on our end, the credit comes back automatically. If the
        verdict is &ldquo;not enough evidence,&rdquo; the credit comes back
        too. The $10 credit pack itself is a one-time purchase.
      </p>
    </SubpageShell>
  );
}
