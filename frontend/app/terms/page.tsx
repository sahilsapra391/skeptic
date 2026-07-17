import type { Metadata } from "next";

import { SubpageShell } from "@/components/landing/subpage-shell";

export const metadata: Metadata = { title: "Terms of Service — Skeptic" };

/** Placeholder — the owner supplies the final text before launch (PRD H:
 * legal pages are launch blockers, not PR blockers). */
export default function TermsPage() {
  return (
    <SubpageShell kicker="LEGAL" title="Terms of Service">
      <p>The full Terms of Service are being finalized and will be published
      here before public launch.</p>
      <p className="mt-4">
        Until then, the standing terms in short: Skeptic is a research and
        education tool, not financial advice. Backtests run on approximate,
        self-collected market data and overstate live results. Nothing on
        this site is a recommendation to buy or sell any security.
      </p>
    </SubpageShell>
  );
}
